import os
from pathlib import Path
from dotenv import load_dotenv
from mining_pdf_helper import *

if __name__ == "__main__":
    
    # 1. 加载 .env 环境变量 (确保 LandingAI 的 API Key 等凭证被正确加载)
    load_dotenv(override=True)
    
    # 2. 集中管理工程路径配置
    class Config:
        # 获取当前脚本所在绝对目录 (例如: /src)
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(current_dir)
        # 中间的 in_out 文件夹 (存放选中的 PDF)
        IN_OUT_DIR = os.path.normpath(os.path.join(project_root, "data", "in_out"))
        
        # 输出的文件夹 (存放 LandingAI 的提取结果)
        OUT_DIR = os.path.normpath(os.path.join(project_root, "data", "in_out"))
        
        # 配置文件所在目录
        CONFIG_DIR = os.path.normpath(os.path.join(project_root, "configs"))
        
        # 系统路由配置文件路径
        CONFIG_PATH = os.path.join(CONFIG_DIR, "01_pdf-prescreening_config.json")
        
        # =========================================================
        # 【显式设置】修改这里的字符串来切换你想使用的 LandingAI 提取图纸
        # =========================================================
        LANDINGAI_SCHEMA_FILE = "02_PDF2JSON_schema.json" 
        LANDINGAI_SCHEMA_PATH = os.path.join(CONFIG_DIR, LANDINGAI_SCHEMA_FILE)

    # 3. 运行主处理函数
    process_landingai(Config)