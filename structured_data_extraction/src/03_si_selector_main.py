import logging
from si_selector_helper import select_yes_files
from app_configs import load_si_selector_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

if __name__ == "__main__":

    config = load_si_selector_config()
    
    logger.info("=" * 60)

    select_yes_files(config)