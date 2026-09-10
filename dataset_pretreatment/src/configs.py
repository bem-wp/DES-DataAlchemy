import os
import sys
import json
import logging
from datetime import datetime
from pathlib import Path

# --- 日志配置 ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent # 定位根目录

CONFIG_FILE = BASE_DIR / 'configs' / 'pretreatment_config.json'


def _load_raw_config(config_path: Path) -> dict:
    """读取 JSON 配置，失败时返回空字典而不是抛异常。"""
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning(f"配置文件未找到: {config_path}")
    except json.JSONDecodeError as e:
        logger.error(f"配置文件 JSON 解析失败: {config_path} -> {e}")
    except Exception as e:
        logger.error(f"读取配置文件异常: {config_path} -> {e}")
    return {}


def _setup_proxy(proxy_url: str) -> None:
    """根据配置设置代理环境变量。"""
    proxy_url = (proxy_url or "").strip()
    if proxy_url:
        os.environ["http_proxy"] = proxy_url
        os.environ["https_proxy"] = proxy_url
        logger.info(f"已成功配置网络代理: {proxy_url}")
    else:
        logger.info("未配置代理或代理为空，直连网络。")


class MakeIdsMapTableConfig:
    # ---------- 时间戳 ----------
    TIMESTAMP = datetime.now().strftime('%y%m%d')

    # ---------- 目录 ----------
    IN_DIR      = str(BASE_DIR / 'data' / 'input')
    IN_OUT_DIR  = str(BASE_DIR / 'data' / 'in_out')
    OUT_DIR     = str(BASE_DIR / 'data' / 'output')
    TEMP_DIR    = str(BASE_DIR / 'tmp_data')

    # ---------- 配置文件 ----------
    config_path = str(CONFIG_FILE)

    # ---------- 读取配置（类体执行一次） ----------
    _raw_config = _load_raw_config(CONFIG_FILE)

    # 默认值，防止文件读取失败时报错
    _raw_dataset_name = _raw_config.get("RAW_DATASET_NAME", "default_dataset.csv")

    # 代理设置
    _setup_proxy(_raw_config.get("PROXY_URL", ""))

    # ---------- 输入文件路径 ----------
    RAW_DATASET = os.path.join(IN_DIR, _raw_dataset_name)

    # 不含扩展名的基础文件名 (例如 "data.csv" -> "data")
    _base_name = os.path.splitext(_raw_dataset_name)[0]

    # ---------- 中间产物目录 ----------
    MISSING_DATA_MAG_DIR = os.path.join(TEMP_DIR, _base_name, 'mis_data_mag')
    DEDUPLICATE_DIR      = os.path.join(TEMP_DIR, _base_name, 'deduplicate')

    # ---------- 输出文件路径 ----------
    IDS_MAP_TABLE      = os.path.join(IN_OUT_DIR, f'{_base_name}_{TIMESTAMP}_raw-ds_fetch-ids_add-ids.xlsx')
    # IDS_MAP_TABLE_OUT  = os.path.join(OUT_DIR,    f'{_base_name}_{TIMESTAMP}_out_ids-map-tb.xlsx')

    BYPRODUCT_TABLE    = os.path.join(IN_OUT_DIR, f'{_base_name}_{TIMESTAMP}_ids-map-tb_dd_fg.xlsx')
    # BYPRODUCT_TABLE_OUT= os.path.join(OUT_DIR,    f'{_base_name}_{TIMESTAMP}_out_byproduct-tb.xlsx')