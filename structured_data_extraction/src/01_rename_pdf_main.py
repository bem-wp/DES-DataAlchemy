import logging

from rename_pdf_configs import load_config
from rename_pdf_helper import *


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


if __name__ == "__main__":
    config = load_config()
    
    process_pdf_rename(config)
    