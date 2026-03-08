import pandas as pd
import os
import shutil
import json

def select_yes_files(cfg):
    """
    根据pdf_has_xxx.csv
    从PDF仓库中挑选有目标物性记录的PDF，存放到指定文件夹
    """
    # --- 1. 读取配置文件，获取动态字段 ---
    if not os.path.exists(cfg.CONFIG_PATH):
        print(f"错误: 找不到配置文件 '{cfg.CONFIG_PATH}'")
        return
        
    with open(cfg.CONFIG_PATH, 'r', encoding='utf-8') as f:
        cond_config = json.load(f)
        
    # 动态获取 CSV 文件名和判断列名
    target_key = cond_config.get('target_key')
    output_csv_name = cond_config.get('output_csv')

    # --- 2. 基于 Config 配置路径 ---
    csv_path = os.path.join(cfg.IN_OUT_DIR , output_csv_name)
    source_folder = cfg.IN_DIR
    
    # 【核心修改点】：动态提取物性名称并拼接文件夹名
    # 例如: 将 "has_conductivity_data" 处理成 "conductivity"
    prop_name = target_key.replace('has_', '').replace('_data', '')
    
    target_folder_name = f"pdfs_has_{prop_name}"
    target_folder = os.path.join(cfg.IN_OUT_DIR, target_folder_name)

    # --- 3. 检查文件与文件夹 ---
    if not os.path.exists(source_folder):
        print(f"错误: 未找到源文件夹 '{source_folder}'")
        return

    if not os.path.exists(target_folder):
        os.makedirs(target_folder)
        print(f"已创建目标文件夹: {target_folder}")

    # --- 4. 读取并处理 CSV ---
    try:
        df = pd.read_csv(csv_path)
        
        # 检查目标列是否存在
        if target_key not in df.columns:
            print(f"错误: CSV 文件中未找到列 '{target_key}'。请检查配置文件和 CSV。")
            return

        # 筛选目标列为 'YES' 的行 (增加 .str.strip().str.upper() 提高容错率)
        yes_files_df = df[df[target_key].astype(str).str.strip().str.upper() == 'YES']
        
        print(f"在 CSV 中共找到 {len(yes_files_df)} 条标记为 YES 的记录。")
        print("开始复制文件...")

        success_count = 0
        missing_count = 0

        for index, row in yes_files_df.iterrows():
            json_filename = str(row['filename'])
            
            # 将文件名后缀从 .json 改为 .pdf
            file_base_name = os.path.splitext(json_filename)[0]
            pdf_filename = file_base_name + ".pdf"
            
            source_file_path = os.path.join(source_folder, pdf_filename)
            target_file_path = os.path.join(target_folder, pdf_filename)
            
            # 检查文件是否存在并复制
            if os.path.exists(source_file_path):
                shutil.copy2(source_file_path, target_file_path)
                success_count += 1
            else:
                print(f"警告: 未找到文件 {pdf_filename} (对应记录: {json_filename})")
                missing_count += 1

        print("-" * 40)
        print(f"处理完成！当前提取物性: {prop_name}")
        print(f"成功复制: {success_count} 个文件")
        if missing_count > 0:
            print(f"缺失文件: {missing_count} 个 (CSV中有记录但在 input 文件夹中未找到 PDF)")
        print(f"所有选中的 PDF 文件已统一保存至: {os.path.abspath(target_folder)}")
        print("-" * 40)

    except FileNotFoundError:
        print(f"错误: 未找到 CSV 文件 '{csv_path}'。请确认上一步提取是否成功运行。")
    except Exception as e:
        print(f"发生未知错误: {e}")

if __name__ == "__main__":
    
    # 集中管理路径配置
    class Config:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(current_dir)
        
        # 输入的原始 PDF 文件夹
        IN_DIR = os.path.normpath(os.path.join(project_root, "data", "input"))
        
       
        IN_OUT_DIR = os.path.normpath(os.path.join(project_root, "data", "in_out"))
        
        # 配置文件路径
        CONFIG_PATH = os.path.normpath(os.path.join(project_root, "configs", "01_pdf-screening_config.json"))

    # 将 Config 传入执行函数
    select_yes_files(Config)