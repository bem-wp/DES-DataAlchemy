import csv
import json
import logging
import os
import re
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

import httpx
from openai import OpenAI, APIConnectionError, APITimeoutError, RateLimitError
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from llm_configs import AppConfig

logger = logging.getLogger(__name__)

# ==========================================
# CSV 读写工具函数
# ==========================================

def read_csv_as_dict(csv_path):
    """读取CSV文件，以第一列（文件名）为键，返回有序字典"""
    records = OrderedDict()
    if os.path.exists(csv_path):
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if header:
                for row in reader:
                    if row:
                        key = row[0]
                        records[key] = row
    return records


def write_csv_from_dict(csv_path, records, header=None):
    """将有序字典写回CSV文件"""
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        if header:
            writer.writerow(header)
        for row in records.values():
            writer.writerow(row)


def append_csv_record(csv_path, record, header=None):
    """
    追加一条新记录到 CSV 文件末尾。

    如果文件不存在或为空，会先写入表头再追加记录。
    仅用于确认该记录不在文件中的场景（新增），避免全量读写。
    """
    file_exists = os.path.exists(csv_path) and os.path.getsize(csv_path) > 0
    with open(csv_path, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        if not file_exists and header:
            writer.writerow(header)
        writer.writerow(record)


def upsert_csv_record(csv_path, record, header=None):
    """
    更新或插入一条 CSV 记录（以 record[0] 为主键）。
    会全量读取再全量写入，仅用于需要覆盖已有记录的场景（更新）。
    """
    records = read_csv_as_dict(csv_path)
    key = record[0]
    records[key] = record
    write_csv_from_dict(csv_path, records, header=header)


# ==========================================
# Prompt 与 LLM 交互
# ==========================================

def load_prompt_template(template_path):
    """加载提示词模板文件"""
    if not os.path.exists(template_path):
        raise FileNotFoundError(f"提示模板未找到: {template_path}")
    
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()



_RETRY_POLICY = dict(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type(
        (
            APIConnectionError,
            APITimeoutError,
            RateLimitError,
            RuntimeError,
            httpx.ReadTimeout,      
            httpx.ConnectTimeout,   
        )
    ),
    before_sleep=before_sleep_log(logger, logging.WARNING),
)


@retry(**_RETRY_POLICY)
def upload_file(client: OpenAI, file_path: str):
    """上传 PDF 文件到 Qwen Long，返回 file_id"""
    if not os.path.exists(file_path):
        logger.error(f"文件不存在: {file_path}")
        raise FileNotFoundError(f"文件不存在: {file_path}")

    logger.info(f"上传文件中: {os.path.basename(file_path)}")
    file_object = client.files.create(
        file=Path(file_path), purpose="file-extract" 
    )

    if not file_object or not file_object.id:
        logger.error(f"文件上传失败: {os.path.basename(file_path)}")
        raise RuntimeError(f"文件上传失败: {os.path.basename(file_path)}")

    return file_object.id


def clean_response_text(text):
    """清理 LLM 返回文本中的 <think> 标签和 Markdown 代码块标记"""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = re.sub(r"```", "", text)
    return text.strip()


@retry(**_RETRY_POLICY)
def run_workflow(
    client: OpenAI, file_id: str, prompt: str, config: AppConfig
) -> str | None:
    """调用 Qwen Long 对已上传的文件执行提取任务"""
    logger.info(f"  调用模型中 (file_id={file_id[:16]}..., timeout={config.timeout}s)")
    completion = client.chat.completions.create(
        model=config.model_name,
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "system", "content": f"fileid://{file_id}"},
            {"role": "user", "content": prompt},
        ],
    )

    if not completion.choices:
        logger.warning("API 返回空 choices")
        return None
    
    return completion.choices[0].message.content


# ==========================================
# 结果判断
# ==========================================

NEGATIVE_KEYWORDS = {
    "NO", "无", "NONE", "NULL", "FALSE","N/A"
}
POSITIVE_KEYWORDS = {"YES", "有", "TRUE"}


