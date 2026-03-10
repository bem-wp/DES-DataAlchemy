import os
import re
import requests
import fitz  # PyMuPDF

# 配置文件夹名称
PDF_DIR = "pdf_repo"

def extract_doi_from_pdf(pdf_path):
    """从 PDF 前两页提取 DOI"""
    try:
        # 打开 PDF 文件
        doc = fitz.open(pdf_path)
        text = ""
        # 只提取前两页的文本，加快速度且 DOI 通常在首页
        for page_num in range(min(2, doc.page_count)):
            text += doc[page_num].get_text()
        doc.close()

        # 正则表达式匹配 DOI (10.xxxx/xxxx)
        doi_match = re.search(r'(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)', text)
        if doi_match:
            # 去除可能黏在末尾的非法标点
            doi = doi_match.group(1).rstrip('.:;,')
            return doi
    except Exception as e:
        print(f"  ❌ 读取 PDF 出错: {e}")
    return None

def fetch_metadata_crossref(doi):
    """通过 Crossref API 获取标准文献元数据"""
    url = f"https://api.crossref.org/works/{doi}"
    headers = {
        "User-Agent": "PDFRenamerBot/1.0 (mailto:your_email@example.com)" # API 要求礼貌提供 UA
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            return response.json().get('message', {})
    except requests.exceptions.RequestException:
        pass
    return {}

def clean_filename(filename):
    """清理文件名中的非法字符，并去掉多余的空格"""
    # 替换 Windows/Mac 文件系统不允许的字符
    cleaned = re.sub(r'[\\/*?:"<>|]', "", filename)
    # 替换换行符等不可见字符
    cleaned = re.sub(r'\s+', " ", cleaned)
    return cleaned.strip()

def build_new_filename(metadata):
    """根据公式构建新文件名：[年份] - [第一作者姓氏] - [期刊简称/全称]-[标题前5个单词].pdf"""
    # 1. 获取年份 (Published-print 或 published-online)
    year = ""
    if 'published-print' in metadata:
        year = str(metadata['published-print']['date-parts'][0][0])
    elif 'published-online' in metadata:
        year = str(metadata['published-online']['date-parts'][0][0])
    elif 'created' in metadata:
        year = str(metadata['created']['date-parts'][0][0])

    # 2. 获取第一作者姓氏
    author_last_name = ""
    if 'author' in metadata and len(metadata['author']) > 0:
        author_last_name = metadata['author'][0].get('family', '')

    # 3. 获取期刊名称 (优先用简称，没有则用全称)
    journal = ""
    if 'short-container-title' in metadata and metadata['short-container-title']:
        journal = metadata['short-container-title'][0]
    elif 'container-title' in metadata and metadata['container-title']:
        journal = metadata['container-title'][0]

    # 4. 获取标题前5个单词
    title_snippet = ""
    if 'title' in metadata and metadata['title']:
        full_title = metadata['title'][0]
        # 用空格分割，取前5个单词
        words = full_title.split()[:5]
        title_snippet = " ".join(words)

    # 组装文件名逻辑（处理缺失字段留空的情况）
    parts = []
    if year: parts.append(year)
    if author_last_name: parts.append(author_last_name)
    if journal: parts.append(journal)
    
    # 用 " - " 连接前三个要素
    base_name = " - ".join(parts)
    
    # 加上标题部分（用 "-" 连接）
    if title_snippet:
        if base_name:
            final_name = f"{base_name}-{title_snippet}"
        else:
            final_name = title_snippet
    else:
        final_name = base_name

    # 如果所有数据都抓取失败，返回 None
    if not final_name:
        return None

    return clean_filename(final_name) + ".pdf"

def main():
    # 确保文件夹存在
    if not os.path.exists(PDF_DIR):
        print(f"❌ 找不到文件夹: {PDF_DIR}，请确保它与本脚本在同一目录下。")
        return

    print(f"📁 开始处理 '{PDF_DIR}' 中的 PDF 文件...\n")
    
    for filename in os.listdir(PDF_DIR):
        if not filename.lower().endswith('.pdf'):
            continue
            
        old_path = os.path.join(PDF_DIR, filename)
        print(f"📄 正在处理: {filename}")
        
        # 1. 提取 DOI
        doi = extract_doi_from_pdf(old_path)
        if not doi:
            print("  ⚠️ 未能在前两页找到 DOI，跳过重命名。")
            continue
            
        print(f"  🔍 找到 DOI: {doi}")
        
        # 2. 获取元数据
        metadata = fetch_metadata_crossref(doi)
        if not metadata:
            print("  ⚠️ 未能从 Crossref 获取到元数据，跳过重命名。")
            continue
            
        # 3. 构建新文件名
        new_filename = build_new_filename(metadata)
        if not new_filename:
            print("  ⚠️ 元数据字段均为空，无法构建新文件名。")
            continue
            
        new_path = os.path.join(PDF_DIR, new_filename)
        
        # 防止文件重名覆盖
        if os.path.exists(new_path) and old_path != new_path:
            # 如果文件名已存在，加个小尾巴
            name_body, ext = os.path.splitext(new_filename)
            new_filename = f"{name_body}_1{ext}"
            new_path = os.path.join(PDF_DIR, new_filename)
            
        # 4. 执行重命名
        if old_path != new_path:
            try:
                os.rename(old_path, new_path)
                print(f"  ✅ 成功重命名为: {new_filename}")
            except Exception as e:
                print(f"  ❌ 重命名失败: {e}")
        else:
            print("  ✨ 文件名已经是标准格式，无需修改。")
        print("-" * 40)

if __name__ == "__main__":
    main()