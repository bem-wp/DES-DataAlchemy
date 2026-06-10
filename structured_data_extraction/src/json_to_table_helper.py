# d:\envs\codes\DES-DataAlchemy\structured_data_extraction\src\07_extract_json_to_table.py
import os
import json
import pandas as pd
from typing import Optional

from app_configs import JsontotableConfig

def extract_json_to_excel(cfg : JsontotableConfig):
    # 从指定文件夹读取所有 JSON 文件，提取 DES 配方数据并保存为 Excel。
    # property_name 必须与 JSON 中 des_entries 内的物性键名完全一致（如 'conductivity'），
    # 请确保调用时传入的字段名与原始 JSON 文件同步。
    property_name = cfg.property_name
    input_folder = cfg.json_folder
    output_dir = cfg.table_folder
    
    # 确保输出目录存在，并构建完整的输出文件路径
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, f"{property_name}_data.xlsx")

    all_data = []

    # 遍历文件夹中的所有 JSON 文件
    for filename in os.listdir(input_folder):
        if not filename.endswith('.json'):
            continue

        file_path = os.path.join(input_folder, filename)

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            article_title = data.get('extraction', {}).get('article_title', '')
            des_entries = data.get('extraction', {}).get('des_entries', [])

            for entry in des_entries:
                # 提取字段
                compounds_names = entry.get('compounds_names', [])
                compounds_acronyms = entry.get('compounds_acronyms', [])
                molar_ratio = entry.get('molar_ratio', '')
                temperature = entry.get('temperature', [])
                property_list = entry.get(property_name, [])   # 动态物性字段

                # 展平组分名称和缩写
                c1 = compounds_names[0] if len(compounds_names) > 0 else ''
                c2 = compounds_names[1] if len(compounds_names) > 1 else ''
                c3 = compounds_names[2] if len(compounds_names) > 2 else ''

                a1 = compounds_acronyms[0] if len(compounds_acronyms) > 0 else ''
                a2 = compounds_acronyms[1] if len(compounds_acronyms) > 1 else ''
                a3 = compounds_acronyms[2] if len(compounds_acronyms) > 2 else ''

                # 解析摩尔比并转为数值
                ratio_parts = molar_ratio.split(':') if molar_ratio else []
                x1 = _safe_float(ratio_parts, 0)
                x2 = _safe_float(ratio_parts, 1)
                x3 = _safe_float(ratio_parts, 2)

                # 提取温度
                temp = _safe_float(temperature, 0) if len(temperature) > 0 else None
                temp_unit = temperature[1] if len(temperature) > 1 and temperature[1] else ''

                # 提取指定物性
                prop_value = _safe_float(property_list, 0) if len(property_list) > 0 else None
                prop_unit = property_list[1] if len(property_list) > 1 and property_list[1] else ''

                # 数据清洗：组分非空数量与摩尔比非空数量必须匹配
                count_c = sum(1 for x in [c1, c2, c3] if x and x.strip())
                count_x = sum(1 for x in [x1, x2, x3] if x is not None)

                if count_c != count_x:
                    print(f"数据清洗：跳过不匹配数据 - c:{count_c}, x:{count_x} (文件:{filename})")
                    continue

                # 温度和物性字段均不能缺失
                if temp is None or not temp_unit.strip() or prop_value is None or not prop_unit.strip():
                    print(f"数据清洗：跳过温度或{property_name}缺失数据 (文件:{filename})")
                    continue

                all_data.append({
                    'article_title': article_title,
                    'c1': c1, 'c2': c2, 'c3': c3,
                    'a1': a1, 'a2': a2, 'a3': a3,
                    'x1': x1, 'x2': x2, 'x3': x3,
                    'temp': temp, 'temp_unit': temp_unit,
                    property_name: prop_value,
                    f'{property_name}_unit': prop_unit,
                    'ref': article_title
                })

        except Exception as e:
            print(f"处理文件 {filename} 时出错: {str(e)}")
            continue

    df = pd.DataFrame(all_data)
    if not df.empty:
        df.to_excel(output_file, index=False)
        print(f"数据已保存至 {output_file}，共 {len(df)} 条记录")
    else:
        print("未提取到任何有效数据")

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