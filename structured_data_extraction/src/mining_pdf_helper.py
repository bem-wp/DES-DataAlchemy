import json
import os
import re
import traceback
from pathlib import Path
from typing import List, Any, Dict

# 新增的导入
from landingai_ade import LandingAIADE

def save_json_safe(data_obj: Any, filename: str, output_dir: Path):
    """保存数据到本地 JSON，兼容处理各种对象类型"""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    file_path = output_path / filename
    
    if hasattr(data_obj, "model_dump_json"):
        content = data_obj.model_dump_json(indent=2)
    elif hasattr(data_obj, "json") and callable(getattr(data_obj, "json")):
        content = data_obj.json()
    elif hasattr(data_obj, "to_json"):
        content = data_obj.to_json()
    else:
        content = json.dumps(data_obj, indent=2, ensure_ascii=False, default=str)
        
    file_path.write_text(content, encoding="utf-8")
    print(f"✅ 数据已保存: {file_path}")


def process_landingai(cfg):
    """
    根据 Config 类和 config.json 动态定位目标 PDF，
    并加载显式指定的 LandingAI Schema 进行结构化数据提取。
    """
    # --- 1. 读取系统配置文件 (控制管线走向) ---
    if not os.path.exists(cfg.CONFIG_PATH):
        print(f"❌ 错误: 找不到系统配置文件 '{cfg.CONFIG_PATH}'")
        return
        
    with open(cfg.CONFIG_PATH, 'r', encoding='utf-8') as f:
        prop_config = json.load(f)
        
    # 动态获取物性名称 (例如: 从 "has_conductivity_data" 提取出 "conductivity")
    target_key = prop_config.get('target_key', 'unknown_key')
    prop_name = target_key.replace('has_', '').replace('_data', '')

    # --- 2. 基于 Config 配置路径 ---
    # 输入文件夹: 上一步生成的 data/in_out/pdfs_has_xxx
    input_dir = Path(cfg.IN_OUT_DIR) / f"pdfs_has_{prop_name}"
    
    # 输出文件夹: 动态加上物性前缀，防止不同物性的提取结果互相覆盖
    parsed_subdir = Path(cfg.OUT_DIR) / f"{prop_name}_00_parsed"
    extracted_subdir = Path(cfg.OUT_DIR) / f"{prop_name}_01_extracted"
    
    schema_path = Path(cfg.LANDINGAI_SCHEMA_PATH)

    # 确保输出目录存在
    parsed_subdir.mkdir(parents=True, exist_ok=True)
    extracted_subdir.mkdir(parents=True, exist_ok=True)

    print(f"📂 扫描输入文件夹: {input_dir}")
    print(f"📂 解析输出路径: {parsed_subdir}")
    print(f"📂 提取输出路径: {extracted_subdir}")

    # --- 3. 加载 LandingAI 专用的 Schema (提取图纸) ---
    if not schema_path.exists():
        print(f"❌ LandingAI Schema 加载失败: 未找到文件 {schema_path}")
        print(f"💡 提示: 请确保您已将 Schema 文件放在正确目录下，并在最下方的 Config 类中填写了正确的文件名。")
        return
        
    try:
        print(f"📄 加载 LandingAI 提取图纸: {schema_path.name}")
        landingai_schema_dict = json.loads(schema_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ Schema 解析失败: {e}")
        return

    # --- 4. 初始化 LandingAI 客户端 ---
    client = LandingAIADE()
    MODEL_PARSE = "dpt-2-latest"
    MODEL_EXTRACT = "extract-latest"

    # --- 5. 获取所有待处理的 PDF 文件 ---
    pdf_files = list(input_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"⚠️ 在 {input_dir} 未找到任何 PDF 文件。请确认上一步的 PDF 筛选脚本已成功运行。")
        return

    print(f"📊 共发现 {len(pdf_files)} 个 PDF 文件，准备开始处理...\n")

    # --- 6. 批量循环处理 ---
    for index, doc_path in enumerate(pdf_files):
        filename = doc_path.name
        print(f"--- [{index+1}/{len(pdf_files)}] 正在检查: {filename} ---")

        # 定义预期的输出文件名
        expected_parsed_file = parsed_subdir / f"{filename}_parsed.json"
        expected_extracted_file = extracted_subdir / f"{filename}_extracted.json"

        # --- 敏感跳过逻辑 (Skip Logic) ---
        if expected_parsed_file.exists() and expected_extracted_file.exists():
            print(f"⏭️  文件已完全处理 (Parsed & Extracted 已存在)，跳过以节省 API 用量。")
            continue
        
        # --- 开始调用 API 处理 ---
# --- 开始调用 API 处理 ---
        try:
            # Step A: 解析文档结构 (Parse)
            if not expected_parsed_file.exists():
                print(f"⚡ [API] 正在调用 Parse ({MODEL_PARSE})...")
                
                # 【修改这里】：去掉 str()，直接原汁原味地传入 Path 对象 doc_path
                parse_result = client.parse(document=doc_path, model=MODEL_PARSE)
                
                # 保存 parsed 结果 
                save_json_safe(parse_result, expected_parsed_file.name, str(parsed_subdir))
                print(f"   ✅ Parse 完成，已保存本地。")
                
                markdown_content = parse_result.markdown
            else:
                print(f"   ✅ Parse 文件已存在，加载本地 Markdown 数据...")
                with open(expected_parsed_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    markdown_content = data.get('markdown', '') or data.get('body', {}).get('markdown', '')

            # Step B: 提取核心物性数据 (Extract)
            if not expected_extracted_file.exists():
                print(f"⚡ [API] 正在调用 Extract ({MODEL_EXTRACT})...")
                extraction_result = client.extract(
                    schema=json.dumps(landingai_schema_dict), # 将专用图纸喂给 LandingAI
                    markdown=markdown_content,
                    model=MODEL_EXTRACT
                )
                
                # 保存 extracted 结果
                save_json_safe(extraction_result, expected_extracted_file.name, str(extracted_subdir))
                print(f"   ✅ Extract 提取完成，已保存本地。")
            else:
                print(f"   ✅ Extract 文件已存在，跳过提取。")

        except Exception as e:
            print(f"❌ 处理文件 {filename} 时发生致命错误: {e}")
            traceback.print_exc()
            continue

    print("\n🎉 所有的 LandingAI 数据提取任务已全部处理完毕！")