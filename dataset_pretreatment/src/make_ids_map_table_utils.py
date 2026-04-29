import json
import logging
import os
import re
# import socket
import sys
import time
import urllib.request
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from urllib.error import HTTPError, URLError

import pandas as pd
import pubchempy as pcp
from openpyxl import load_workbook
from openpyxl.styles import Alignment
from tenacity import retry, stop_after_attempt, wait_exponential

# socket.setdefaulttimeout(15)

# ==========================================
# 1. Logging
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)


# ==========================================
# 2. 基础工具
# ==========================================
def validate_cas(cas_str):
    """校验并返回合法的 CAS 号，否则返回 None"""
    if not cas_str:
        return None
    cas_str = str(cas_str).strip()
    if re.match(r'^\d{2,7}-\d{2}-\d$', cas_str):  # CAS 号：2~7-2-1 
        return cas_str
    return None


def extract_cas_from_synonyms(synonyms_list):
    """从同义词列表中提取第一个合法 CAS 号"""
    if not synonyms_list:
        return None
    for synonym in synonyms_list:
        valid_cas = validate_cas(synonym)
        if valid_cas:
            return valid_cas
    return None


# ==========================================
# 3. PubChem 检索引擎
# ==========================================
def _log_after_attempt(retry_state):
    """每次尝试后，若失败则记录异常信息"""
    if retry_state.outcome.failed:
        exc = retry_state.outcome.exception()
        logging.warning(
            f"调用 {retry_state.fn.__name__} 失败 "
            f"(第{retry_state.attempt_number}次): {type(exc).__name__}: {exc}"
        )


def _log_before_sleep(retry_state):
    """重试前的休眠提示"""
    max_attempts = retry_state.retry_object.stop.max_attempt_number
    sleep_time = retry_state.next_action.sleep
    logging.info(
        f"重试 {retry_state.attempt_number}/{max_attempts - 1}，"
        f"等待 {sleep_time:.0f}s..."
    )


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=4),
    after=_log_after_attempt,
    before_sleep=_log_before_sleep,
)
def fetch_pubchem(name, target_fields):
    """
    PubChem 检索引擎。
    uname 字段使用 compound.iupac_name（IUPAC 系统名），字段名保持 'uname'。
    """
    data = {'ismile': None, 'uname': None, 'cas': None}
    
    # 移除多余的 try-except，让 tenacity 全权接管异常
    results = pcp.get_compounds(name, 'name', timeout = 15)
    compound = results[0] if results else None
    
    # 提前阻断：如果没搜到，直接返回全 None 字典，避免后续无意义的判断
    if not compound:
        return data

    if 'ismile' in target_fields:
        data['ismile'] = compound.isomeric_smiles
        
    if 'cas' in target_fields and getattr(compound, 'synonyms', None):
        data['cas'] = extract_cas_from_synonyms(compound.synonyms)
        
    if 'uname' in target_fields:
        data['uname'] = compound.iupac_name
        
    return data


# ==========================================
# 4. 检索调度器
# ==========================================
@lru_cache(maxsize=None)
def f_fetch_compound_info(name):
    """
    PubChem 单引擎检索。

    Returns
    -------
    tuple : (ismile, uname, cas)
    """
    if not isinstance(name, str) or not name.strip():
        return None, None, None

    time.sleep(0.5)

    logging.info(f"--> 正在请求: [{name}] ...")

    info = {'ismile': None, 'uname': None, 'cas': None}

    try:
        new_data = fetch_pubchem(name, list(info.keys()))
        for field in info:
            if new_data and new_data.get(field):
                info[field] = new_data[field]
                logging.info(f"    └─ 成功获取 [{field}]: {new_data[field]}")
    except Exception as e:
        # 当 tenacity 所有重试均失败后，异常会抛到这里，记录最终失败状态
        logging.error(f"  PubChem 在 [{name}] 上彻底失败: {type(e).__name__}: {e}")

    logging.info(
        f"Result '{name}': ismile={info['ismile']}, "
        f"uname={info['uname']}, CAS={info['cas']}"
    )

    return info['ismile'], info['uname'], info['cas']

