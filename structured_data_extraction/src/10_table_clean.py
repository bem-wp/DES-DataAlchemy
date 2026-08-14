import logging
from table_clean_helper import TableCleaner
from app_configs import load_table_clean_config


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

if __name__ == "__main__":

    config = load_table_clean_config()

    logger.info("=" * 60)

    cleaner = TableCleaner(config)
    cleaner.run()
