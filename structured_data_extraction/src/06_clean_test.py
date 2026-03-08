import os
import glob
import json
import pandas as pd
import numpy as np

# 导入所有纯函数
from clean_helper import build_column_mapping, extract_rows_from_json_data, apply_data_cleaning

def main():
    # === 1. 配置与路径设定 ===
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(current_dir)

    CONFIG_DIR = os.path.normpath(os.path.join(project_root, "configs"))
    CONFIG_PATH = os.path.join(CONFIG_DIR, "04_tabular_clean_config.json")
    OUTPUT_SCH = os.path.join(CONFIG_DIR, "tabular_shcema.csv")

    INPUT_FOLDER = os.path.normpath(os.path.join(project_root, "data", "in_out", "conductivity_02_filtered"))
    OUTPUT_FOLDER = os.path.normpath(os.path.join(project_root, "data", "output"))
    TEMP_FOLDER = os.path.normpath(os.path.join(project_root, "temp_data")) 
    
    os.makedirs(TEMP_FOLDER, exist_ok=True)
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)

    # === 2. 加载配置并初始化 ===
    if not os.path.exists(CONFIG_PATH):
        print(f"❌ 错误: 找不到配置文件 '{CONFIG_PATH}'")
        return
        
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        config = json.load(f)
        
    prop_field = config.get('property_json_field', 'default_prop')
    print(f"✅ 成功加载清理范式，目标物性: {prop_field}")

    if not os.path.exists(INPUT_FOLDER) or not os.path.exists(OUTPUT_SCH):
        print(f"❌ 请检查路径是否存在:\n 文件夹: {INPUT_FOLDER}\n Schema: {OUTPUT_SCH}")
        return

    # === 3. 提取阶段 (JSON -> RAW CSV) ===
    schema_df = pd.read_csv(OUTPUT_SCH, nrows=0)
    target_columns = schema_df.columns.tolist()
    print(f"📊 [Schema] 目标结构已加载: {target_columns}")
    
    col_map, cond_map = build_column_mapping(target_columns, config)
    json_files = glob.glob(os.path.join(INPUT_FOLDER, "*.json"))
    
    if not json_files:
        print(f"⚠️ 警告: 在 '{INPUT_FOLDER}' 下未找到 JSON 文件。")
        return

    print(f"🚀 [Start] 发现 {len(json_files)} 个 JSON 文件，准备提取基础数据...")
    all_rows = []
    success_count = 0
    fail_count = 0

    for json_path in json_files:
        filename = os.path.basename(json_path)
        file_number = filename.split('.')[0] 
        
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 调用 helper 里的纯逻辑提取行
            rows = extract_rows_from_json_data(data, target_columns, col_map, cond_map, config, file_number)
            if rows:
                all_rows.extend(rows)
            success_count += 1
        except Exception as e:
            print(f"❌ [Error] 处理文件 {filename} 时出错: {e}")
            fail_count += 1

    if not all_rows:
        print("🛑 [End] 未提取到任何有效数据，流程终止。")
        return

    # 生成原始 DataFrame 并持久化
    df_raw = pd.DataFrame(all_rows, columns=target_columns)
    pd.set_option('future.no_silent_downcasting', True)
    df_raw = df_raw.replace(r'^\s*$', np.nan, regex=True).replace('None', np.nan)
    
    RAW_EXT_FILE = os.path.join(TEMP_FOLDER, f"00_raw_ext_{prop_field}.csv")
    df_raw.to_csv(RAW_EXT_FILE, index=False, encoding='utf-8-sig')
    
    print("="*40)
    print(f"✅ [Step 1] 提取完成！ 成功文件: {success_count} | 记录总数: {len(df_raw)}")
    print(f"📄 原始底表已保存: {RAW_EXT_FILE}")
    print("="*40)

    # === 4. 级联清洗阶段 (RAW CSV -> Cleaned CSV + Dropped CSVs) ===
    print(f"\n🧹 [Step 2] 开始级联数据清洗追踪...")
    original_count = len(df_raw)
    
    # 核心调用：由纯函数执行所有的切片逻辑，返回清洗结果与丢弃清单
    df_final, dropped_dfs = apply_data_cleaning(df_raw, config)
    
    # 集中处理 I/O：持久化各个步骤中抛弃的数据
    for drop_step_name, df_dropped in dropped_dfs.items():
        if not df_dropped.empty:
            drop_path = os.path.join(TEMP_FOLDER, f"{drop_step_name}_{prop_field}.csv")
            df_dropped.to_csv(drop_path, index=False, encoding='utf-8-sig')

    FINAL_OUTPUT_FILE = os.path.join(OUTPUT_FOLDER, f"{prop_field}_cleaned.csv")
    df_final.to_csv(FINAL_OUTPUT_FILE, index=False, encoding='utf-8-sig')
    
    # 输出漏斗统计
    print("-" * 55)
    print(f"📊 清洗漏斗统计:")
    print(f"[{original_count}] 原始提取总记录数")
    print(f"  |")
    print(f"  |--- [剔除 {len(dropped_dfs.get('01_dropped_alignment', []))}] 条 -> 组分与比例未对齐")
    print(f"  |--- [剔除 {len(dropped_dfs.get('02_dropped_missing_c1c2', []))}] 条 -> 缺少核心组分名称 c1,c2")
    print(f"  |--- [剔除 {len(dropped_dfs.get('03_dropped_missing_x1x2', []))}] 条 -> 缺少核心摩尔比例 x1,x2")
    print(f"  |--- [剔除 {len(dropped_dfs.get('04_dropped_missing_props', []))}] 条 -> 目标物性或关联条件不达标")
    print(f"  v")
    print(f"[{len(df_final)}] 最终高质量数据已保存至: {FINAL_OUTPUT_FILE}")
    print("-" * 55)

if __name__ == "__main__":
    main()