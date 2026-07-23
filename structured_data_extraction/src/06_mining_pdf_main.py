import logging
from mining_pdf_helper import process_extraction
from app_configs import load_extraction_config


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

if __name__ == "__main__":

    config = load_extraction_config()
    
    logger.info("=" * 60)

    process_extraction(config)