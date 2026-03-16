import json
import logging
import os
import re
import socket
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
    
    # 针对 Comment 1: 判断 None，并显式抛出异常，主动触发装饰器的重试
    if data is None:

        raise Exception("PubChem API 返回了空数据 (None)")
    
    # 针对 Comment 2: 使用 (data or {}) 增加一层防御性保护
    for section in (data or {}).get('Record', {}).get('Section', []):
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
