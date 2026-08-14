# 单位归一化清洗管线：读取 09 输出的 xlsx，清洗单位后另存 *_cleaned.xlsx
import os
import math
import logging
from decimal import Decimal, ROUND_HALF_UP

import pandas as pd

from app_configs import TableCleanConfig

logger = logging.getLogger(__name__)


# ==================== 数据清洗（纯函数） ====================

def _round_half_up(value, ndigits: int = 2) -> float:
    """ROUND_HALF_UP 四舍五入（避免 Python 银行家舍入的边界坑）。"""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return value
    quant = Decimal('1.' + '0' * ndigits)
    return float(Decimal(str(value)).quantize(quant, rounding=ROUND_HALF_UP))


_SUPERSCRIPT_MAP = {'⁻': '-', '¹': '1', '²': '2'}


def _canon_unit(unit: str) -> str:
    """单位规范化键：小写、·→.、去首尾空格、去内部空格、Unicode 上标替换。

    与 configs/unit_mappings.yaml 的规范化键规则保持一致。
    """
    key = unit.strip().lower().replace('·', '.')
    for src, dst in _SUPERSCRIPT_MAP.items():
        key = key.replace(src, dst)
    return key.replace(' ', '')


def normalize_temp(value, unit) -> tuple:
    """
    归一化单个温度 (value, unit)。
    °C / Celsius（忽略大小写）→ 数值 +273.15（round 2 位），标签置 K；
    Kelvin（本身就是开尔文）→ 数值与标签均原样保留；
    未知单位 → warning + 原样保留。
    """
    if not isinstance(unit, str):
        return value, unit
    key = unit.strip().lower()
    if key in ('°c', 'celsius'):
        return _round_half_up(value + 273.15, 2), 'K'
    if key in ('kelvin', 'k'):
        return value, unit
    logger.warning(f"未识别的温度单位: {unit!r}，原样保留")
    return value, unit


def normalize_viscosity_unit(value, unit, mappings: dict) -> tuple:
    """
    归一化单个粘度 (value, unit)。

    规则来源为 configs/unit_mappings.yaml（配置层加载后经 mappings 传入）：
    规范化键命中 → 数值 ×factor，标签置目标单位；
    kind=kinematic（运动粘度）→ 数值不变，仅归一标签 + 告警；
    kind=assumed 或 warn_on_raw 命中（疑似 OCR/笔误）→ 数值不变，归一标签 + 告警；
    未命中 → warning + 原样保留。
    """
    if not isinstance(unit, str):
        return value, unit
    raw = unit.strip()
    entry = mappings.get(_canon_unit(raw))
    if entry is None:
        logger.warning(f"未识别的粘度单位: {unit!r}，原样保留")
        return value, unit
    target = entry['unit']
    if entry.get('kind') == 'kinematic':
        logger.warning(f"运动粘度单位 {unit!r} 缺少密度无法换算为 mPa·s，仅归一标签为 {target!r}")
    elif entry.get('kind') == 'assumed':
        logger.warning(f"粘度单位 {unit!r} 疑似 OCR/笔误，按 {target!r} 处理")
    warn_on_raw = entry.get('warn_on_raw')
    if warn_on_raw is not None and warn_on_raw.match(raw):
        logger.warning(f"粘度单位 {unit!r} 疑似 OCR 错误（应为 mPa s），按 {target!r} 处理")
    return value * entry['factor'], target


def clean_table(df: pd.DataFrame, property_name: str, mappings: dict) -> pd.DataFrame:
    """对整表执行单位归一化，返回新 DataFrame（不修改入参）。"""
    out = df.copy()
    if {'temp', 'temp_unit'}.issubset(out.columns):
        out[['temp', 'temp_unit']] = out.apply(
            lambda r: normalize_temp(r['temp'], r['temp_unit']),
            axis=1, result_type='expand')
    unit_col = f'{property_name}_unit'
    if unit_col in out.columns:
        out[[property_name, unit_col]] = out.apply(
            lambda r: normalize_viscosity_unit(r[property_name], r[unit_col], mappings),
            axis=1, result_type='expand')
    return out


# ==================== 清洗器类（IO + 主流程，不复用 09 模块） ====================

class TableCleaner:
    """读取 09 输出的 xlsx → 单位归一化 → 另存 *_cleaned.xlsx。

    自带写盘兜底链（openpyxl → xlsxwriter → CSV，编码 utf-8-sig），
    不依赖 json_to_table_helper.save_dataframe。
    """

    CLEANED_SUFFIX = '_cleaned'

    def __init__(self, config: TableCleanConfig):
        self.property_name = config.property_name
        self.input_folder = config.input_folder
        self.output_folder = config.output_folder
        self.unit_mappings = config.unit_mappings
        self._input_names = (f"{config.property_name}_data.xlsx",
                             f"{config.property_name}_over3components.xlsx")

    # ---- IO ----

    def load_table(self, filename: str):
        """读取 xlsx 为 DataFrame；文件不存在返回 None（容错跳过）。"""
        path = os.path.join(self.input_folder, filename)
        if not os.path.exists(path):
            return None
        return pd.read_excel(path)

    def save_dataframe(self, df: pd.DataFrame, path: str) -> str:
        """openpyxl → xlsxwriter → CSV（utf-8-sig）三级兜底，返回实际写入的后缀。"""
        try:
            df.to_excel(path, index=False)
            return '.xlsx'
        except Exception as e:
            logger.warning(f"使用openpyxl保存失败: {e}，尝试xlsxwriter引擎")
        try:
            df.to_excel(path, index=False, engine='xlsxwriter')
            return '.xlsx'
        except Exception as e2:
            logger.warning(f"所有Excel引擎均失败: {e2}，保存为CSV")
            csv_file = path.replace('.xlsx', '.csv')
            df.to_csv(csv_file, index=False, encoding='utf-8-sig')
            return '.csv'

    # ---- 主流程 ----

    def run(self) -> list:
        """清洗全部输入表，返回 [(输入文件名, 清洗后行数, 输出路径), ...]。"""
        os.makedirs(self.output_folder, exist_ok=True)
        results = []
        for src_name in self._input_names:
            df = self.load_table(src_name)
            if df is None:
                logger.warning(f"输入表不存在，跳过: {os.path.join(self.input_folder, src_name)}")
                continue
            df_clean = clean_table(df, self.property_name, self.unit_mappings)
            dst_name = src_name.replace('.xlsx', f'{self.CLEANED_SUFFIX}.xlsx')
            dst_path = os.path.join(self.output_folder, dst_name)
            suffix = self.save_dataframe(df_clean, dst_path)
            logger.info(f"清洗完成: {dst_path}（{suffix}），{len(df_clean)} 行（输入 {len(df)} 行）")
            results.append((src_name, len(df_clean), dst_path))

        if not results:
            logger.error(f"未找到任何输入表（{' / '.join(self._input_names)}），请先运行 09 号脚本")
        return results
