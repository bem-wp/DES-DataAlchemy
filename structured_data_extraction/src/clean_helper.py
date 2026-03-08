import pandas as pd
import numpy as np

def extract_complex_value(field_data):
    """从复杂结构（字符串、列表、字典、字典列表）中提取值列表"""
    if field_data is None:
        return []
    
    if isinstance(field_data, dict):
        val = field_data.get('value')
        return [str(val).strip()] if val is not None else []
    
    if isinstance(field_data, str):
        clean_str = field_data.strip()
        if not clean_str or clean_str.lower() == 'null':
            return []
        if clean_str.startswith('[') and clean_str.endswith(']'):
            return [x.strip() for x in clean_str[1:-1].split(',')]
        return [clean_str]
    
    if isinstance(field_data, list):
        results = []
        for item in field_data:
            if isinstance(item, dict):
                val = item.get('value')
                if val is not None:
                    results.append(str(val).strip())
            elif item is not None:
                results.append(str(item).strip())
        return results
    
    return [str(field_data)]

def find_target_column(target_columns, candidates):
    """在目标列列表中查找匹配的列名（忽略大小写）。"""
    target_lower_map = {col.lower(): col for col in target_columns}
    for cand in candidates:
        if cand.lower() in target_lower_map:
            return target_lower_map[cand.lower()]
    return None

def get_condition_info(cond_name):
    """智能条件推断器：根据简写推断完整的 JSON 字段名和 CSV 候选列名"""
    cond_name = str(cond_name).lower()
    if cond_name in ['t', 'temperature', 'temp']:
        return 'temperature', ['t', 'temperature', 'temp', 'T']
    elif cond_name in ['p', 'pressure', 'press']:
        return 'pressure', ['p', 'pressure', 'press', 'P']
    else:
        return cond_name, [cond_name]

def parse_condition_config(config):
    """解析条件配置 (向下兼容旧版的 use_temperature)"""
    use_cond = config.get("use_condition", False)
    cond_cols = config.get("condition_cols", [])
    
    if not use_cond and config.get("use_temperature"):
        use_cond = True
        cond_cols = [config.get("temp_col_name", "temperature")]
        
    return use_cond, cond_cols

def build_column_mapping(target_columns, config):
    """根据 Config 构建 JSON 到 CSV Column 的映射关系字典"""
    col_map = {}
    col_map['number'] = find_target_column(target_columns, ['number', 'num', 'No', 'ID'])
    col_map['reference'] = find_target_column(target_columns, ['ref', 'ref.', 'reference'])
    
    prop_cols = config.get("property_cols", [])
    if config.get("property_type") == "single_with_unit":
        col_map['prop_val'] = find_target_column(target_columns, [prop_cols[0]] if len(prop_cols)>0 else [])
        col_map['prop_unit'] = find_target_column(target_columns, [prop_cols[1]] if len(prop_cols)>1 else [])
    
    use_cond, cond_cols = parse_condition_config(config)
    cond_map = {}
    if use_cond:
        for cond in cond_cols:
            json_key, candidates = get_condition_info(cond)
            cond_map[cond] = {
                'json_key': json_key,
                'val_col': find_target_column(target_columns, candidates),
                'unit_col': find_target_column(target_columns, [f"{c}_unit" for c in candidates])
            }
    return col_map, cond_map

def extract_rows_from_json_data(data, target_columns, col_map, cond_map, config, file_number):
    """从单个 JSON 数据对象中提取出行列表 (Rows)"""
    extraction_root = data.get("extraction", {})
    des_entries = extraction_root.get("des_entries", [])
    article_title_obj = extraction_root.get("article_title", "")
    
    if not des_entries:
        if isinstance(extraction_root, list):
            des_entries = extraction_root
        elif "des_systems" in extraction_root:
            des_entries = extraction_root["des_systems"]
        elif not isinstance(extraction_root, dict): 
            return []
    
    if isinstance(article_title_obj, dict):
        article_title = article_title_obj.get("value", "")
    else:
        article_title = str(article_title_obj)

    if not isinstance(des_entries, list): 
        return []

    rows = []
    prop_cols = config.get("property_cols", [])
    
    for item in des_entries:
        row = {col: None for col in target_columns}
        
        # (A) 组分名称 
        raw_names = extract_complex_value(item.get("compounds_names"))
        names = []
        for n in raw_names:
            if isinstance(n, str) and ", " in n:
                names.extend([p.strip() for p in n.split(", ")])
            else:
                names.append(n)
        for i, part in enumerate(names):
            if i < 3: row[f"c{i+1}"] = part

        # (B) 缩写 
        raw_acronyms = extract_complex_value(item.get("compounds_acronyms"))
        acronyms = []
        for a in raw_acronyms:
            if isinstance(a, str) and ", " in a:
                acronyms.extend([p.strip() for p in a.split(", ")])
            else:
                acronyms.append(a)
        for i, part in enumerate(acronyms):
            if i < 3: row[f"c{i+1}_acr"] = part

        # (C) 摩尔比
        ratio_vals = extract_complex_value(item.get("molar_ratio"))
        if ratio_vals:
            ratio_str = ratio_vals[0]
            if ratio_str and ratio_str.lower() not in ["none", "null", "nan"]:
                delimiter = ":" if ":" in ratio_str else ","
                parts = [p.strip() for p in ratio_str.split(delimiter)]
                for i, part in enumerate(parts):
                    if i < 3:
                        try: row[f"x{i+1}"] = float(part)
                        except ValueError: row[f"x{i+1}"] = part

        # (D) 多维环境变量提取
        for cond_name, cinfo in cond_map.items():
            c_vals = extract_complex_value(item.get(cinfo['json_key']))
            if c_vals:
                if cinfo['val_col']: 
                    try: row[cinfo['val_col']] = float(c_vals[0])
                    except: row[cinfo['val_col']] = c_vals[0]
                if len(c_vals) > 1 and cinfo['unit_col']:
                    row[cinfo['unit_col']] = c_vals[1]

        # (E) 动态目标物性提取
        prop_json_field = config.get("property_json_field")
        prop_type = config.get("property_type")
        prop_vals = extract_complex_value(item.get(prop_json_field))
        
        if prop_vals:
            if prop_type == "multiple_values":
                for i, val in enumerate(prop_vals):
                    if i < len(prop_cols) and val and str(val).lower() != 'null':
                        mapped_c = find_target_column(target_columns, [prop_cols[i]])
                        if mapped_c:
                            try: row[mapped_c] = float(val)
                            except: pass
            
            elif prop_type == "single_with_unit":
                if col_map.get('prop_val'):
                    try: row[col_map['prop_val']] = float(prop_vals[0])
                    except: row[col_map['prop_val']] = prop_vals[0]
                if len(prop_vals) > 1 and col_map.get('prop_unit'):
                    row[col_map['prop_unit']] = prop_vals[1]

        # (F) 引用及编号
        if col_map.get('reference'): row[col_map['reference']] = article_title
        if col_map.get('number'): row[col_map['number']] = file_number
        
        rows.append(row)
        
    return rows

