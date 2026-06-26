import logging
# 明确指出只导入 process_pdf_folder
from as_prescreening_helper import process_pdf_folder
from app_configs import load_deepseek_screening_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

if __name__ == "__main__":

    config = load_deepseek_screening_config()
    
    logger.info(f"使用模型: {config.model_name}")
    logger.info("=" * 60)

    # config 现在直接就是 DeepSeekScreeningConfig 类型
    process_pdf_folder(config)