import os
import json
from pathlib import Path

from app_configs import JsonFilterConfig


def is_valid_value(value):
    if value is None:
        return False
    if isinstance(value, list):
        if not value:  # 空列表
            return False
        if all(v is None for v in value):  # 全是 null
            return False
    return True


def filter_des_entries(data, target_property, condition_vars=None):
    """
    通用过滤函数。
    - 必检字段: molar_ratio (定义 DES 组成的基础字段，通常必留)
    - 条件字段: condition_vars (若列表非空，则检查其中的每一个变量)
    - 目标物性: target_property (例如 'viscosity', 'density', 'conductivity')
    """
    if condition_vars is None:
        condition_vars = []

    if not isinstance(data, dict):
        return data
        
    extraction = data.get('extraction', {})
    
    # 自动检测键名，兼容 'des_systems' 和 'des_entries'
    target_key = None
    if 'des_entries' in extraction:
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

        # --- 规则 2: 检查条件变量 (若 condition_vars 非空，则逐一校验) ---
        if should_keep and condition_vars:
            for var in condition_vars:
                if not is_valid_value(entry.get(var)):
                    should_keep = False
                    break  # 只要有一个条件变量无效，该条目即无效，直接跳出循环优化性能

        # --- 规则 3: 检查目标物性 ---
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


def process_filtering(cfg: JsonFilterConfig):
    """
    主处理流程：从配置对象读取参数，执行过滤
    过滤后若 des_entries 为空，则不输出文件
    """
    # 1. 从配置对象获取参数
    prop_name = cfg.target_property
    input_dir = Path(cfg.json_input_folder)
    output_dir = Path(cfg.json_output_folder)
    cond_vars = cfg.condition_vars

    # 2. 确保输出目录存在
    output_dir.mkdir(parents=True, exist_ok=True)

    if not input_dir.exists():
        print(f"⚠️ 找不到输入文件夹 '{input_dir}'。请确认 LandingAI 提取脚本已成功运行。")
        return

    print(f"⚙️ 当前过滤目标物性: [{prop_name}]")
    print(f"🌡️ 是否检查状态变量: [{'是' if cond_vars else '否'}]  "
          f"{'(' + ', '.join(cond_vars) + ')' if cond_vars else ''}")
    print(f"📂 开始扫描文件夹: {input_dir} ...\n")
    
    files = list(input_dir.glob("*.pdf_extracted.json"))
    count = 0
    
    for doc_path in files:
        filename = doc_path.name
        new_filename = filename.replace('.pdf_extracted.json', '.pdf_filtered.json')
        output_filepath = output_dir / new_filename
        
        try:
            with open(doc_path, 'r', encoding='utf-8') as f:
                json_data = json.load(f)
            
            # 过滤函数统一处理 'des_entries'，此处直接硬编码
            target_key_log = 'des_entries'
            old_len = len(json_data.get('extraction', {}).get(target_key_log, []))

            # 调用通用过滤函数
            cleaned_data = filter_des_entries(
                data=json_data, 
                target_property=prop_name, 
                condition_vars=cond_vars
            )
            
            # 获取过滤后的条目数量
            new_len = len(cleaned_data.get('extraction', {}).get(target_key_log, []))

            # 如果过滤后 des_entries 为空列表，则跳过保存
            if new_len == 0:
                print(f" ⚠️ [跳过] {filename} -> 过滤后无有效条目，未保存文件")
                continue  # 跳过保存，不计入成功计数

            # 写入新文件
            with open(output_filepath, 'w', encoding='utf-8') as f:
                json.dump(cleaned_data, f, indent=2, ensure_ascii=False)
            
            print(f" ✅ [处理成功] {filename} -> {new_filename} (条目保留: {old_len} -> {new_len})")
            count += 1
            
        except Exception as e:
            print(f" ❌ [出错] 处理文件 {filename} 时发生错误: {e}")

    print(f"\n🎉 全部完成！共保存了 {count} 个有效文件。清洗后的数据保存在 {output_dir}")