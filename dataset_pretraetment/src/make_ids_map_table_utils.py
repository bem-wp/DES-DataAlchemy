import os
import sys
import time
import re
import io
import socket
import logging
from collections import Counter
from functools import lru_cache

# 数据处理与科学计算
import numpy as np
import pandas as pd

# 化学信息学专业库
import pubchempy as pcp

# 设置全局超时（utils内部生效）
socket.setdefaulttimeout(15)


# --- 2. 重试装饰器 ---
def retry_on_failure(max_retries=3, delay=2, backoff=2):
    """
    重试装饰器
    max_retries: 最大重试次数
    delay: 初始延迟时间(秒)
    backoff: 延迟倍增系数
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            current_delay = delay
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt < max_retries - 1:
                        print(f" [重试 {attempt + 1}/{max_retries - 1}，等待 {current_delay}s]", end="", flush=True)
                        time.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        # 最后一次重试也失败了
                        raise e
            return None
        return wrapper
    return decorator


# --- 3. 分离的 PubChem 请求函数（带重试） ---
@retry_on_failure(max_retries=3, delay=1, backoff=2)
def get_smiles_with_retry(name):
    """获取 SMILES，带重试"""
    result = pcp.get_properties('IsomericSMILES', name, 'name')
    if result and len(result) > 0 and 'SMILES' in result[0]:
        return result[0]['SMILES']
    return None


@retry_on_failure(max_retries=3, delay=1, backoff=2)
def get_synonyms_with_retry(name):
    """获取同义词，带重试"""
    return pcp.get_synonyms(name, 'name')


@retry_on_failure(max_retries=3, delay=1, backoff=2)
def get_compounds_with_retry(name):
    """获取化合物信息，带重试"""
    return pcp.get_compounds(name, 'name')


# --- 4. CAS 号提取函数（双重策略） ---
def extract_cas_from_synonyms(synonyms_list):
    """
    从同义词列表中提取 CAS 号
    synonyms_list: 同义词列表
    返回: CAS 号字符串或 None
    """
    if not synonyms_list:
        return None
    
    for synonym in synonyms_list:
        parts = synonym.split('-')
        if len(parts) == 3 and all(part.isdigit() for part in parts):
            return synonym
    return None


# --- 5. 核心处理函数（整合版本 + 双重 CAS 获取策略） ---
@lru_cache(maxsize=None)
def f_fetch_compound_info(name):
    """
    整合版本：第一段搜索逻辑 + 第二段封装 + 重试机制 + 双重 CAS 策略
    """
    # 基础校验
    if not isinstance(name, str) or not name.strip():
        logging.warning(f"Invalid input: {name}")
        return None, None, None

    # 防止请求过快
    time.sleep(0.5)
    
    # 请求提示
    print(f"--> 正在请求: [{name}] ...", end="", flush=True)

    smiles = None
    univers_name = None
    cas_number = None

    # --- Part 1: 获取 SMILES ---
    try:
        smiles = get_smiles_with_retry(name)
        if not smiles:
            logging.warning(f"No 'SMILES' found for {name}")
    except Exception as e:
        logging.error(f"Error getting SMILES for '{name}' after retries: {e}")

    # --- Part 2: 获取通用名 + CAS (策略1: 从 synonyms) ---
    try:
        synonyms = get_synonyms_with_retry(name)
        if synonyms:
            synonym_list = synonyms[0]['Synonym']
            
            # 2A. 获取通用名
            new_name = synonym_list[0]
            if re.search(r'\d{2,}', new_name) is not None:
                if len(synonym_list) > 1:
                    univers_name = synonym_list[1]
                else:
                    logging.warning(f"No second synonym available for compound: {name}")
            else:
                univers_name = new_name
            
            # 2B. 尝试从 synonyms 中提取 CAS (优先策略)
            cas_number = extract_cas_from_synonyms(synonym_list)
            if cas_number:
                logging.info(f"Found CAS {cas_number} from synonyms for {name}")
        else:
            logging.warning(f"No synonyms found for compound: {name}")
    except Exception as e:
        logging.error(f"Error retrieving synonyms for '{name}' after retries: {e}")

    # --- Part 3: 获取 CAS (策略2: 从 compounds，仅在策略1失败时使用) ---
    if not cas_number:  # 只有在 synonyms 中没找到 CAS 时才执行
        try:
            compounds = get_compounds_with_retry(name)
            if compounds:
                first_compound = compounds[0]
                if hasattr(first_compound, 'synonyms') and first_compound.synonyms:
                    cas_number = extract_cas_from_synonyms(first_compound.synonyms)
                    if cas_number:
                        logging.info(f"Found CAS {cas_number} from compounds for {name}")
                    else:
                        logging.info(f"No CAS-like synonym found in compounds for: {name}")
                else:
                    logging.warning(f"No synonyms available in compound object for: {name}")
            else:
                logging.warning(f"No compound found in PubChem for: {name}")
        except Exception as e:
            logging.error(f"Error retrieving CAS from compounds for '{name}' after retries: {e}")

    # 打印完成状态
    print(f" [完成]")
    logging.info(f"Result '{name}': SMILES={smiles}, Name={univers_name}, CAS={cas_number}")
    return smiles, univers_name, cas_number

def data_quality_summary(df: pd.DataFrame, output_path: str):
    """
    生成列缺失和行缺失的汇总报告，输出到同一个Excel文件的两个sheet
    
    Parameters:
    df: DataFrame - 输入数据框
    output_path: str - 输出Excel文件路径
    """
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
    对副产物表格进行去重操作并生成相关分析报告
    
    Parameters:
    df: DataFrame - 输入数据框
    group_cols: list - 用于判断重复的列名列表
    output_dir: str or Path - 输出目录路径
    
    Returns:
    tuple: (去重后的数据框, 重复数据统计DataFrame)
    """
    from pathlib import Path
    
    # 处理输出目录
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    # 移除compound列
    byproduct_table = df.drop(columns=['compound'])
    
    # 统计重复情况
    is_duplicate = byproduct_table.duplicated(subset=group_cols, keep=False)
    total_duplicates = is_duplicate.sum()
    print(f"总重复行数: {total_duplicates}")
    
    # 保留每组第一行，直接去重
    original_rows = len(byproduct_table)
    byproduct_table_deduplicated = byproduct_table.drop_duplicates(subset=group_cols, keep='first')
    cleaned_rows = len(byproduct_table_deduplicated)
    
    print(f"原始行数: {original_rows}")
    print(f"去重后行数: {cleaned_rows}")
    print(f"移除重复行数: {original_rows - cleaned_rows}")
    
    # 分析重复数据 - 获取所有重复的行（包括每组的第一行）
    duplicate_records = byproduct_table[byproduct_table.duplicated(subset=group_cols, keep=False)]
    print(f"所有重复记录行数: {len(duplicate_records)}")
    
    # 添加重复标识列
    byproduct_table_with_flag = byproduct_table.copy()
    byproduct_table_with_flag['is_duplicate'] = is_duplicate
    duplicates_df = byproduct_table_with_flag[byproduct_table_with_flag['is_duplicate']].copy()
    sorted_duplicates = duplicates_df.sort_values(by=group_cols)
    
    # 统计每组重复行数
    group_sizes = duplicates_df.groupby(group_cols, dropna=False).size()
    group_sizes_df = group_sizes.to_frame(name='count')
    
    # 如果需要分析被移除的重复行
    removed_duplicates = byproduct_table[byproduct_table.duplicated(subset=group_cols, keep='first')]
    print(f"被移除的重复行数: {len(removed_duplicates)}")
    
    # 保存文件
    removed_duplicates.to_excel(output_path / 'byproduct_removed_table.xlsx', index=False)
    byproduct_table_deduplicated.to_excel(output_path / 'byproduct_table.xlsx', index=False)
    duplicate_records.to_excel(output_path / 'byproduct_duplicate_analysis.xlsx', index=False)
    sorted_duplicates.to_excel(output_path / 'byproduct_sorted_duplicates.xlsx', index=False)
    
    # 添加空值检查
    if not group_sizes_df.empty:
        group_sizes_df.to_excel(output_path / 'byproduct_duplicate_group_counts.xlsx', index=True)
    else:
        print("警告: 没有找到重复组，跳过保存分组计数文件")
    
    return byproduct_table_deduplicated
