import csv
import json
import logging
import os
import re
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from threading import Lock, Event
from dataclasses import dataclass, field

import httpx
import fitz  # PyMuPDF for PDF text extraction
from openai import OpenAI, APIConnectionError, APITimeoutError, RateLimitError
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from app_configs import AsExtractionConfig

logger = logging.getLogger(__name__)

# ==========================================
# CSV 读写工具函数
# ==========================================

def read_csv_as_dict(csv_path):
    """读取CSV文件，以第一列（文件名）为键，返回有序字典"""
    # 用于 断点续跑 ：加载已处理的CSV记录，避免重复处理
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

# ==========================================
# 多线程csv管理类
# ==========================================
class ThreadSafeCSVManager:

    def __init__(self, csv_path: str, header: list[str]):
        self.csv_path = csv_path
        self.header = header
        self._lock = Lock()

        existing = read_csv_as_dict(csv_path)
        self._records: OrderedDict = existing          
        self.handled_set: set[str] = set(existing.keys())

    def is_handled(self, key: str) -> bool:
        return key in self.handled_set

    def write_record(self, row: list) -> None:
 
        key = row[0]
        with self._lock:
            is_new = key not in self._records
            self._records[key] = row
            self.handled_set.add(key)

            if is_new:
                self._append_to_file(row)
                logger.debug(f"  CSV 已追加（append）: {key}")
            else:
                write_csv_from_dict(self.csv_path, self._records, header=self.header)
                logger.debug(f"  CSV 已更新（upsert）: {key}")

    def _append_to_file(self, row: list) -> None:

        file_exists = (
            os.path.exists(self.csv_path)
            and os.path.getsize(self.csv_path) > 0
        )
        with open(self.csv_path, "a", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(self.header)
            writer.writerow(row)


@dataclass
class ThreadSafeCounters:
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)
    skipped: int = 0
    has_data: int = 0
    no_data: int = 0
    failed: int = 0

    def increment(self, key: str) -> None:
        with self._lock:
            setattr(self, key, getattr(self, key) + 1)


# ==========================================
# 线程独立client
# ==========================================

def make_client(config: AsExtractionConfig) -> OpenAI:

    http_client = httpx.Client(
        proxy=None,
        timeout=httpx.Timeout(config.timeout, connect=10.0),
    )
    return OpenAI(
        api_key=config.api_key,
        base_url=config.model_base_url,
        http_client=http_client,
    )

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


