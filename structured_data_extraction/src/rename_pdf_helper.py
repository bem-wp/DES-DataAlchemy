"""
helper.py
PDF 重命名相关的辅助函数：DOI 提取、Crossref API 调用、文件名清理和构建。
"""
import os
import re
import time
import logging
import requests
import fitz  # PyMuPDF

from app_configs import AppConfig

def extract_doi_from_pdf(pdf_path):
    """从 PDF 前两页提取 DOI（清理换行符，支持跨行）"""
    try:
        with fitz.open(pdf_path) as doc:
            text = ""
            for page_num in range(min(2, doc.page_count)):
                text += doc[page_num].get_text()

        # 清理换行符，防止跨行中断
        text = text.replace('\n', ' ').replace('\r', ' ')

        # 直接匹配纯 DOI
        doi_match = re.search(r'(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)', text)
        if doi_match:
            doi = doi_match.group(1).rstrip('.:;,')
            return doi
    except Exception as e:
        logging.error(f"读取 PDF 出错: {e}")
    return None


def fetch_metadata_crossref(doi, user_agent, timeout=10):
    """通过 Crossref API 获取标准文献元数据"""
    url = f"https://api.crossref.org/works/{doi}"
    headers = {"User-Agent": user_agent}
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
        if response.status_code == 200:
            return response.json().get('message', {})
    except requests.exceptions.RequestException:
        pass
    return {}


def clean_filename(filename):
    """清理文件名中的非法字符，并去掉多余的空格"""
    cleaned = re.sub(r'[\\/*?:"<>|]', "", filename)
    cleaned = re.sub(r'\s+', " ", cleaned)
    return cleaned.strip()


def _get_year(metadata: dict) -> str:
    """提取年份，优先级：published-print > published-online > created"""
    for key in ('published-print', 'published-online', 'created'):
        date_info = metadata.get(key)
        if date_info:
            date_parts = date_info.get('date-parts')
            if date_parts and len(date_parts) > 0 and len(date_parts[0]) > 0:
                return str(date_parts[0][0])
    return ""


def _get_first_author_last_name(metadata: dict) -> str:
    """提取第一作者姓氏"""
    authors = metadata.get('author')
    if authors and isinstance(authors, list) and len(authors) > 0:
        return authors[0].get('family', '')
    return ""


def _get_journal_name(metadata: dict) -> str:
    """提取期刊名称，优先使用简称"""
    short_titles = metadata.get('short-container-title')
    if short_titles and isinstance(short_titles, list) and len(short_titles) > 0:
        return short_titles[0]
    container_titles = metadata.get('container-title')
    if container_titles and isinstance(container_titles, list) and len(container_titles) > 0:
        return container_titles[0]
    return ""


def _get_title_snippet(metadata: dict, max_words: int = 5) -> str:
    """提取标题前 N 个单词"""
    titles = metadata.get('title')
    if titles and isinstance(titles, list) and len(titles) > 0:
        words = titles[0].split()[:max_words]
        return " ".join(words)
    return ""

def extract_filename_fields(metadata: dict) -> dict:
    """
    从 Crossref metadata 中提取构建文件名所需的结构化字段。
    """
    return {
        "year":          _get_year(metadata),
        "author":        _get_first_author_last_name(metadata),
        "journal":       _get_journal_name(metadata),
        "title_snippet": _get_title_snippet(metadata, max_words=5),
    }


def build_new_filename(metadata: dict) -> str | None:
    """
    根据元数据构建新文件名：年份 - 作者 - 期刊 - 标题前5个单词.pdf
    内部调用 extract_filename_fields 完成字段提取，自身只负责格式拼装。
    """
    fields = extract_filename_fields(metadata)

    year          = fields["year"]
    author        = fields["author"]
    journal       = fields["journal"]
    title_snippet = fields["title_snippet"]

    # 组装 "年份 - 作者 - 期刊" 前缀
    parts = [p for p in (year, author, journal) if p]
    base_name = " - ".join(parts)

    # 拼接标题片段
    if title_snippet:
        final_name = f"{base_name}-{title_snippet}" if base_name else title_snippet
    else:
        final_name = base_name

    if not final_name:
        return None

    return clean_filename(final_name) + ".pdf"

def process_pdf_rename(config: AppConfig):
    """执行 PDF 重命名主流程"""
    if not os.path.exists(config.pdf_dir):
        logging.error(f"找不到文件夹: {config.pdf_dir}")
        return

    pdf_files = [f for f in os.listdir(config.pdf_dir) if f.lower().endswith('.pdf')]
    total_pdfs = len(pdf_files)

    if total_pdfs == 0:
        logging.info(f"在目录 '{config.pdf_dir}' 中未找到任何 PDF 文件，跳过处理。")
        return

    logging.info(f"开始处理 '{config.pdf_dir}' 中的 {total_pdfs} 个 PDF 文件...\n")

    failed_files = []
    success_count = 0

    for filename in pdf_files:
        old_path = os.path.join(config.pdf_dir, filename)
        logging.info(f"正在处理: {filename}")

        doi = extract_doi_from_pdf(old_path)
        if not doi:
            logging.warning(f"未能在前两页找到 DOI，跳过重命名: {filename}")
            failed_files.append(filename)
            continue

        logging.info(f"找到 DOI: {doi}")

        time.sleep(config.rate_limit_sleep) 
        
        metadata = fetch_metadata_crossref(doi, config.user_agent, config.api_timeout)
        if not metadata:
            logging.warning(f"未能从 Crossref 获取到元数据，跳过重命名: {filename}")
            failed_files.append(filename)
            continue

        new_filename = build_new_filename(metadata)
        if not new_filename:
            logging.warning(f"元数据字段均为空，无法构建新文件名: {filename}")
            failed_files.append(filename)
            continue

        new_path = os.path.join(config.pdf_dir, new_filename)

        # 处理文件名冲突：递增后缀，直接生成可用的名称
        if old_path != new_path:
            name_body, ext = os.path.splitext(new_filename)
            counter = 1
            while os.path.exists(new_path) and old_path != new_path:
                new_filename = f"{name_body}_{counter}{ext}"
                new_path = os.path.join(config.pdf_dir, new_filename)
                counter += 1

        if old_path != new_path:
            try:
                os.rename(old_path, new_path)
                logging.info(f"  成功重命名为: {new_filename}")
                success_count += 1
            except Exception as e:
                logging.error(f"  重命名失败: {e}")
                failed_files.append(filename)
        else:
            logging.info("  文件名已经是标准格式，无需修改。")
            success_count += 1

        logging.info("-" * 40)
        # time.sleep(config.rate_limit_sleep)

    logging.info("=" * 40)
    if success_count == total_pdfs:
        logging.info(f"处理完成：共 {total_pdfs} 个 PDF，全部处理成功！")
    else:
        logging.info(
            f"处理完成：共 {total_pdfs} 个 PDF，"
            f"成功 {success_count} 个，失败/跳过 {len(failed_files)} 个。"
        )

    if failed_files:
        fail_log_path = os.path.join(config.pdf_dir, "failed_pdfs.txt")
        with open(fail_log_path, "w", encoding="utf-8") as f:
            for fn in failed_files:
                f.write(fn + "\n")
        logging.warning(f"已将 {len(failed_files)} 个未重命名的文件名写入 {fail_log_path}")