def is_no_data(content):
    """
    判断 LLM 返回内容是否表示'无数据'。

    判断优先级：
      1. 空内容 -> True（无数据）
      2. 如果是文本，且完全匹配否定关键词 -> True（无数据）
      3. 如果是文本，且完全匹配肯定关键词 -> False（有数据）
      4. 如果是JSON格式，收集所有字符串值进行匹配（肯定优先策略）：
         - 任一值匹配肯定关键词 -> False（有数据）
         - 无肯定匹配，且任一值匹配否定关键词 -> True（无数据）
      5. 无法确定 -> False（保守策略，不漏掉可能有数据的PDF）
    """
    if not content:
        return True

    content_stripped = content.strip()
    content_upper = content_stripped.upper()

    if content_upper in NEGATIVE_KEYWORDS:
        return True
    if content_upper in POSITIVE_KEYWORDS:
        return False

    try:
        data = json.loads(content_stripped)
        if isinstance(data, dict):
            str_values = [
                v.strip().upper() for v in data.values() if isinstance(v, str)
            ]
            if any(v in POSITIVE_KEYWORDS for v in str_values):
                return False       
            if any(v in NEGATIVE_KEYWORDS for v in str_values):
                return True        
    except json.JSONDecodeError:
        pass

    return False


# ==========================================
# 主处理流程
# ==========================================


def process_pdf_folder(config: AppConfig, skip_existing=True):
    """
    遍历 PDF 文件夹，逐个上传并调用 Qwen Long 进行预筛选，
    结果写入 CSV。

    CSV 写入策略：
      - 全新文件：append 追加
      - 已有记录需更新：upsert 全量重写

    """
    csv_path = config.output_csv
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)

    http_client = httpx.Client(
        proxy=None,
        timeout=httpx.Timeout(config.timeout, connect=10.0),
    )
    client = OpenAI(
        api_key=config.api_key,
        base_url=config.model_base_url,
        http_client=http_client,
    )
    prompt = load_prompt_template(config.prompt_templates_path)
    if not prompt:
        logger.error("prompt 模板为空，终止处理")
        return

    pdf_files = sorted(Path(config.pdf_input_folder).glob("*.pdf"))
    if not pdf_files:
        logger.warning(
            f"在文件夹 {config.pdf_input_folder} 中没有找到PDF文件"
        )
        return

    total = len(pdf_files)
    logger.info(f"找到 {total} 个PDF文件")

    csv_headers = ["文件名", "数据状态", "处理时间", "备注", "LLM原始回复"]

    existing_records = read_csv_as_dict(csv_path)
    handled_set = set(existing_records.keys())
    if handled_set:
        logger.info(f"已有 {len(handled_set)} 条历史记录")

    counters = {"skipped": 0, "has_data": 0, "no_data": 0, "failed": 0}

    for i, pdf_file in enumerate(pdf_files, 1):
        filename = pdf_file.name
        logger.info(f"[{i}/{total}] {filename}")

        if skip_existing and filename in handled_set:
            logger.info(f"  已处理，跳过")
            counters["skipped"] += 1
            continue

        has_data = "处理失败"
        note = "未知错误"
        raw_response = ""

        try:
            file_id = upload_file(client, str(pdf_file))
            result = run_workflow(client, file_id, prompt, config)
            if not result:
                has_data, note = "处理失败", "工作流执行失败"
                counters["failed"] += 1
            else:
                raw_response = result
                result = clean_response_text(result)
                logger.debug(f"  提取结果: {result[:200]}")

                if is_no_data(result):
                    logger.info(f"  结果: 无相关数据")
                    has_data, note = "无", "无相关数据"
                    counters["no_data"] += 1
                else:
                    logger.info(f"  结果: 有相关数据")
                    has_data, note = "有", "有相关数据"
                    counters["has_data"] += 1

        except Exception as e:
            logger.error(f"  处理异常: {e}", exc_info=True)
            has_data, note = "处理失败", f"错误: {e}"
            counters["failed"] += 1

        row = [
            filename,
            has_data,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            note,
            raw_response,
        ]

        if filename in handled_set:
            upsert_csv_record(csv_path, row, header=csv_headers)
            logger.debug(f"  CSV 已更新（upsert）: {filename}")
        else:
            append_csv_record(csv_path, row, header=csv_headers)
            handled_set.add(filename)
            logger.debug(f"  CSV 已追加（append）: {filename}")

    processed = total - counters["skipped"]
    logger.info(
        f"处理完成 | 总计: {total} | 跳过: {counters['skipped']} | "
        f"实际处理: {processed} | "
        f"有数据: {counters['has_data']} | 无数据: {counters['no_data']} | "
        f"失败: {counters['failed']}"
    )
    logger.info(f"CSV 路径: {csv_path}")
