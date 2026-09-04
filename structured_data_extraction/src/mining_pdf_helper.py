import json
import os
import re
import time  # 用于 Jobs 轮询等待
import traceback
from datetime import datetime  # 用于记录失败时间
from pathlib import Path
from typing import List, Any, Dict
import logging

import httpx  # 用于下载 output_url 大结果（SDK 依赖自带）

from openpyxl import Workbook  # 用于生成 .xlsx 失败记录文件 (pip install openpyxl)
from landingai_ade import LandingAIADE
from app_configs import ExtractionConfig

logger = logging.getLogger(__name__)

# ==================== Jobs 轮询配置（Parse Jobs & Extract Jobs 共用） ====================
# 轮询间隔（秒）：向服务端查询任务状态的频率
JOB_POLL_INTERVAL_SECONDS = 5.0
# 单个异步任务最长等待时间（秒）：超时后抛出异常并计入失败记录
JOB_MAX_WAIT_SECONDS = 1800.0  # 30 分钟
# 任务终态集合：出现这些状态时停止轮询
JOB_TERMINAL_STATUSES = ("completed", "failed", "cancelled")
# ===========================================================================================


def save_json_safe(data_obj: Any, filename: str, output_dir: Path):
    """保存数据到本地 JSON，兼容处理各种对象类型"""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    file_path = output_path / filename

    if hasattr(data_obj, "model_dump_json"):
        content = data_obj.model_dump_json(indent=2)
    elif hasattr(data_obj, "json") and callable(getattr(data_obj, "json")):
        content = data_obj.json()
    elif hasattr(data_obj, "to_json"):
        content = data_obj.to_json()
    else:
        content = json.dumps(data_obj, indent=2, ensure_ascii=False, default=str)

    file_path.write_text(content, encoding="utf-8")
    print(f"✅ 数据已保存: {file_path}")


# ==================== 失败记录相关工具函数 ====================
def format_error_reason(e: Exception) -> str:
    """格式化失败原因：异常类型 + 异常信息 + traceback 摘要（做截断，防止超出 Excel 单元格上限）"""
    tb_lines = traceback.format_exc().strip().splitlines()
    tb_summary = "\n".join(tb_lines[-3:]) if tb_lines else ""
    reason = f"[{type(e).__name__}] {e}"
    if tb_summary:
        reason += f"\n--- traceback ---\n{tb_summary}"
    # Excel 单元格内容上限约 32767 字符，这里保守截断
    if len(reason) > 30000:
        reason = reason[:30000] + "\n...(内容过长已截断)"
    return reason


def save_failures_to_xlsx(failure_records: List[Dict[str, Any]], output_path: Path):
    """将失败记录（文件名/失败阶段/失败原因/失败时间）保存为 .xlsx 文件（全量覆盖式写入）"""
    if not failure_records:
        return
    try:
        wb = Workbook()
        ws = wb.active
        ws.title = "失败记录"
        headers = ["序号", "文件名", "失败阶段", "失败原因", "失败时间"]
        ws.append(headers)
        for i, rec in enumerate(failure_records, start=1):
            ws.append([
                i,
                rec.get("文件名", ""),
                rec.get("失败阶段", ""),
                rec.get("失败原因", ""),
                rec.get("失败时间", ""),
            ])
        # 设置列宽便于阅读
        ws.column_dimensions['A'].width = 8
        ws.column_dimensions['B'].width = 45
        ws.column_dimensions['C'].width = 14
        ws.column_dimensions['D'].width = 90
        ws.column_dimensions['E'].width = 22
        wb.save(output_path)
        print(f"💾 失败记录已保存: {output_path}")
    except Exception as save_err:
        print(f"⚠️ 失败记录 .xlsx 保存出错: {save_err}")
# ====================================================================


# ==================== 超时/超限判定工具函数（同步降级用） ====================
def _should_fallback_to_jobs(e: Exception) -> bool:
    """
    判断同步调用异常是否应当降级到异步 Jobs 通道。
    满足其一即降级：
    1. 状态码为 413（请求实体过大 / 超过页数上限）
    2. 状态码为 504（网关超时）或异常类型/消息含 timeout/gateway timeout/timed out
    3. 异常消息中含"页数/size/limit/too large/maximum"等超限相关关键字
    其余错误（鉴权失败、Schema 错误等）不降级，直接抛出走外层失败记录流程。
    """
    status_code = getattr(e, "status_code", None)
    if status_code in (413, 504):
        return True
    msg = f"{type(e).__name__}: {e}".lower()
    timeout_kw = ("504", "gateway timeout", "gateway time-out", "timed out", "timeout")
    limit_kw = ("too large", "maximum", "exceed", "limit", "page", "size", "payload")
    return any(k in msg for k in timeout_kw) or any(k in msg for k in limit_kw)
