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
                        print(f" [遇到网络拥堵，重试 {attempt + 1}/{max_retries - 1}，等待 {current_delay}s] ", end="", flush=True)
                        time.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        logging.debug(f"最终失败: {e}")
                        return None
            return None
        return wrapper
    return decorator

def validate_cas(cas_str):
    """
    【最终安检门】：使用严格正则验证 CAS 号。
    只有严格符合 2~7位数字-2位数字-1位数字 格式的才放行，彻底拦截 EC 号！
    """
    if not cas_str: return None
    cas_str = str(cas_str).strip()
    if re.match(r'^\d{2,7}-\d{2}-\d$', cas_str):
        return cas_str
    return None

def extract_cas_from_synonyms(synonyms_list):
    """从同义词列表中提取 CAS 号 (结合安检门)"""
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
    """直接从 PubChem PUG View API 获取专属 CAS 节点，附带自动重试防封禁"""
    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=10) as response:
        data = json.loads(response.read().decode('utf-8'))
        
        # 顺藤摸瓜，直接找 "Names and Identifiers" -> "Other Identifiers" -> "CAS" 专属节点
        for section in data.get('Record', {}).get('Section', []):
            if section.get('TOCHeading') == 'Names and Identifiers':
                for sub_sec in section.get('Section', []):
                    if sub_sec.get('TOCHeading') == 'Other Identifiers':
                        for sub_sub_sec in sub_sec.get('Section', []):
                            if sub_sub_sec.get('TOCHeading') == 'CAS':
                                # 找到了！直接提取数值
                                raw_cas = sub_sub_sec['Information'][0]['Value']['StringWithMarkup'][0]['String']
                                return raw_cas
    return None

def fetch_pubchem(name, missing_fields):
    """PubChem 检索引擎 (优先 API 直拿，正则守底线，增加访问限速防封)"""
    data = {'smiles': None, 'uname': None, 'cas': None}
    cid = None
    synonyms_list = []
    
    # 1. 基础查询：通过名字拿到 CID，顺便拿 SMILES 和 同义词列表
    try:
        compounds = pcp.get_compounds(name, 'name')
        if compounds:
            cid = compounds[0].cid
            synonyms_list = compounds[0].synonyms
            
            if 'smiles' in missing_fields:
                data['smiles'] = compounds[0].smiles
    except Exception as e: 
        logging.debug(f"PubChem Compounds API Error: {e}")

    # 2. 搜 CAS：【优先走 PUG View 直接途径】
    if 'cas' in missing_fields and cid:
        # 🚨 加入短暂休眠，防止背靠背请求触发 PubChem 的每秒5次动态封禁！
        time.sleep(0.5)
        
        raw_cas = get_cas_directly_from_pubchem(cid)
        # 拿到结果后，必须过最后一道安检门
        validated_cas = validate_cas(raw_cas)
        if validated_cas:
            data['cas'] = validated_cas
            
    # 3. 搜 CAS (兜底)：如果专属节点没找到，再去同义词里捞 (带着严格安检门)
    if 'cas' in missing_fields and not data['cas'] and synonyms_list:
        data['cas'] = extract_cas_from_synonyms(synonyms_list)

    # 4. 搜 通用名 (uname)
    if 'uname' in missing_fields and synonyms_list:
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
    """化合物信息检索引擎 (PubChem 专精版)"""
    if not isinstance(name, str) or not name.strip():
        # 返回 3 个 None 而不是 6 个
        return None, None, None

    # 每次检索新化合物前休息 0.5 秒，总体防封禁策略
    time.sleep(0.5)
    print(f"\n--> 正在向 PubChem 请求: [{name}] ...", flush=True)

    info = {'smiles': None, 'uname': None, 'cas': None}
    
    missing_fields = [k for k, v in info.items() if not v]
    
    try:
        new_data = fetch_pubchem(name, missing_fields)
        
        for field in missing_fields:
            if new_data and new_data.get(field):
                info[field] = new_data[field]
                logging.info(f"   └─ 成功获取 [{field}]: {new_data[field]}")
                
    except Exception as e:
        logging.error(f" PubChem Engine crashed on {name}: {e}")

    logging.info(f"Result '{name}': SMILES={info['smiles']}, Name={info['uname']}, CAS={info['cas']}")
    
    # 仅返回 3 个核心字段
    return info['smiles'], info['uname'], info['cas']