def data_quality_summary(df: pd.DataFrame, output_path: str):
    """
    生成列缺失和行缺失的汇总报告，输出到同一个Excel文件的两个sheet
    
    Parameters:
    df: DataFrame - 输入数据框
    output_path: str - 输出Excel文件路径
    """
    output_dir = os.path.dirname(output_path)
    if output_dir:  # 确保路径不为空
        os.makedirs(output_dir, exist_ok=True)

    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        # 列缺失汇总
        total_rows = len(df)
        miss_count = df.isnull().sum()
        miss_pct = miss_count / total_rows if total_rows else 0
        dtype = df.dtypes
        col_summary = pd.DataFrame({
            "column": df.columns,
            "dtype": dtype.values,
            "missing_count": miss_count.values,
            "missing_pct": miss_pct.values,
            "total_rows": total_rows
        })
        col_summary = col_summary.sort_values("missing_pct", ascending=False).reset_index(drop=True)
        
        # 行缺失汇总
        total_cols = df.shape[1]
        r_miss = df.isnull().sum(axis=1)
        r_pct = r_miss / total_cols if total_cols else 0
        row_summary = pd.DataFrame({
            "index": df.index,
            "missing_count": r_miss,
            "missing_pct": r_pct,
            "total_cols": total_cols
        })
        row_summary = row_summary.sort_values(["missing_count", "missing_pct"], ascending=False).reset_index(drop=True)
        
        # 写入Excel的不同sheet
        col_summary.to_excel(writer, sheet_name='Column_Missing_Summary', index=False)
        row_summary.to_excel(writer, sheet_name='Row_Missing_Summary', index=False)

def deduplicate_byproduct_table(df, group_cols, output_dir):
    """
    对副产物表格进行去重操作，并将所有重复明细整合到单个具有良好可读性的审查报表中。
    
    Parameters:
    df: DataFrame - 输入数据框
    group_cols: list - 用于判断重复的列名列表
    output_dir: str or Path - 输出目录路径
    
    Returns:
    DataFrame: 去重后的数据框
    """
    # 1. 处理输出目录与基础清理
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    # 增加鲁棒性：判断是否存在 compound 列后再移除
    if 'compound' in df.columns:
        byproduct_table = df.drop(columns=['compound'])
    else:
        byproduct_table = df.copy()
        
    # 添加 Excel 物理行号用于溯源 (Pandas index 从0开始，Excel有表头且从1开始，因此 +2)
    byproduct_table['original_excel_index'] = byproduct_table.index + 2
    
    # 2. 统计并提取重复情况
    is_duplicate = byproduct_table.duplicated(subset=group_cols, keep=False)
    total_duplicates = is_duplicate.sum()
    print(f"涉及重复的总行数: {total_duplicates}")
    
    # 3. 处理重复明细并输出合并报表 (一表搞定留痕)
    duplicates_df = byproduct_table[is_duplicate].copy()
    
    if not duplicates_df.empty:
        # 添加分组编号
        duplicates_df['_duplicate_group'] = duplicates_df.groupby(group_cols, dropna=False).ngroup() + 1
        
        # 排序：按分组编号 -> Excel行号 排序
        sorted_dups = duplicates_df.sort_values(by=['_duplicate_group', 'original_excel_index'])
        
        # 调整列顺序，把核心溯源字段放在最前面
        cols = ['_duplicate_group', 'original_excel_index'] + \
               [c for c in sorted_dups.columns if c not in ['_duplicate_group', 'original_excel_index']]
        sorted_dups = sorted_dups[cols]
        
        detail_path = output_path / 'byproduct_duplicated_details.xlsx'
        sorted_dups.to_excel(detail_path, index=False)
        
        # 使用 openpyxl 合并重复组单元格以提升可读性
        try:
            wb = load_workbook(detail_path)
            ws = wb.active
            col_idx, start_row, max_row = 1, 2, ws.max_row
            prev_val = ws.cell(row=2, column=col_idx).value
            
            # 遍历到 max_row + 1 以确保最后一组也能被合并
            for row in range(3, max_row + 2):
                curr_val = ws.cell(row=row, column=col_idx).value if row <= max_row else None
                if curr_val != prev_val:
                    if row - 1 > start_row:
                        ws.merge_cells(start_row=start_row, start_column=col_idx, end_row=row-1, end_column=col_idx)
                        ws.cell(row=start_row, column=col_idx).alignment = Alignment(horizontal='center', vertical='center')
                    start_row, prev_val = row, curr_val
            wb.save(detail_path)
            print(f"重复明细已整合并保存至: {detail_path} (单元格已合并)")
        except Exception as e:
            print(f"警告: 单元格合并优化失败，但数据已保存。错误信息: {e}")
    else:
        print("未发现重复数据，跳过生成重复明细表。")

    # 4. 执行直接留一去重
    original_rows = len(byproduct_table)
    byproduct_table_deduplicated = byproduct_table.drop_duplicates(subset=group_cols, keep='first')
    
    # 返回前清理掉溯源辅助列
    final_result = byproduct_table_deduplicated.drop(columns=['original_excel_index'])
    cleaned_rows = len(final_result)
    
    print("开始去重，规则：直接留一")
    print(f"原始行数: {original_rows}")
    print(f"去重后行数: {cleaned_rows}")
    
    return final_result