# ====================================================================


# ==================== Parse Jobs 异步工具函数 ====================
def _run_parse_job_with_polling(
    client: LandingAIADE,
    doc_path: Path,
    model_name: str,
    poll_interval: float = JOB_POLL_INTERVAL_SECONDS,
    max_wait_seconds: float = JOB_MAX_WAIT_SECONDS,
):
    """
    Parse Jobs 异步接口：
    提交大 PDF 解析任务，立即拿到 job_id，轮询直至终态，返回解析结果对象。
    """
    # 1. 提交异步任务，立即拿到 job_id
    job = client.parse_jobs.create(
        document=doc_path,
        model=model_name,
    )
    if isinstance(job, dict):
        job_id = job.get("job_id")
    else:
        job_id = getattr(job, "job_id", None)
    print(f"   ↪️ 已提交 Parse Jobs 异步任务，job_id = {job_id}")

    # 2. 轮询任务状态直至终态
    waited = 0.0
    while True:
        resp = client.parse_jobs.get(job_id)
        if isinstance(resp, dict):
            status = resp.get("status")
            data = resp.get("data")
            output_url = resp.get("output_url")
            error = resp.get("error")
            progress = resp.get("progress")
        else:
            status = getattr(resp, "status", None)
            data = getattr(resp, "data", None)
            output_url = getattr(resp, "output_url", None)
            error = getattr(resp, "error", None)
            progress = getattr(resp, "progress", None)

        progress_pct = f"({progress * 100:.0f}%)" if progress is not None else ""
        print(f"   ⏳ 任务状态: {status}{progress_pct}（已等待 {waited:.0f}s / 上限 {max_wait_seconds:.0f}s）")

        # 3. 终态处理
        if status == "completed":
            if data is not None:
                return data
            if output_url:
                print(f"   ⬇️ 结果较大，正在从 output_url 下载...")
                with httpx.Client(timeout=300.0) as http_client:
                    dl_resp = http_client.get(output_url)
                    dl_resp.raise_for_status()
                try:
                    return dl_resp.json()
                except json.JSONDecodeError:
                    return {"markdown": dl_resp.text}
            raise RuntimeError(f"Parse Job 已完成但未返回结果（job_id={job_id}）")
        if status in ("failed", "cancelled"):
            raise RuntimeError(f"Parse Job 终止于状态 [{status}]，错误信息: {error}（job_id={job_id}）")

        # 4. 非终态：等待后继续轮询
        time.sleep(poll_interval)
        waited += poll_interval
        if waited >= max_wait_seconds:
            raise TimeoutError(
                f"Parse Job 等待超过 {max_wait_seconds:.0f}s 仍未完成（job_id={job_id}），"
                f"任务在服务端仍在运行，可稍后手动查询"
            )
# ====================================================================


# ==================== Extract Jobs 超时降级工具函数 ====================
def _run_extract_job_with_polling(
    client: LandingAIADE,
    schema_json_str: str,
    markdown_content: str,
    model_name: str,
    poll_interval: float = JOB_POLL_INTERVAL_SECONDS,
    max_wait_seconds: float = JOB_MAX_WAIT_SECONDS,
):
    """
    超时降级方案：提交 Extract Jobs 异步任务并轮询直至终态。
    """
    # 1. 提交异步任务
    job = client.extract_jobs.create(
        schema=schema_json_str,
        markdown=markdown_content,
        model=model_name,
    )
    if isinstance(job, dict):
        job_id = job.get("job_id")
    else:
        job_id = getattr(job, "job_id", None)
    print(f"   ↪️ 已提交 Extract Jobs 异步任务，job_id = {job_id}")

    # 2. 轮询任务状态直至终态
    waited = 0.0
    while True:
        job = client.extract_jobs.get(job_id)
        if isinstance(job, dict):
            status = job.get("status")
            result = job.get("result")
            error = job.get("error")
        else:
            status = getattr(job, "status", None)
            result = getattr(job, "result", None)
            error = getattr(job, "error", None)

        print(f"   ⏳ 任务状态: {status}（已等待 {waited:.0f}s / 上限 {max_wait_seconds:.0f}s）")

        # 3. 终态处理
        if status == "completed":
            if result is None:
                raise RuntimeError(f"Extract Job 已完成但未返回结果（job_id={job_id}）")
            return result
        if status in ("failed", "cancelled"):
            raise RuntimeError(f"Extract Job 终止于状态 [{status}]，错误信息: {error}（job_id={job_id}）")

        # 4. 非终态：等待后继续轮询
        time.sleep(poll_interval)
        waited += poll_interval
        if waited >= max_wait_seconds:
            raise TimeoutError(
                f"Extract Job 等待超过 {max_wait_seconds:.0f}s 仍未完成（job_id={job_id}），"
                f"任务在服务端仍在运行，可稍后手动查询"
            )