def extract_pdf_text(file_path: str, output_folder: str = None) -> str:
    """提取PDF文件中的文本内容
    
    Args:
        file_path: PDF文件路径
        output_folder: 可选，文本输出目录（用于保存提取的文本）
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"文件不存在: {file_path}")

    logger.info(f"提取PDF文本: {os.path.basename(file_path)}")
    text = ""
    try:
        with fitz.open(file_path) as doc:
            for page in doc:
                text += page.get_text()
    except Exception as e:
        raise RuntimeError(f"PDF提取失败: {e}")
    
    if output_folder:
        os.makedirs(output_folder, exist_ok=True)
        txt_filename = Path(file_path).stem + ".txt"
        txt_path = os.path.join(output_folder, txt_filename)
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(text)
        logger.info(f"  文本已保存: {txt_filename}")
    
    return text


def clean_response_text(text):
    """清理 LLM 返回文本中的 <think> 标签和 Markdown 代码块标记"""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = re.sub(r"```", "", text)
    return text.strip()


@retry(**_RETRY_POLICY)
def run_workflow(client: OpenAI, file_id: str, prompt: str, config: AsExtractionConfig, pdf_text: str = None) -> str | None:
    """调用LLM对PDF内容执行提取任务
    
    Args:
        prompt: 从prompt.md加载的完整system prompt内容
        pdf_text: 如果提供，则直接使用文本内容（用于不支持文件上传的API如DeepSeek）
    """

    logger.info(f"  调用模型中 (直接文本输入, timeout={config.timeout}s)")
    completion = client.chat.completions.create(
        model=config.model_name,
        messages=[
            {"role": "system", "content": prompt},  # prompt.md的完整内容作为system message
            {"role": "user", "content": f"请分析以下PDF文本内容，按照要求提取DES配方和应用场景，仅输出JSON格式结果。\n\n{pdf_text}"},
        ],
        reasoning_effort="high",  # 启用思考模式，提高推理能力
        extra_body={"thinking": {"type": "enabled"}},  # 启用思考模式
    )

    if not completion.choices:
        logger.warning("API 返回空 choices")
        return None, None

    message = completion.choices[0].message
    content = message.content
    reasoning_content = getattr(message, 'reasoning_content', None)  # 获取思考过程
    
    # 记录思考过程到日志
    if reasoning_content:
        logger.debug(f"  思考过程: {reasoning_content[:200]}...")

    return content, reasoning_content

# ==========================================
# 结果判断
# ==========================================

NEGATIVE_KEYWORDS = {"NO", "无", "NONE", "NULL", "FALSE", "N/A", "不确定"}
POSITIVE_KEYWORDS = {"YES", "有", "TRUE"}

def is_no_data(content):
    """判断 LLM 返回内容是否表示'无数据'"""
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
            # 检查是否包含 error: 不确定（prompt规定的无数据格式）
            if "error" in data and data["error"] == "不确定":
                return True
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
# 单文件处理任务
# ==========================================

def _process_single_task(
    pdf_file: Path,
    index: int,
    total: int,
    config: AsExtractionConfig,
    prompt: str,
    csv_manager: ThreadSafeCSVManager,
    counters: ThreadSafeCounters,
    skip_existing: bool,
    stop_event: Event,
) -> None:
    
    if stop_event.is_set():
        return

    filename = pdf_file.name
    logger.info(f"[{index}/{total}] {filename}")

    if skip_existing and csv_manager.is_handled(filename):
        logger.info(f"  已处理，跳过")
        counters.increment("skipped")
        return

    client = make_client(config)

    has_data = "处理失败"
    note = "未知错误"
    raw_response = ""
    reasoning_content = ""  # 新增：用于存储思考过程
    des_formula = ""
    application = ""

    try:
        # DeepSeek处理流程：提取PDF文本 -> 调用API
        txt_output_folder = getattr(config, 'pdf_to_txt_log', None)
        pdf_text = extract_pdf_text(str(pdf_file), output_folder=txt_output_folder)
        result, reasoning = run_workflow(client, None, prompt, config, pdf_text=pdf_text)
        if reasoning:
            reasoning_content = reasoning

        if not result:
            has_data, note = "处理失败", "工作流执行失败"
            counters.increment("failed")
        else:
            raw_response = result
            result = clean_response_text(result)
            logger.debug(f"  提取结果: {result[:200]}")
            # 记录思考过程到日志
            if reasoning_content:
                logger.info(f"  思考过程: {reasoning_content[:100]}...")

            if is_no_data(result):
                logger.info(f"  结果: 无相关数据")
                has_data, note = "无", "无相关数据"
                counters.increment("no_data")
            else:
                # 解析JSON提取DES配方和应用场景
                try:
                    json_data = json.loads(result)
                    des_formula = json_data.get("DES_formula", "")
                    application = json_data.get("application", "")
                except json.JSONDecodeError:
                    logger.warning(f"  JSON解析失败: {result[:100]}")
                
                logger.info(f"  结果: 有相关数据")
                has_data, note = "有", "有相关数据"
                counters.increment("has_data")

    except Exception as e:
        logger.error(f"  处理异常: {e}", exc_info=True)
        has_data, note = "处理失败", f"错误: {e}"
        counters.increment("failed")

    row = [
        filename,
        has_data,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        note,
        des_formula,
        application,
        reasoning_content,  # 新增：思考过程
        raw_response,
    ]
    csv_manager.write_record(row)


# ==========================================
# 主处理流程
# ==========================================

def process_pdf_folder(config: AsExtractionConfig, skip_existing: bool = True) -> None:
    csv_path = config.output_csv
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)

    prompt = load_prompt_template(config.prompt_templates_path)
    if not prompt:
        logger.error("prompt 模板为空，终止处理")
        return

    pdf_files = sorted(Path(config.pdf_input_folder).glob("*.pdf"))
    if not pdf_files:
        logger.warning(f"在文件夹 {config.pdf_input_folder} 中没有找到PDF文件")
        return

    total = len(pdf_files)
    logger.info(f"找到 {total} 个PDF文件")

    csv_headers = ["文件名", "数据状态", "处理时间", "备注", "DES配方", "应用场景", "思考过程", "LLM原始回复"]
    csv_manager = ThreadSafeCSVManager(csv_path, csv_headers)
    counters = ThreadSafeCounters()
    stop_event = Event()

    if csv_manager.handled_set:
        logger.info(f"已有 {len(csv_manager.handled_set)} 条历史记录")

    max_workers = getattr(config, "max_workers", 4)
    logger.info(f"启动线程池，max_workers={max_workers}")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _process_single_task,
                pdf_file,
                i,
                total,
                config,
                prompt,
                csv_manager,
                counters,
                skip_existing,
                stop_event,
            ): pdf_file
            for i, pdf_file in enumerate(pdf_files, 1)
        }

        for future in as_completed(futures):
            pdf_file = futures[future]
            try:
                future.result()
            except Exception as e:
                logger.error(f"线程异常 [{pdf_file.name}]: {e}", exc_info=True)
                stop_event.set()  

    processed = total - counters.skipped
    logger.info(
        f"处理完成 | 总计: {total} | 跳过: {counters.skipped} | "
        f"实际处理: {processed} | "
        f"有数据: {counters.has_data} | 无数据: {counters.no_data} | "
        f"失败: {counters.failed}"
    )
    logger.info(f"CSV 路径: {csv_path}")