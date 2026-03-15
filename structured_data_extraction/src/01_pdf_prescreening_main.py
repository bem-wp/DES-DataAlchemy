import logging

from llm_configs import load_config
from pdf_prescreening_helper import process_pdf_folder

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

if __name__ == "__main__":

    config = load_config()

    logger.info(f"正在调用 {config.model_name} 进行PDF预筛选...")
    logger.info("=" * 60)

    process_pdf_folder(config)
