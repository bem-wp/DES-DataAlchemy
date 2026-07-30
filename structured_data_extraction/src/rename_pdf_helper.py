"""
helper.py
PDF 重命名相关的辅助函数：DOI 提取、Crossref API 调用、文件名清理和构建。
"""
import os
import re
import time
import logging
import requests
import unicodedata
import fitz  # PyMuPDF

from app_configs import PdfRenamerConfig


# 预编译 DOI 正则与黑名单关键字，供所有提取阶段复用
_DOI_REGEX = re.compile(r'(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)')
_EXCLUDE_KEYWORDS = ['zenodo', 'figshare', 'dryad', 'osf.io', 'supp', 'video']


def _search_text_for_doi(text: str) -> str | None:
    """
    在文本块中搜索第一个未被黑名单过滤的有效 DOI。
    清理换行符、去除末尾标点，应用黑名单。
    返回 DOI 字符串，未找到时返回 None。
    """
    text = text.replace('\n', ' ').replace('\r', ' ')
    for match in _DOI_REGEX.findall(text):
        doi = match.rstrip('.:;,)')
        if not any(kw in doi.lower() for kw in _EXCLUDE_KEYWORDS):
            return doi
    return None



def extract_doi_from_pdf(pdf_path):
    """
    多阶段 DOI 提取，按优先级依次尝试：

      1. PDF 元数据 (doc.metadata)  — 处理 Elsevier 的 "doi:10.xxx" 前缀
      2. 首页超链接 (page.get_links) — DOI 常作为可点击链接
      3. 页面文本前 2 页           — get_text() + NFKC 归一化
      4. 页面文本最后 2 页          — 同上
      5. 全页扫描（兜底）           — 逐页搜索

    所有文本路径在正则匹配前均通过 unicodedata.normalize('NFKC', …)
    归一化，将 Unicode 连字（如 U+FB02 "ﬂ"→"fl"）分解为 ASCII。
    黑名单过滤（zenodo/figshare 等）在所有阶段统一应用。
    """
    try:
        with fitz.open(pdf_path) as doc:

            # ————————————————————————————————————————
            # 阶段1：PDF 元数据（覆盖 80% Elsevier 失败）
            # ————————————————————————————————————————
            metadata_text = ' '.join(
                str(v) for v in doc.metadata.values() if v
            )
            if metadata_text:
                metadata_text = unicodedata.normalize('NFKC', metadata_text)

                # 优先匹配 "doi:10.xxx" 显式前缀（Elsevier 惯用法）
                m = re.search(
                    r'\bdoi\s*:\s*(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)',
                    metadata_text, re.IGNORECASE,
                )
                if m:
                    doi_candidate = m.group(1).rstrip('.:;,)')
                    if not any(kw in doi_candidate.lower()
                               for kw in _EXCLUDE_KEYWORDS):
                        return doi_candidate

                # 其次匹配元数据中任意位置的裸 DOI
                doi = _search_text_for_doi(metadata_text)
                if doi:
                    return doi

            # ————————————————————————————————————————
            # 阶段2：首页超链接（前 3 页）
            # ————————————————————————————————————————
            for page_num in range(min(3, doc.page_count)):
                for link in doc[page_num].get_links():
                    uri = link.get('uri', '')
                    if uri:
                        uri = unicodedata.normalize('NFKC', uri)
                        doi = _search_text_for_doi(uri)
                        if doi:
                            return doi

            # ————————————————————————————————————————
            # 阶段3：页面文本 — 前 2 页（含 NFKC 归一化）
            # ————————————————————————————————————————
            text_front = ''
            for page_num in range(min(2, doc.page_count)):
                text_front += doc[page_num].get_text()
            text_front = unicodedata.normalize('NFKC', text_front)
            doi = _search_text_for_doi(text_front)
            if doi:
                return doi

            # ————————————————————————————————————————
            # 阶段4：页面文本 — 最后 2 页
            # ————————————————————————————————————————
            if doc.page_count > 2:
                text_back = ''
                start_page = max(0, doc.page_count - 2)
                for page_num in range(start_page, doc.page_count):
                    text_back += doc[page_num].get_text()
                text_back = unicodedata.normalize('NFKC', text_back)
                doi = _search_text_for_doi(text_back)
                if doi:
                    return doi

            # ————————————————————————————————————————
            # 阶段5：全页扫描（兜底，跳过已查过的页）
            # ————————————————————————————————————————
            if doc.page_count > 4:
                for page_num in range(2, doc.page_count - 2):
                    page_text = doc[page_num].get_text()
                    page_text = unicodedata.normalize('NFKC', page_text)
                    doi = _search_text_for_doi(page_text)
                    if doi:
                        return doi

    except Exception as e:
        logging.error(f"读取 PDF 出错: {e}")
    return None


