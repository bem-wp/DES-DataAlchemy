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


def _safe_float(parts: list, idx: int) -> Optional[float]:
    """安全地将列表元素转换为浮点数，处理空值、'null' 等异常情况。"""
    if idx >= len(parts):
        return None
    val = parts[idx]
    if val is None or (isinstance(val, str) and (not val.strip() or val.strip().lower() == 'null')):
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _split_value_unit(pair) -> tuple[Optional[float], str]:
    """将 [数值, 单位] 二元列表拆为 (float|None, str)。"""
    if not isinstance(pair, list) or len(pair) == 0:
        return None, ''
    value = _safe_float(pair, 0)
    unit = pair[1] if len(pair) > 1 and pair[1] else ''
    return value, unit


def parse_molar_ratio(ratio_str) -> list[Optional[float]]:
    """将冒号字符串摩尔比（如 '2:1'）解析为数值列表。"""
    if not ratio_str:
        return []
    parts = ratio_str.split(':')
    return [_safe_float(parts, i) for i in range(len(parts))]


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
    """按目标宽度展开 c1..cN / a1..aN / x1..xN 列（宽度不足补空）。"""
    names = entry.get('compounds_names', [])
    acronyms = entry.get('compounds_acronyms', [])
    ratio_parts = parse_molar_ratio(entry.get('molar_ratio', ''))

    row = {'article_title': article_title}
    for i in range(width):
        row[f'c{i + 1}'] = names[i] if i < len(names) else ''
    for i in range(width):
        row[f'a{i + 1}'] = acronyms[i] if i < len(acronyms) else ''
    for i in range(width):
        row[f'x{i + 1}'] = ratio_parts[i] if i < len(ratio_parts) else None
    return row


def build_row(entry, article_title, property_name, origin):
    """
    单条 des_entry → 行数据，返回 (row, status)。
    status: 'kept' 有效 / 'over3' >3组分（分流，不丢数据）
            'ratio_mismatch' 组分与摩尔比数量不匹配 / 'incomplete' 温度或物性缺失
    """
    names = entry.get('compounds_names', [])
    ratio_parts = parse_molar_ratio(entry.get('molar_ratio', ''))
    temp_value, temp_unit = _split_value_unit(entry.get('temperature', []))
    prop_value, prop_unit = _split_value_unit(entry.get(property_name, []))

    # 超过 3 组分的条目本脚本无法放入固定 c1..c3 列，
    # 单独收集并另存为一个 xlsx 文件，避免丢数据
    if len(names) > 3 or len(ratio_parts) > 3:
        row = {
            'article_title': article_title,
            'compounds_names': names,
            'compounds_acronyms': entry.get('compounds_acronyms', []),
            'molar_ratio': entry.get('molar_ratio', ''),
            'temp': temp_value, 'temp_unit': temp_unit,
            property_name: prop_value,
            f'{property_name}_unit': prop_unit,
            'origin': origin,
        }
        return row, 'over3'

    row = _entry_to_columns(entry, article_title, property_name, 3)
    row['temp'] = temp_value
    row['temp_unit'] = temp_unit
    row[property_name] = prop_value
    row[f'{property_name}_unit'] = prop_unit
    row['origin'] = origin

    # 数据清洗：组分非空数量与摩尔比非空数量必须匹配
    count_c = sum(1 for i in range(3) if row[f'c{i + 1}'] and row[f'c{i + 1}'].strip())
    count_x = sum(1 for i in range(3) if row[f'x{i + 1}'] is not None)
    if count_c != count_x:
        return row, 'ratio_mismatch'

    # 温度和物性字段均不能缺失
    if temp_value is None or not temp_unit.strip() or prop_value is None or not prop_unit.strip():
        return row, 'incomplete'

    return row, 'kept'


def build_over3_dataframe(over3_rows, property_name) -> pd.DataFrame:
    """将 >3 组分条目按最大组分宽度展开为 c1..cN / a1..aN / x1..xN 列。"""
    max_c = max(max(len(r['compounds_names']), len(r['compounds_acronyms']),
                    len(r['molar_ratio'].split(':')) if r['molar_ratio'] else 0)
                for r in over3_rows)
    expanded = []
    for r in over3_rows:
        row = {'article_title': r['article_title']}
        names, acronyms, ratio_parts = r['compounds_names'], r['compounds_acronyms'], parse_molar_ratio(r['molar_ratio'])
        for i in range(max_c):
            row[f'c{i + 1}'] = names[i] if i < len(names) else ''
        for i in range(max_c):
            row[f'a{i + 1}'] = acronyms[i] if i < len(acronyms) else ''
        for i in range(max_c):
            row[f'x{i + 1}'] = ratio_parts[i] if i < len(ratio_parts) else None
        row['temp'] = r['temp']
        row['temp_unit'] = r['temp_unit']
        row[property_name] = r[property_name]
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

    property_name 必须与 JSON 中 des_entries 内的物性键名完全一致（如 'conductivity'），
    请确保调用时传入的字段名与原始 JSON 文件同步。
    """
    property_name = cfg.property_name
    input_folder = cfg.json_folder
    output_dir = cfg.table_folder

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
                row, status = build_row(entry, article_title, property_name, filename)
                if status == 'kept':
                    file_kept += 1
                    all_data.append(row)
                elif status == 'over3':
                    skipped_too_many_components += 1
                    logger.warning(
                        f"收集 >3 组分条目 (文件:{filename}, 组分:{len(entry.get('compounds_names', []))}, "
                        f"摩尔比段:{len(parse_molar_ratio(entry.get('molar_ratio', '')))})"
                    )
                    over3_data.append(row)
                elif status == 'ratio_mismatch':
                    skipped_ratio_mismatch += 1
                    logger.debug(f"数据清洗：跳过不匹配数据 (文件:{filename})")
                else:
                    skipped_incomplete += 1
                    logger.debug(f"数据清洗：跳过温度或{property_name}缺失数据 (文件:{filename})")

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
            f"跳过 不匹配 {skipped_ratio_mismatch} / 温度或物性缺失 {skipped_incomplete} / 组分>3单独保存 {skipped_too_many_components}。"
            f"若条目大量存在但全被跳过，请检查 config.yaml 的 targe_property 是否与 JSON 中 des_entries 的物性键一致"
        )

    total_skipped = skipped_ratio_mismatch + skipped_incomplete + skipped_too_many_components

    # 将超过 3 组分的条目按组分最大数展开为 c1..cN / a1..aN / x1..xN 列，另存一个 xlsx
    if over3_data:
        df_over3 = build_over3_dataframe(over3_data, property_name)
        over3_file = os.path.join(output_dir, f"{property_name}_over3components.xlsx")
        save_dataframe(df_over3, over3_file)
        logger.info(f">3组分数据已单独保存至 {over3_file}，共 {len(df_over3)} 条记录")

    logger.info(
        f"汇总: 文件 {files_processed} | 总条目 {total_entries} → 有效 {len(df)}，"
        f"跳过 {total_skipped}（不匹配 {skipped_ratio_mismatch} / 缺失 {skipped_incomplete} / >3组分单独保存 {skipped_too_many_components}），"
        f"文件错误 {file_errors}"
    )

    return df
