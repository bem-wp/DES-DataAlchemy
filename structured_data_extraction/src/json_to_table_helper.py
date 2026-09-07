# d:\envs\codes\DES-DataAlchemy\structured_data_extraction\src\07_extract_json_to_table.py
import os
import json
import re
import logging
import pandas as pd
from typing import Optional
from dataclasses import dataclass
from app_configs import JsontotableConfig

logger = logging.getLogger(__name__)


# ==================== 数据清洗（纯函数） ====================

def clean_illegal_chars(value):
    """清理Excel不支持的非法字符"""
    if isinstance(value, str):
        # 保留常用的控制字符（换行、回车、制表符），移除其他控制字符
        # 同时清理零宽空格等特殊Unicode字符
        cleaned = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F\u200B-\u200F\u2028-\u202F\uFEFF]', '', value)
        # 去除首尾空白（可选）
        cleaned = cleaned.strip()
        return cleaned
    return value


def sanitize_dataframe(df):
    """清理DataFrame中所有字符串列的非法字符"""
    # 对object类型（字符串）列进行清洗
    for col in df.select_dtypes(include=['object']).columns:
        df[col] = df[col].apply(clean_illegal_chars)
    return df


def _safe_number(value) -> Optional[float]:
    """安全地将标量转换为浮点数，处理 None、空串、'null' 等异常情况。

    适配新结构：组分摩尔比 components[].composition 与物性值均为纯标量（无单位）。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip()
        if not s or s.lower() == 'null':
            return None
        try:
            return float(s)
        except (ValueError, TypeError):
            return None
    return None


def _get_components(entry) -> list:
    """从条目取出 components 列表（每项含 name / acronym / composition）。"""
    components = entry.get('components')
    return components if isinstance(components, list) else []


_NULL_TEXT = {'', 'null', 'none'}


def _clean_text(value) -> str:
    """将 None 及字面量占位符 'null'/'none' 归一为空串。

    与数值路径 _safe_number 对 'null' 的处理保持一致（提取器常把“无值”写成字符串 'null'）。
    """
    if value is None:
        return ''
    s = str(value)
    return '' if s.strip().lower() in _NULL_TEXT else s


def _component_field(comp, key: str):
    """安全读取单个组分的字段：name/acronym 归一为字符串，composition 转数值。"""
    if not isinstance(comp, dict):
        return None if key == 'composition' else ''
    val = comp.get(key)
    if key == 'composition':
        return _safe_number(val)
    return _clean_text(val)


def _split_value_unit(raw) -> tuple[Optional[float], str]:
    """把「物性 / 条件变量」原始值拆为 (数值, 单位)，自动判别形态（方案C）：
      - 列表 [值, 单位]         → (float, 单位)
      - 对象 {value, unit}      → (float, 单位)；仅有 value（如 metadata 的 {references,value}）→ 单位空
      - 标量 298.15 / "298.15"  → (float, '')
    单位为空串表示「无单位」，调用方据此决定是否落 <字段>_unit 列（标量只出值列）。
    """
    if isinstance(raw, list):
        value = _safe_number(raw[0]) if raw else None
        unit = raw[1] if len(raw) > 1 else ''
        return value, _clean_text(unit)
    if isinstance(raw, dict):
        return _safe_number(raw.get('value')), _clean_text(raw.get('unit'))
    return _safe_number(raw), ''


def _merge_row_fields(row, entry, property_name, condition_vars):
    """把条件变量与目标物性写入行：值必落列，单位仅在解析出非空单位时落 <字段>_unit 列。

    返回目标物性的数值，供上层做「物性缺失」清洗判定。
    """
    for cond in condition_vars:
        value, unit = _split_value_unit(entry.get(cond))
        row[cond] = value
        if unit:
            row[f'{cond}_unit'] = unit
    prop_value, prop_unit = _split_value_unit(entry.get(property_name))
    row[property_name] = prop_value
    if prop_unit:
        row[f'{property_name}_unit'] = prop_unit
    return prop_value


# ==================== 管线第 1 步：加载文件 ====================

def load_json_files(folder):
    """遍历文件夹，产出 (文件名, JSON数据)。单文件读取失败由调用方捕获。"""
    for filename in os.listdir(folder):
        if not filename.endswith('.json'):
            continue
        file_path = os.path.join(folder, filename)
        with open(file_path, 'r', encoding='utf-8') as f:
            yield filename, json.load(f)


# ==================== 管线第 2 步：解析条目 ====================

def parse_entries(data) -> tuple[str, list]:
    """从 JSON 顶层提取 article_title 与 des_entries。"""
    extraction = data.get('extraction', {})
    article_title = clean_illegal_chars(extraction.get('article_title', ''))
    return article_title, extraction.get('des_entries', [])


# ==================== 管线第 3 步：行构建 ====================

def _entry_to_columns(entry, article_title, property_name, width):
    """按目标宽度展开 c1..cN / a1..aN / x1..xN 列（宽度不足补空）。

    组分来自 entry['components']：name→c、acronym→a、composition→x（摩尔比）。
    """
    components = _get_components(entry)
    row = {'article_title': article_title}
    for i in range(width):
        comp = components[i] if i < len(components) else {}
        row[f'c{i + 1}'] = _component_field(comp, 'name')
    for i in range(width):
        comp = components[i] if i < len(components) else {}
        row[f'a{i + 1}'] = _component_field(comp, 'acronym')
    for i in range(width):
        comp = components[i] if i < len(components) else {}
        row[f'x{i + 1}'] = _component_field(comp, 'composition')
    return row


def build_row(entry, article_title, property_name, origin, condition_vars=None):
    """
    单条 des_entry → 行数据，返回 (row, status)。
    condition_vars: 环境条件字段名列表（如 ['temperature']）；每个条件按方案C自动判别——
                    标量只出值列，[值,单位]/{value,unit} 则额外出 <字段>_unit 列。
    status: 'kept' 有效 / 'over3' >3组分（分流，不丢数据）
            'ratio_mismatch' 组分名数量与有效摩尔比数量不匹配 / 'incomplete' 目标物性或条件变量缺失
    """
    condition_vars = condition_vars or []
    components = _get_components(entry)

    # 超过 3 组分的条目本脚本无法放入固定 c1..c3 列，
    # 单独收集并另存为一个 xlsx 文件，避免丢数据
    if len(components) > 3:
        row = {'article_title': article_title, 'components': components, 'origin': origin}
        _merge_row_fields(row, entry, property_name, condition_vars)
        return row, 'over3'

    row = _entry_to_columns(entry, article_title, property_name, 3)
    prop_value = _merge_row_fields(row, entry, property_name, condition_vars)
    row['origin'] = origin

    # 数据清洗：组分名非空数量与有效摩尔比(composition)数量必须匹配
    count_c = sum(1 for i in range(3) if row[f'c{i + 1}'] and str(row[f'c{i + 1}']).strip())
    count_x = sum(1 for i in range(3) if row[f'x{i + 1}'] is not None)
    if count_c != count_x:
        return row, 'ratio_mismatch'

    # 目标物性不能缺失；若配置了条件变量，其数值同样不能缺失（保留旧版“条件必检”清洗逻辑）
    if prop_value is None:
        return row, 'incomplete'
    for cond in condition_vars:
        if row.get(cond) is None:
            return row, 'incomplete'

    return row, 'kept'


def build_over3_dataframe(over3_rows, property_name, condition_vars=None) -> pd.DataFrame:
    """将 >3 组分条目按最大组分宽度展开为 c1..cN / a1..aN / x1..xN 列。"""
    condition_vars = condition_vars or []
    max_c = max(len(r['components']) for r in over3_rows)
    expanded = []
    for r in over3_rows:
        components = r['components']
        row = {'article_title': r['article_title']}
        for i in range(max_c):
            comp = components[i] if i < len(components) else {}
            row[f'c{i + 1}'] = _component_field(comp, 'name')
        for i in range(max_c):
            comp = components[i] if i < len(components) else {}
            row[f'a{i + 1}'] = _component_field(comp, 'acronym')
        for i in range(max_c):
            comp = components[i] if i < len(components) else {}
            row[f'x{i + 1}'] = _component_field(comp, 'composition')
        # 条件变量列：值必带；单位列按原始行是否解析出单位透传
        for cond in condition_vars:
            row[cond] = r.get(cond)
            if f'{cond}_unit' in r:
                row[f'{cond}_unit'] = r[f'{cond}_unit']
        row[property_name] = r[property_name]
        if f'{property_name}_unit' in r:
            row[f'{property_name}_unit'] = r[f'{property_name}_unit']
        row['origin'] = r['origin']
        expanded.append(row)
    return sanitize_dataframe(pd.DataFrame(expanded))


# ==================== 管线第 4 步：写盘（引擎兜底） ====================

def save_dataframe(df: pd.DataFrame, path: str) -> str:
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


# ==================== 主流程 ====================

def extract_json_to_excel(cfg: JsontotableConfig) -> pd.DataFrame:
    """
    从指定文件夹读取所有 JSON 文件，提取 DES 配方数据并保存为 Excel。

    property_name 必须与 JSON 中 des_entries 内的物性键名完全一致（如 'alpha' / 'beta' / 'pi_star'），
    请确保调用时传入的字段名与原始 JSON 文件同步。

    condition_vars（来自 cfg）为环境条件字段名列表：每个条件按方案C自动判别——
    标量只出 <字段> 值列，[值,单位] 或 {value,unit} 则额外出 <字段>_unit 列，
    供 10 号表清洗按 condition_mappings.yaml 归一化（无单位列时自动跳过）。
    """
    property_name = cfg.property_name
    input_folder = cfg.json_folder
    output_dir = cfg.table_folder
    condition_vars = list(getattr(cfg, 'condition_vars', []) or [])

    # 确保输出目录存在，并构建完整的输出文件路径
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, f"{property_name}_data.xlsx")

    all_data = []
    over3_data = []
    files_processed = 0
    total_entries = 0
    skipped_ratio_mismatch = 0
    skipped_incomplete = 0
    skipped_too_many_components = 0
    file_errors = 0

    for filename, data in load_json_files(input_folder):
        files_processed += 1
        file_entries = 0
        file_kept = 0
        try:
            article_title, des_entries = parse_entries(data)
            for entry in des_entries:
                file_entries += 1
                row, status = build_row(entry, article_title, property_name, filename, condition_vars)
                if status == 'kept':
                    file_kept += 1
                    all_data.append(row)
                elif status == 'over3':
                    skipped_too_many_components += 1
                    logger.warning(
                        f"收集 >3 组分条目 (文件:{filename}, 组分数:{len(_get_components(entry))})"
                    )
                    over3_data.append(row)
                elif status == 'ratio_mismatch':
                    skipped_ratio_mismatch += 1
                    logger.debug(f"数据清洗：跳过不匹配数据 (文件:{filename})")
                else:
                    skipped_incomplete += 1
                    logger.debug(f"数据清洗：跳过{property_name}或条件变量缺失数据 (文件:{filename})")

            total_entries += file_entries
            logger.info(f"[{filename}] 条目 {file_entries} → 有效 {file_kept}")
        except Exception:
            file_errors += 1
            logger.exception(f"处理文件 {filename} 时出错")

    # 创建DataFrame
    df = pd.DataFrame(all_data)

    if not df.empty:
        # 【关键修复】在写入Excel前清洗所有字符串列
        df = sanitize_dataframe(df)
        save_dataframe(df, output_file)
        logger.info(f"数据已保存至 {output_file}，共 {len(df)} 条记录")
    else:
        logger.warning(
            f"未提取到任何有效数据：文件错误 {file_errors}，"
            f"跳过 不匹配 {skipped_ratio_mismatch} / 物性缺失 {skipped_incomplete} / 组分>3单独保存 {skipped_too_many_components}。"
            f"若条目大量存在但全被跳过，请检查 config.yaml 的 targe_property 是否与 JSON 中 des_entries 的物性键一致"
        )

    total_skipped = skipped_ratio_mismatch + skipped_incomplete + skipped_too_many_components

    # 将超过 3 组分的条目按组分最大数展开为 c1..cN / a1..aN / x1..xN 列，另存一个 xlsx
    if over3_data:
        df_over3 = build_over3_dataframe(over3_data, property_name, condition_vars)
        over3_file = os.path.join(output_dir, f"{property_name}_over3components.xlsx")
        save_dataframe(df_over3, over3_file)
        logger.info(f">3组分数据已单独保存至 {over3_file}，共 {len(df_over3)} 条记录")

    logger.info(
        f"汇总: 文件 {files_processed} | 总条目 {total_entries} → 有效 {len(df)}，"
        f"跳过 {total_skipped}（不匹配 {skipped_ratio_mismatch} / 缺失 {skipped_incomplete} / >3组分单独保存 {skipped_too_many_components}），"
        f"文件错误 {file_errors}"
    )

    return df
