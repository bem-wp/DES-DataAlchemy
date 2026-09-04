#!/usr/bin/env python3
"""
OpenAI 文件提取脚本（带自动清理）
逐文件上传 → 提取内容 → 立即删除，避免文件数量超限。
支持从目录批量处理，并记录处理进度。
"""

import os
import time
import logging
from pathlib import Path
from typing import List, Optional

from openai import OpenAI
from openai.types import FileObject

# ---------- 配置 ----------
OPENAI_API_KEY = 'sk-ws-H.PMEXLLP.GFDa.MEUCIE17COG9OpogW2pzdGAhqp7__GKCIMNGUhgmt6wz_PARAiEA8O2ZEARj3b74Bvz89mQBC9-1Qtyu2dstIchffijylAA' # 从环境变量读取
if not OPENAI_API_KEY:
    raise ValueError("请设置环境变量 OPENAI_API_KEY")

INPUT_DIR = Path(r"D:\envs\codes\DES-DataAlchemy\data\input")        # 待处理文件所在目录
LOG_FILE = Path("./processing.log") # 日志文件
SUPPORTED_EXT = (".pdf",)           # 可扩展其他格式

# 限制每次请求间隔（秒），避免速率限制
REQUEST_DELAY = 0.5
# 当超限时，等待秒数后重试
RETRY_DELAY = 5

# ---------- 日志设置 ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("file_processor")

# ---------- OpenAI 客户端 ----------
client = OpenAI(api_key=OPENAI_API_KEY)

# ---------- 工具函数 ----------
def get_file_list(directory: Path) -> List[Path]:
    """获取指定目录下所有支持的文件路径（按名称排序）"""
    files = []
    for ext in SUPPORTED_EXT:
        files.extend(directory.glob(f"*{ext}"))
    return sorted(files)

def delete_all_files() -> None:
    """删除当前 OpenAI 账户下的所有文件（谨慎使用）"""
    try:
        files = client.files.list()
        if not files.data:
            logger.info("当前没有文件需要删除。")
            return
        for f in files.data:
            logger.info(f"删除旧文件: {f.id} ({f.filename})")
            client.files.delete(f.id)
            time.sleep(0.2)  # 避免删除过快
        logger.info(f"已删除 {len(files.data)} 个文件。")
    except Exception as e:
        logger.error(f"删除文件时出错: {e}")

def process_single_file(file_path: Path) -> Optional[str]:
    """
    处理单个文件：
      1. 上传（purpose='file-extract'）
      2. 提取内容（此处仅为示例，可根据实际需求替换）
      3. 删除文件
    返回提取的内容（或 None 表示失败）
    """
    file_id = None
    try:
        # 1. 上传
        logger.info(f"上传文件: {file_path.name}")
        with open(file_path, "rb") as f:
            uploaded: FileObject = client.files.create(
                file=f,
                purpose="file-extract"
            )
        file_id = uploaded.id
        logger.info(f"上传成功，file_id: {file_id}")

        # 2. 提取内容（示例：获取文件内容，实际可能用于后续处理）
        #    注意：OpenAI 的 files.content 接口返回的是二进制内容，此处仅作演示。
        #    如果您的目的是将文件内容作为上下文传给模型，可在此处调用其他 API。
        content_response = client.files.content(file_id)
        content = content_response.text  # 对于文本文件可用 .text，PDF 可能返回 base64 或原始数据
        # 若需处理 PDF，可使用 PyPDF2 等库，但这里只做演示
        logger.info(f"文件 {file_path.name} 提取内容长度: {len(content)} 字符")

        # ---- 在这里插入您实际的业务逻辑 ----
        # 例如：将 content 存入数据库、调用 GPT 分析等
        # ------------------------------------

        # 3. 处理完毕后立即删除
        logger.info(f"删除文件: {file_id}")
        client.files.delete(file_id)
        file_id = None  # 标记已删除
        return content

    except Exception as e:
        logger.error(f"处理文件 {file_path.name} 失败: {e}")
        # 若上传成功但后续出错，尝试删除已上传文件
        if file_id:
            try:
                client.files.delete(file_id)
                logger.info(f"已清理残留文件: {file_id}")
            except Exception as del_err:
                logger.error(f"清理残留文件失败: {del_err}")
        return None

def main():
    """主流程"""
    # 获取待处理文件列表
    files = get_file_list(INPUT_DIR)
    total = len(files)
    if total == 0:
        logger.warning(f"目录 {INPUT_DIR} 中没有找到支持的文件。")
        return

    logger.info(f"共发现 {total} 个文件待处理。")

    # 可在此处预留一个"强制清理"开关，用于启动时清空旧文件（谨慎）
    # delete_all_files()   # 取消注释可清空所有旧文件

    processed = 0
    failed = []

    for idx, file_path in enumerate(files, start=1):
        logger.info(f"[{idx}/{total}] 开始处理: {file_path.name}")
        try:
            result = process_single_file(file_path)
            if result is not None:
                processed += 1
                logger.info(f"[{idx}/{total}] 完成: {file_path.name}")
            else:
                failed.append(file_path.name)
        except Exception as e:
            logger.error(f"[{idx}/{total}] 未预期的错误: {e}")
            failed.append(file_path.name)

        # 请求间隔，避免速率限制
        time.sleep(REQUEST_DELAY)

        # 如果发生超限错误，尝试重试（已在 process_single_file 内部捕获）
        # 但若外部仍需全局清理，可在此处检查

    # 输出总结
    logger.info("=" * 50)
    logger.info(f"处理完成。成功: {processed}, 失败: {len(failed)}")
    if failed:
        logger.warning(f"失败文件列表: {', '.join(failed)}")
    logger.info("=" * 50)

if __name__ == "__main__":
    # 可添加重试循环，当遇到文件数量超限时，清空所有文件后重新尝试
    max_global_retries = 3
    for attempt in range(1, max_global_retries + 1):
        try:
            main()
            break  # 正常完成则退出
        except Exception as e:
            if "Upload file number exceed limit" in str(e):
                logger.error(f"文件数量超限，尝试清空所有文件 (第 {attempt} 次重试)...")
                delete_all_files()
                time.sleep(RETRY_DELAY)
                if attempt == max_global_retries:
                    logger.critical("达到最大重试次数，程序退出。")
                    raise
            else:
                raise