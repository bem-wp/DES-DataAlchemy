import logging

from app_configs import load_pdf_renamer_config # 直接导入专属函数
from rename_pdf_helper import process_pdf_pool

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

if __name__ == "__main__":

    config = load_pdf_renamer_config()
    
    process_pdf_pool(config)