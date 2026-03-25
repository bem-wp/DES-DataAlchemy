import logging

from app_configs import load_config
from rename_pdf_helper import *


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


if __name__ == "__main__":
    # 只需要 PDF 重命名功能，关闭 LLM 配置加载
    config = load_config(require_llm=False)
    
    if config.pdf_renamer is None:
        logger.error("未成功加载 pdf_renamer 配置！")
    else:
        process_pdf_rename(config.pdf_renamer)