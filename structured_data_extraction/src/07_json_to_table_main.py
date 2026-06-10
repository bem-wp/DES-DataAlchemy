import logging
from json_to_table_helper import extract_json_to_excel
from app_configs import load_json_to_table_config


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

if __name__ == "__main__":

    config = load_json_to_table_config()
    
    logger.info("=" * 60)

    extract_json_to_excel(config)