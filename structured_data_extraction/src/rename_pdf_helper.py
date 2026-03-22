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

from rename_pdf_configs import AppConfig

def extract_doi_from_pdf(pdf_path):
    """从 PDF 前两页提取 DOI（清理换行符，支持跨行）"""
    try:
        doc = fitz.open(pdf_path)
        text = ""
        for page_num in range(min(2, doc.page_count)):
            text += doc[page_num].get_text()
        doc.close()

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


def build_new_filename(metadata):
    """根据元数据构建新文件名：年份 - 作者 - 期刊 - 标题前5个单词.pdf"""
    # 1. 获取年份（按优先级）
    year = ""
    pub_print = metadata.get('published-print')
    if pub_print:
        date_parts = pub_print.get('date-parts')
        if date_parts and len(date_parts) > 0 and len(date_parts[0]) > 0:
            year = str(date_parts[0][0])
    if not year:
        pub_online = metadata.get('published-online')
        if pub_online:
            date_parts = pub_online.get('date-parts')
            if date_parts and len(date_parts) > 0 and len(date_parts[0]) > 0:
                year = str(date_parts[0][0])
    if not year:
        created = metadata.get('created')
        if created:
            date_parts = created.get('date-parts')
            if date_parts and len(date_parts) > 0 and len(date_parts[0]) > 0:
                year = str(date_parts[0][0])

    # 2. 获取第一作者姓氏
    author_last_name = ""
    authors = metadata.get('author')
    if authors and isinstance(authors, list) and len(authors) > 0:
        author_last_name = authors[0].get('family', '')

    # 3. 获取期刊名称（优先用简称）
    journal = ""
    short_titles = metadata.get('short-container-title')
    if short_titles and isinstance(short_titles, list) and len(short_titles) > 0:
        journal = short_titles[0]
    if not journal:
        container_titles = metadata.get('container-title')
        if container_titles and isinstance(container_titles, list) and len(container_titles) > 0:
            journal = container_titles[0]

    # 4. 获取标题前5个单词
    title_snippet = ""
    titles = metadata.get('title')
    if titles and isinstance(titles, list) and len(titles) > 0:
        full_title = titles[0]
        words = full_title.split()[:5]
        title_snippet = " ".join(words)

    # 组装文件名
    parts = []
    if year:
        parts.append(year)
    if author_last_name:
        parts.append(author_last_name)
    if journal:
        parts.append(journal)

    base_name = " - ".join(parts)

    if title_snippet:
        if base_name:
            final_name = f"{base_name}-{title_snippet}"
        else:
            final_name = title_snippet
    else:
        final_name = base_name

    if not final_name:
        return None

    return clean_filename(final_name) + ".pdf"


def process_pdf_rename(config: AppConfig):
    """
    执行 PDF 重命名主流程
    :param pdf_dir: PDF 文件所在目录
    :param rate_limit_sleep: API 请求间隔（秒）
    :param api_timeout: API 请求超时（秒）
    :param user_agent: API 请求的 User-Agent
    """
    # 检查目录是否存在
    if not os.path.exists(config.pdf_dir):
        logging.error(f"找不到文件夹: {config.pdf_dir}")
        return

    logging.info(f"开始处理 '{config.pdf_dir}' 中的 PDF 文件...\n")

    failed_files = []

    for filename in os.listdir(config.pdf_dir):
        if not filename.lower().endswith('.pdf'):
            continue

        old_path = os.path.join(config.pdf_dir, filename)

        # 提取 DOI
        logging.warning(f"尝试提取: {filename}的doi")
        doi = extract_doi_from_pdf(old_path)
        if not doi:
            logging.warning(f"未能在前两页找到 DOI，跳过重命名: {filename}")
            failed_files.append(filename)
            continue

        logging.info(f"找到 DOI: {doi}")

        # 获取元数据
        metadata = fetch_metadata_crossref(doi, config.user_agent, config.api_timeout)
        if not metadata:
            logging.warning(f"未能从 Crossref 获取到元数据，跳过重命名: {filename}")
            failed_files.append(filename)
            continue

        # 构建新文件名
        new_filename = build_new_filename(metadata)
        if not new_filename:
            logging.warning(f"元数据字段均为空，无法构建新文件名: {filename}")
            failed_files.append(filename)
            continue

        new_path = os.path.join(config.pdf_dir, new_filename)

        # 防止文件名冲突
        if os.path.exists(new_path) and old_path != new_path:
            name_body, ext = os.path.splitext(new_filename)
            new_filename = f"{name_body}_1{ext}"
            new_path = os.path.join(config.pdf_dir, new_filename)

        # 遵守 API 速率限制
        time.sleep(config.rate_limit_sleep)

        # 执行重命名
        if old_path != new_path:
            try:
                os.rename(old_path, new_path)
                logging.info(f"  成功重命名为: {new_filename}")
            except Exception as e:
                logging.error(f"  重命名失败: {e}")
        else:
            logging.info("文件名已经是标准格式，无需修改。")
        logging.info("-" * 40)

        # 记录未找到 DOI 的文件
        if failed_files:
            no_doi_log = os.path.join(config.pdf_dir, "no_doi.txt")
            with open(no_doi_log, "w", encoding="utf-8") as f:
                for fn in failed_files:
                    f.write(fn + "\n")
            logging.info(f"已将 {len(failed_files)} 个未找到 DOI 的文件名写入 {no_doi_log}")
        else:
            logging.info("所有 PDF 均成功找到 DOI。")