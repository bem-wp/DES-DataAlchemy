import os
from dotenv import load_dotenv
from pdf_prescreening_helper import process_pdf_folder, GenericPropertyHandler

if __name__ == "__main__":
    
    # ==========================================
    # 1. 路径与环境变量初始化
    # ==========================================
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(current_dir)
    env_path = os.path.join(project_root, ".env")

    # 强制指定从项目根目录读取 .env
    load_dotenv(dotenv_path=env_path)
    
    API_KEY = os.getenv("DIFY_API_KEY")
    API_BASE_URL = os.getenv("DIFY_API_BASE_URL", "http://localhost/v1")
    USER = os.getenv("DIFY_USER", "difyuser")

    if not API_KEY:
        print("❌ 错误: 未在 .env 文件中找到 DIFY_API_KEY")
        exit(1)

    # ==========================================
    # 2. 全局配置管理
    # ==========================================
    class Config:
        IN_DIR = os.path.normpath(os.path.join(project_root, "data", "input"))
        # 千问过滤出来的PDF生成的 JSON 存放位置（同时也作为下一步提取 CSV 的输入源）
        IN_OUT_DIR = os.path.normpath(os.path.join(project_root, "data", "in_out", "all-pdf_qwen_pdf-sel"))
        # 最终 CSV 的存放位置
        OUT_DIR = os.path.normpath(os.path.join(project_root, "data", "in_out"))
        # JSON 提取的规则配置文件
        CONFIG_PATH = os.path.normpath(os.path.join(project_root, "configs", "01_pdf-prescreening_config.json"))

    # ==========================================
    # 3. 核心流水线执行
    # ==========================================
    
    # Step 1: 扫描目录，调用 Dify API 筛选 PDF，并生成 JSON
    print(f"\n🚀 [Step 1] 开始执行 Dify PDF 初筛工作流...")
    process_pdf_folder(
        pdf_folder=Config.IN_DIR, 
        output_dir=Config.IN_OUT_DIR, 
        user=USER, 
        api_key=API_KEY, 
        api_base_url=API_BASE_URL
    )

    # Step 2: 提取生成的 JSON 文件，整合成最终的 CSV
    print(f"\n🚀 [Step 2] 开始将生成的 JSON 结果提取为 CSV...")
    handler = GenericPropertyHandler(Config.CONFIG_PATH)
    handler.process_json_to_csv(
        input_dir=Config.IN_OUT_DIR, 
        output_dir=Config.OUT_DIR
    )

    print("\n🎉 所有预筛选流程执行完毕！")