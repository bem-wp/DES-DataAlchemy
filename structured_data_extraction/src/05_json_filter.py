import logging
from json_filter_helper import process_filtering
from app_configs import load_json_filter_config


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

if __name__ == "__main__":

    config = load_json_filter_config()
    
    logger.info("=" * 60)

    process_filtering(config)