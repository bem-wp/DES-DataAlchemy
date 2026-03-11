import os
import sys
import time
import re
import logging
import json
import urllib.request
from urllib.error import HTTPError, URLError
import socket
from functools import lru_cache
from datetime import datetime
import pandas as pd

import pubchempy as pcp

# 设置全局超时（防止网络死锁）
socket.setdefaulttimeout(15)

# 日志配置
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

# ==========================================
# 1. 基础工具与防封禁重试机制
# ==========================================
def retry_on_failure(max_retries=3, delay=2, backoff=2):
    """重试装饰器，附带防崩溃护甲"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            current_delay = delay
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt < max_retries - 1:
                        print(f" [遇到异常: {type(e).__name__}，重试 {attempt + 1}/{max_retries - 1}，等待 {current_delay}s] ", end="", flush=True)
                        time.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        logging.debug(f"最终失败: {e}")
                        return None
            return None
        return wrapper
    return decorator

def validate_cas(cas_str):
    if not cas_str: return None
    cas_str = str(cas_str).strip()
    if re.match(r'^\d{2,7}-\d{2}-\d$', cas_str):
        return cas_str
    return None

def extract_cas_from_synonyms(synonyms_list):
    if not synonyms_list: return None
    for synonym in synonyms_list:
        valid_cas = validate_cas(synonym)
        if valid_cas:
            return valid_cas
    return None

# ==========================================
# 2. PubChem 专属检索引擎
# ==========================================
@retry_on_failure(max_retries=3, delay=2, backoff=2)
def get_cas_directly_from_pubchem(cid):
    """直接从 PubChem PUG View API 获取专属 CAS 节点，显式异常抛出"""
    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
    except socket.timeout:
        raise Exception("PubChem API 网络连接超时")
    except HTTPError as e:
        raise Exception(f"PubChem 拒绝访问或限流, 状态码: {e.code}")
    except URLError as e:
        raise Exception(f"PubChem URL 路由错误: {e.reason}")
    except json.JSONDecodeError:
        raise Exception("PubChem 返回了无效的 JSON 数据")
    
    for section in data.get('Record', {}).get('Section', []):
        if section.get('TOCHeading') == 'Names and Identifiers':
            for sub_sec in section.get('Section', []):
                if sub_sec.get('TOCHeading') == 'Other Identifiers':
                    for sub_sub_sec in sub_sec.get('Section', []):
                        if sub_sub_sec.get('TOCHeading') == 'CAS':
                            try:
                                return sub_sub_sec['Information'][0]['Value']['StringWithMarkup'][0]['String']
                            except (KeyError, IndexError, TypeError):
                                logging.debug(f"CID {cid} CAS节点残缺，跳过。")
                                continue
    return None

def fetch_pubchem(name, target_fields):
    """采用 get_compounds 全量获取"""
    data = {'smiles': None, 'uname': None, 'cas': None}
    cid = None
    synonyms_list = []
    
    # 1. 基础查询 (全量下载)
    try:
        compounds = pcp.get_compounds(name, 'name')
        if compounds:
            cid = compounds[0].cid
            synonyms_list = compounds[0].synonyms
            
            if 'smiles' in target_fields:
                data['smiles'] = compounds[0].smiles
    except Exception as e: 
        logging.debug(f"PubChem Compounds API Error: {e}")

    # 2. 搜专属 CAS (PUG View)
    if 'cas' in target_fields and cid:
        time.sleep(0.5) 
        raw_cas = get_cas_directly_from_pubchem(cid)
        validated_cas = validate_cas(raw_cas)
        if validated_cas:
            data['cas'] = validated_cas
            
    # 3. 搜 CAS (同义词兜底)
    if 'cas' in target_fields and not data['cas'] and synonyms_list:
        data['cas'] = extract_cas_from_synonyms(synonyms_list)

    # 4. 搜 通用名 (uname)
    if 'uname' in target_fields and synonyms_list:
        new_name = synonyms_list[0]
        if re.search(r'\d{2,}', new_name) and len(synonyms_list) > 1:
            data['uname'] = synonyms_list[1]
        else:
            data['uname'] = new_name

    return data

# ==========================================
# 3. 核心调度器
# ==========================================
@lru_cache(maxsize=None)
def f_fetch_compound_info(name):
    if not isinstance(name, str) or not name.strip():
        return None, None, None

    time.sleep(0.5)
    
    info = {'smiles': None, 'uname': None, 'cas': None}
    target_fields = ['smiles', 'uname', 'cas'] 
    
    try:
        new_data = fetch_pubchem(name, target_fields)
        
        for field in target_fields:
            if new_data and new_data.get(field):
                info[field] = new_data[field]
                logging.info(f"   └─ 成功获取 [{field}]: {new_data[field]}")
                
    except Exception as e:
        logging.error(f" PubChem Engine crashed on {name}: {e}")

    logging.info(f"Result '{name}': SMILES={info['smiles']}, Name={info['uname']}, CAS={info['cas']}")
    return info['smiles'], info['uname'], info['cas']


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