def extract_doi(pdf_path: str, user_agent: str = None, timeout: int = 10) -> str | None:
    """
    从 PDF 提取 DOI（增强版本地多阶段搜索：元数据、超链接、带 NFKC
    连字归一化的页面文本）。

    返回 DOI 字符串，所有路径均失败时返回 None。

    ``user_agent`` 与 ``timeout`` 参数仅为向后兼容保留，不再消费
    （文件名 PII 回退已移除）。
    """
    return extract_doi_from_pdf(pdf_path)


def fetch_metadata_crossref(doi, user_agent, timeout=10):
    """通过 Crossref API 获取标准文献元数据"""
    url = f"https://api.crossref.org/works/{doi}"
    headers = {"User-Agent": user_agent}
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
        if response.status_code == 200:
            return response.json().get('message', {})
    except requests.exceptions.RequestException as e:
        logging.warning(f"Crossref API 请求失败 (DOI: {doi}): {e}")
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
    根据元数据构建新文件名：年份-作者-期刊-标题前5个单词.pdf
    内部调用 extract_filename_fields 完成字段提取，自身只负责格式拼装。
    """
    fields = extract_filename_fields(metadata)

    year          = fields["year"]
    author        = fields["author"]
    journal       = fields["journal"]
    title_snippet = fields["title_snippet"]

    # 组装 "年份-作者-期刊" 前缀 (去掉了连字符周围的空格)
    parts = [p for p in (year, author, journal) if p]
    base_name = "-".join(parts)

    # 拼接标题片段
    if title_snippet:
        final_name = f"{base_name}-{title_snippet}" if base_name else title_snippet
    else:
        final_name = base_name

    if not final_name:
        return None

    return clean_filename(final_name) + ".pdf"


def remove_duplicate_pdfs(pdf_dir: str) -> None:
    """
    按文件名去重：如果同时存在 xxxx.pdf 和 xxxx_N.pdf（N 为数字），
    则保留 xxxx.pdf，删除所有 xxxx_N.pdf。

    例如：
      paper.pdf + paper_1.pdf + paper_2.pdf  → 保留 paper.pdf，删除 _1 和 _2
      仅有 paper_1.pdf（无 paper.pdf）         → 不做任何操作
    """
    pdf_files = [f for f in os.listdir(pdf_dir) if f.lower().endswith('.pdf')]
    if not pdf_files:
        logging.info("去重：目录中没有 PDF 文件。")
        return

    # 按基础名分组（去除 _N 后缀）
    pattern = re.compile(r'^(.+)_(\d+)\.pdf$', re.IGNORECASE)
    groups: dict[str, list[dict]] = {}

    for filename in pdf_files:
        match = pattern.match(filename)
        if match:
            base_name = match.group(1)
            entry = {
                'filename': filename,
                'has_suffix': True,
                'suffix_num': int(match.group(2)),
            }
        else:
            base_name = filename[:-4]  # strip .pdf
            entry = {
                'filename': filename,
                'has_suffix': False,
                'suffix_num': None,
            }
        groups.setdefault(base_name, []).append(entry)

    # 每组：有无后缀原始文件 → 删除所有 _N 后缀文件
    deleted_count = 0
    for base_name, files in groups.items():
        if len(files) <= 1:
            continue

        unsuffixed = [f for f in files if not f['has_suffix']]
        if not unsuffixed:
            continue  # 无原始文件，不删

        keeper = unsuffixed[0]
        to_delete = [f for f in files if f['has_suffix']]

        logging.info(f"重复分组 [{base_name}] → 保留: {keeper['filename']}")
        for file_info in to_delete:
            file_path = os.path.join(pdf_dir, file_info['filename'])
            try:
                os.remove(file_path)
                logging.info(f"  已删除: {file_info['filename']}")
                deleted_count += 1
            except Exception as e:
                logging.error(f"  删除失败 {file_info['filename']}: {e}")

    if deleted_count > 0:
        logging.info(f"去重完成：删除 {deleted_count} 个带 _N 后缀的重复文件。")
    else:
        logging.info("去重完成：未发现需要清理的 _N 后缀重复文件。")


def process_pdf_rename(config: PdfRenamerConfig):
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

        doi = extract_doi(old_path, config.user_agent, config.api_timeout)
        if not doi:
            logging.warning(f"未能找到有效 DOI，跳过重命名: {filename}")
            failed_files.append(filename)
            continue

        logging.info(f"找到 DOI: {doi}")

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
            while os.path.exists(new_path) and old_path != new_path and counter <= 999:
                new_filename = f"{name_body}_{counter}{ext}"
                new_path = os.path.join(config.pdf_dir, new_filename)
                counter += 1
            if counter > 999:
                logging.warning(f"  文件名冲突过多（>999），无法自动解决: {filename}")
                failed_files.append(filename)
                continue

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
        time.sleep(config.rate_limit_sleep)

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

    logging.info("开始清理重复文件...")
    remove_duplicate_pdfs(config.pdf_dir)