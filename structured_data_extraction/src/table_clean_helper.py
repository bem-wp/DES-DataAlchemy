# 单位归一化清洗管线：读取 09 输出的 xlsx，清洗单位后另存 *_cleaned.xlsx
import os
import math
import re
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


_SUPERSCRIPT_MAP = {'⁻': '-', '¹': '1', '²': '2', '³': '3', '⁴': '4'}


def _canon_unit(unit: str) -> str:
    """单位规范化键：小写、·→.、去首尾空格、去内部空格、Unicode 上标替换。

    与 configs/unit_mappings.yaml 的规范化键规则保持一致。
    """
    key = unit.strip().lower().replace('·', '.')
    for src, dst in _SUPERSCRIPT_MAP.items():
        key = key.replace(src, dst)
    return key.replace(' ', '')


def normalize_condition(value, unit, cond_name: str, spec: dict) -> tuple:
    """
    归一化单个环境条件（temp / pressure / ...）的 (value, unit)。

    规则来源为 table_clean.condition_mappings_file 指向的映射文件
    （如 configs/condition_mappings.yaml，配置层加载后经 spec 传入）：
    规范化键命中 offset/factor 条目 → 换算后按 round_digits 舍入，标签置 target_unit；
    keep 条目（已是目标单位）→ 数值与原单位标签均原样保留；
    未命中 → warning + 原样保留。
    """
    if not isinstance(unit, str):
        return value, unit
    entry = spec['units'].get(_canon_unit(unit.strip()))
    if entry is None:
        logger.warning(f"未识别的{cond_name}单位: {unit!r}，原样保留")
        return value, unit
    if entry.get('keep'):
        return value, unit
    if 'offset' in entry:
        new_value = value + entry['offset']
    else:
        new_value = value * entry['factor']
    return _round_half_up(new_value, spec['round_digits']), spec['target_unit']


def normalize_property_unit(value, unit, property_name: str, mappings: dict) -> tuple:
    """
    归一化单个属性（如 viscosity / density）的 (value, unit)。

    规则来源为 table_clean.unit_mappings_file 指向的映射文件
    （如 configs/unit_mappings.yaml，按属性名取段，
    配置层加载后经 mappings 传入）：
    规范化键命中 → 数值 ×factor，标签置目标单位；
    kind=kinematic / relative（近亲量，缺换算条件）→ 数值不变，仅归一标签 + 告警；
    kind=assumed（疑似 OCR/笔误）或 warn_on_raw 命中 → 换算 + 告警；
    未命中 → warning + 原样保留。
    """
    if not isinstance(unit, str):
        return value, unit
    raw = unit.strip()
    entry = mappings.get(_canon_unit(raw))
    if entry is None:
        logger.warning(f"未识别的{property_name}单位: {unit!r}，原样保留")
        return value, unit
    target = entry['unit']
    kind = entry.get('kind')
    if kind == 'kinematic':
        logger.warning(f"{property_name}单位 {unit!r} 为运动粘度，缺少密度无法换算，仅归一标签为 {target!r}")
    elif kind == 'relative':
        logger.warning(f"{property_name}单位 {unit!r} 为相对密度/比重类量，无法按系数换算，仅归一标签为 {target!r}")
    elif kind == 'assumed':
        logger.warning(f"{property_name}单位 {unit!r} 疑似 OCR/笔误或制式不明，按 {target!r} 换算")
    warn_on_raw = entry.get('warn_on_raw')
    if warn_on_raw is not None and warn_on_raw.match(raw):
        logger.warning(f"{property_name}单位 {unit!r} 疑似 OCR 错误，按 {target!r} 处理")
    return value * entry['factor'], target


def normalize_composition(df: pd.DataFrame) -> pd.DataFrame:
    """将每条记录的组分摩尔比列（x1..xN）归一化为摩尔分数，使每条记录求和为 1。

    空值/缺失视为 0（覆盖主表中 2 组分条目的 x3 恒空情形）；
    全部为 0 的行（无有效组分）保持原样（全 0），避免除零。
    """
    cols = [c for c in df.columns if re.fullmatch(r'x\d+', c)]
    if not cols:
        return df
    out = df.copy()
    sub = out[cols].apply(pd.to_numeric, errors='coerce').fillna(0.0)
    sums = sub.sum(axis=1)
    mask = sums > 0
    out[cols] = sub.div(sums.where(mask, 1.0), axis=0).where(mask, 0.0)
    return out


def clean_table(df: pd.DataFrame, property_name: str, mappings: dict, condition_specs: dict) -> pd.DataFrame:
    """对整表执行单位归一化，返回新 DataFrame（不修改入参）。

    condition_specs 中每个条件段作用于 <条件名>/<条件名>_unit 列对（列存在才生效）。
    """
    out = df.copy()
    for cond_name, spec in condition_specs.items():
        unit_col = f'{cond_name}_unit'
        if {cond_name, unit_col}.issubset(out.columns):
            out[[cond_name, unit_col]] = out.apply(
                lambda r: normalize_condition(r[cond_name], r[unit_col], cond_name, spec),
                axis=1, result_type='expand')
    unit_col = f'{property_name}_unit'
    if unit_col in out.columns:
        out[[property_name, unit_col]] = out.apply(
            lambda r: normalize_property_unit(r[property_name], r[unit_col], property_name, mappings),
            axis=1, result_type='expand')
    out = normalize_composition(out)
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
        self.condition_specs = config.condition_specs
        self._input_names = (f"{config.property_name}_data.xlsx",
                             f"{config.property_name}_pure_compound.xlsx",
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
            df_clean = clean_table(df, self.property_name, self.unit_mappings, self.condition_specs)
            dst_name = src_name.replace('.xlsx', f'{self.CLEANED_SUFFIX}.xlsx')
            dst_path = os.path.join(self.output_folder, dst_name)
            suffix = self.save_dataframe(df_clean, dst_path)
            logger.info(f"清洗完成: {dst_path}（{suffix}），{len(df_clean)} 行（输入 {len(df)} 行）")
            results.append((src_name, len(df_clean), dst_path))

        if not results:
            logger.error(f"未找到任何输入表（{' / '.join(self._input_names)}），请先运行 09 号脚本")
        return results