def apply_data_cleaning(df, config):
    """
    接收原始 DataFrame，依据规则清洗数据。
    返回: (清洗后的_df, 丢弃的数据集字典)
    """
    dropped_dfs = {}
    
    # === Step 1. 组分与比例数量对齐清洗 ===
    existing_c = [c for c in ['c1', 'c2', 'c3'] if c in df.columns]
    existing_x = [x for x in ['x1', 'x2', 'x3'] if x in df.columns]
    
    if existing_c and existing_x:
        df['c_count'] = df[existing_c].count(axis=1)
        df['x_count'] = df[existing_x].count(axis=1)
        mask_aligned = df['c_count'] == df['x_count']
        
        df_aligned = df[mask_aligned].copy()
        dropped_step1 = df[~mask_aligned].copy()
        
        df_aligned.drop(columns=['c_count', 'x_count'], inplace=True)
        dropped_step1.drop(columns=['c_count', 'x_count'], inplace=True)
        
        dropped_dfs['01_dropped_alignment'] = dropped_step1
        df = df_aligned
    else:
        dropped_dfs['01_dropped_alignment'] = pd.DataFrame(columns=df.columns)

    # === Step 2. 基础非空清洗 (c1, c2) ===
    if 'c1' in df.columns and 'c2' in df.columns:
        df_valid = df.dropna(subset=['c1', 'c2'], how='any')
        dropped_dfs['02_dropped_missing_c1c2'] = df.drop(df_valid.index)
        df = df_valid
    else:
        dropped_dfs['02_dropped_missing_c1c2'] = pd.DataFrame(columns=df.columns)

    # === Step 3. 基础非空清洗 (x1, x2) ===
    if 'x1' in df.columns and 'x2' in df.columns:
        df_valid = df.dropna(subset=['x1', 'x2'], how='any')
        dropped_dfs['03_dropped_missing_x1x2'] = df.drop(df_valid.index)
        df = df_valid
    else:
        dropped_dfs['03_dropped_missing_x1x2'] = pd.DataFrame(columns=df.columns)

    # === Step 4. 动态物性与多维条件清洗 (T, P 等) ===
    prop_type = config.get("property_type")
    prop_cols = config.get("property_cols", [])
    clean_strategy = config.get("clean_strategy", "any") 
    
    existing_prop = [find_target_column(df.columns.tolist(), [p_col]) for p_col in prop_cols]
    existing_prop = [col for col in existing_prop if col]
    
    use_cond, cond_cols = parse_condition_config(config)
    real_cond_cols = []
    if use_cond:
        for cond in cond_cols:
            _, candidates = get_condition_info(cond)
            real_col = find_target_column(df.columns.tolist(), candidates)
            if real_col:
                real_cond_cols.append(real_col)

    if prop_type == "multiple_values":
        df_valid_p = df.copy()
        if existing_prop:
            df_valid_p = df_valid_p.dropna(subset=existing_prop, how=clean_strategy)
        if real_cond_cols:
            df_valid_p = df_valid_p.dropna(subset=real_cond_cols, how='any')
        dropped_dfs['04_dropped_missing_props'] = df.drop(df_valid_p.index)
        df = df_valid_p
            
    elif prop_type == "single_with_unit":
        tv_cols = real_cond_cols.copy()
        if existing_prop:
            tv_cols.append(existing_prop[0])
        
        if tv_cols:
            df_valid_p = df.dropna(subset=tv_cols, how=clean_strategy)
            dropped_dfs['04_dropped_missing_props'] = df.drop(df_valid_p.index)
            df = df_valid_p
    else:
        dropped_dfs['04_dropped_missing_props'] = pd.DataFrame(columns=df.columns)

    return df, dropped_dfs