# ====================================================================


def process_extraction(cfg: ExtractionConfig):
    pdf_input_dir = Path(cfg.pdf_input_folder)

    parsed_subdir = Path(cfg.result_output_folder) / f"_00_parsed"
    extracted_subdir = Path(cfg.result_output_folder) / f"_01_extracted"
    schema_path = Path(cfg.schema_template_path)

    parsed_subdir.mkdir(parents=True, exist_ok=True)
    extracted_subdir.mkdir(parents=True, exist_ok=True)

    print(f"📂 扫描输入文件夹: {pdf_input_dir}")
    print(f"📂 解析输出路径: {parsed_subdir}")
    print(f"📂 提取输出路径: {extracted_subdir}")

    # 失败记录收集器 & xlsx 输出路径
    parse_failures: List[Dict[str, Any]] = []
    extract_failures: List[Dict[str, Any]] = []

    parse_failed_xlsx = parsed_subdir / "parse_failed_records.xlsx"
    extract_failed_xlsx = extracted_subdir / "extract_failed_records.xlsx"

    # --- 3. 加载 LandingAI 专用的 Schema (提取图纸) ---
    if not schema_path.exists():
        print(f"❌ LandingAI Schema 加载失败: 未找到文件 {schema_path}")
        print(f"💡 提示: 请确保您已将 Schema 文件放在正确目录下，并在最下方的 Config 类中填写了正确的文件名。")
        return

    try:
        print(f"📄 加载 LandingAI 提取图纸: {schema_path.name}")
        landingai_schema_dict = json.loads(schema_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ Schema 解析失败: {e}")
        return

    # --- 4. 初始化 LandingAI 客户端 ---
    client = LandingAIADE()

    MODEL_PARSE = cfg.parse_model_name
    MODEL_EXTRACT = cfg.extract_model_name

    # --- 5. 获取所有待处理的 PDF 文件 ---
    pdf_files = list(pdf_input_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"⚠️ 在 {pdf_input_dir} 未找到任何 PDF 文件。请确认上一步的 PDF 筛选脚本已成功运行。")
        return

    print(f"📊 共发现 {len(pdf_files)} 个 PDF 文件，准备开始处理...\n")

    # --- 6. 批量循环处理 ---
    for index, doc_path in enumerate(pdf_files):
        filename = doc_path.name
        print(f"--- [{index+1}/{len(pdf_files)}] 正在检查: {filename} ---")

        expected_parsed_file = parsed_subdir / f"{filename}_parsed.json"
        expected_extracted_file = extracted_subdir / f"{filename}_extracted.json"

        if expected_parsed_file.exists() and expected_extracted_file.exists():
            print(f"⏭️ 文件已完全处理 (已存在)，跳过以节省 API 用量。")
            continue

        markdown_content = None

        # ========== Step A: 解析文档结构 ==========
        try:
            if not expected_parsed_file.exists():
                # 【修改点】：优先尝试同步 Parse 接口，仅当遇到超限/超时类错误时才降级到异步 Jobs
                try:
                    print(f"⚡ [API] 正在尝试同步解析文档 ({MODEL_PARSE})...")
                    # 注意：landingai_ade SDK 中 client.parse 是一个可调用方法，
                    # 正确写法是 client.parse(document=..., model=...)，
                    # 而不是 client.parse.create(...)（后者会抛 AttributeError）。
                    parse_result = client.parse(
                        document=doc_path,
                        model=MODEL_PARSE
                    )
                    print(f" ✅ 同步解析完成。")
                except Exception as sync_parse_err:
                    # 非超限/超时类错误不降级，直接抛出走外层失败记录流程
                    if not _should_fallback_to_jobs(sync_parse_err):
                        raise
                    print(f"   ⚠️ 同步解析失败（原因: {type(sync_parse_err).__name__}: {sync_parse_err}），判定为需要降级")
                    print(f"   ↪️ 自动降级为 Parse Jobs 异步任务处理...")
                    parse_result = _run_parse_job_with_polling(
                        client=client,
                        doc_path=doc_path,
                        model_name=MODEL_PARSE,
                    )
                    print(f" ✅ Parse Jobs 异步任务已完成。")

                # 保存 parsed 结果
                save_json_safe(parse_result, expected_parsed_file.name, str(parsed_subdir))
                print(f" ✅ Parse 结果已保存本地。")

                # 从结果中提取 markdown（兼容对象与 dict 两种形式）
                if hasattr(parse_result, "markdown"):
                    markdown_content = parse_result.markdown
                elif isinstance(parse_result, dict):
                    markdown_content = parse_result.get('markdown', '')
                else:
                    markdown_content = ''
            else:
                print(f" ✅ Parse 文件已存在，加载本地 Markdown 数据...")
                with open(expected_parsed_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                markdown_content = data.get('markdown', '') or data.get('body', {}).get('markdown', '')
        except Exception as e:
            print(f"❌ 处理文件 {filename} 时 Parse 阶段发生致命错误: {e}")
            traceback.print_exc()
            parse_failures.append({
                "文件名": filename,
                "失败阶段": "Parse",
                "失败原因": format_error_reason(e),
                "失败时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
            save_failures_to_xlsx(parse_failures, parse_failed_xlsx)
            continue

        # ========== Step B: 提取核心物性数据 ==========
        try:
            if not expected_extracted_file.exists():
                print(f"⚡ [API] 正在调用 Extract ({MODEL_EXTRACT})...")
                try:
                    extraction_result = client.extract(
                        schema=json.dumps(landingai_schema_dict),
                        markdown=markdown_content,
                        model=MODEL_EXTRACT
                    )
                except Exception as sync_err:
                    if not _should_fallback_to_jobs(sync_err):
                        raise
                    print(f"   ⚠️ 同步 Extract 超时/超限（{type(sync_err).__name__}: {sync_err}）")
                    print(f"   ↪️ 自动降级为 Extract Jobs 异步任务处理...")
                    extraction_result = _run_extract_job_with_polling(
                        client=client,
                        schema_json_str=json.dumps(landingai_schema_dict),
                        markdown_content=markdown_content,
                        model_name=MODEL_EXTRACT,
                    )
                    print(f" ✅ Extract Jobs 异步任务已完成。")

                save_json_safe(extraction_result, expected_extracted_file.name, str(extracted_subdir))
                print(f" ✅ Extract 提取完成，已保存本地。")
            else:
                print(f" ✅ Extract 文件已存在，跳过提取。")
        except Exception as e:
            print(f"❌ 处理文件 {filename} 时 Extract 阶段发生致命错误: {e}")
            traceback.print_exc()
            extract_failures.append({
                "文件名": filename,
                "失败阶段": "Extract",
                "失败原因": format_error_reason(e),
                "失败时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
            save_failures_to_xlsx(extract_failures, extract_failed_xlsx)
            continue

    print("\n🎉 所有的 LandingAI 数据提取任务已全部处理完毕！")

    if parse_failures:
        save_failures_to_xlsx(parse_failures, parse_failed_xlsx)
    if extract_failures:
        save_failures_to_xlsx(extract_failures, extract_failed_xlsx)

    if parse_failures or extract_failures:
        print(f"⚠️ 本次运行共有 {len(parse_failures)} 个文件 Parse 失败、{len(extract_failures)} 个文件 Extract 失败。")
        print(f" 📄 Parse 失败明细: {parse_failed_xlsx}")
        print(f" 📄 Extract 失败明细: {extract_failed_xlsx}")
    else:
        print("✅ 本次运行没有任何失败记录。")
