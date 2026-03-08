def is_valid_value(value):
    """
    通用验证函数：检查值是否有效。
    - 拒绝: None
    - 拒绝: 空列表 []
    - 拒绝: 列表里的元素全为 None (如 [None, None])
    - 接受: 其他有效值
    """
    if value is None:
        return False
    if isinstance(value, list):
        if not value:  # 空列表
            return False
        if all(v is None for v in value):  # 全是 null
            return False
    return True

# 【修改点 1】: 将 condition_var 改为 condition_vars，并赋予默认值 None 以符合 Python 语法规则
def filter_des_entries(data, target_property, use_condition=True, condition_vars=None):
    """
    通用过滤函数。
    - 必检字段: molar_ratio (定义 DES 组成的基础字段，通常必留)
    - 条件字段: condition_vars (受 use_condition 控制，True 时检查列表中的每一个变量)
    - 目标物性: target_property (例如 'viscosity', 'density', 'conductivity')
    """
    if condition_vars is None:
        condition_vars = []

    if not isinstance(data, dict):
        return data
        
    extraction = data.get('extraction', {})
    
    # 自动检测键名，兼容 'des_systems' 和 'des_entries'
    target_key = None
    if 'des_systems' in extraction:
        target_key = 'des_systems'
    elif 'des_entries' in extraction:
        target_key = 'des_entries'
    else:
        return data

    raw_data_entries = extraction[target_key]
    raw_meta_entries = []
    has_metadata = False
    
    # 获取 metadata 同步列表
    if 'extraction_metadata' in data and target_key in data['extraction_metadata']:
        raw_meta_entries = data['extraction_metadata'][target_key]
        has_metadata = True

    clean_data_entries = []
    clean_meta_entries = []

    for i, entry in enumerate(raw_data_entries):
        should_keep = True
        
        # --- 规则 1: 检查 molar_ratio (基础组分定义必检) ---
        molar_ratio = entry.get('molar_ratio')
        if molar_ratio is None or molar_ratio == "":
            should_keep = False
        elif isinstance(molar_ratio, dict) and not molar_ratio.get('value'):
            should_keep = False

        # --- 规则 2: 检查 condition_vars (条件变量列表) ---
        # 【修改点 2】: 遍历 condition_vars 列表，检查每一个传入的条件变量 (如 ["t", "p"])
        if should_keep and use_condition and condition_vars:
            for var in condition_vars:
                if not is_valid_value(entry.get(var)):
                    should_keep = False
                    break  # 只要有一个条件变量无效，该条目即无效，直接跳出循环优化性能

        # --- 规则 3: 检查目标物性 (从 config 中读取的 property_json_field) ---
        if should_keep and target_property:
            if not is_valid_value(entry.get(target_property)):
                should_keep = False

        # --- 执行保留与同步 ---
        if should_keep:
            clean_data_entries.append(entry)
            if has_metadata and i < len(raw_meta_entries):
                clean_meta_entries.append(raw_meta_entries[i])

    # --- 更新数据结构 ---
    data['extraction'][target_key] = clean_data_entries
    if has_metadata:
        data['extraction_metadata'][target_key] = clean_meta_entries
        
    return data