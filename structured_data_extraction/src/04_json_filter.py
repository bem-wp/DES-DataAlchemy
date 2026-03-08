import json
import os
from pathlib import Path

from json_filter_helper import is_valid_value, filter_des_entries

def process_filtering(cfg):
    """
    主处理流程：读取配置，执行过滤
    """
    # 1. 读取全局配置
    if not os.path.exists(cfg.CONFIG_PATH):
        print(f"❌ 错误: 找不到配置文件 '{cfg.CONFIG_PATH}'")
        return
        
    with open(cfg.CONFIG_PATH, 'r', encoding='utf-8') as f:
        prop_config = json.load(f)
        
    # --- 根据 filter_config 结构获取参数 ---
    prop_name = prop_config.get('property_json_field')
    use_condition = prop_config.get('use_condition')

    # 2. 动态拼接路径
    input_dir = Path(cfg.IN_DIR) / f"{prop_name}_01_extracted"
    output_dir = Path(cfg.OUT_DIR) / f"{prop_name}_02_filtered"
    
    # 确保输出目录存在
    output_dir.mkdir(parents=True, exist_ok=True)

    if not input_dir.exists():
        print(f"⚠️ 找不到输入文件夹 '{input_dir}'。请确认 LandingAI 提取脚本已成功运行。")
        return

    print(f"⚙️ 当前过滤目标物性: [{prop_name}]")
    print(f"🌡️ 是否检查环境条件: [{'是' if use_condition else '否'}]")
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
            
            # 获取过滤前的条目数量
            target_key_log = 'des_systems' if 'des_systems' in json_data.get('extraction', {}) else 'des_entries'
            old_len = len(json_data.get('extraction', {}).get(target_key_log, []))

            # --- 【核心】调用通用过滤函数，传入动态参数 ---
            cleaned_data = filter_des_entries(
                data=json_data, 
                target_property=prop_name, 
                use_condition=use_condition
            )
            
            # 获取过滤后的条目数量
            new_len = len(cleaned_data.get('extraction', {}).get(target_key_log, []))

            # 写入新文件
            with open(output_filepath, 'w', encoding='utf-8') as f:
                json.dump(cleaned_data, f, indent=2, ensure_ascii=False)
            
            print(f" ✅ [处理成功] {filename} -> {new_filename} (条目保留: {old_len} -> {new_len})")
            count += 1
            
        except Exception as e:
            print(f" ❌ [出错] 处理文件 {filename} 时发生错误: {e}")

    print(f"\n🎉 全部完成！共过滤了 {count} 个文件。清洗后的数据保存在 {output_dir}")

if __name__ == "__main__":
    
    # 集中管理工程路径配置
    class Config:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(current_dir)
        
        # 结果存取根目录
        IN_DIR = os.path.normpath(os.path.join(project_root, "data", "in_out"))

        OUT_DIR = os.path.normpath(os.path.join(project_root, "data", "in_out"))
        
        # 配置文件路径
        CONFIG_DIR = os.path.normpath(os.path.join(project_root, "configs"))
        CONFIG_PATH = os.path.join(CONFIG_DIR, "03_json_filter_config.json")

    # 运行
    process_filtering(Config)