import requests
from bs4 import BeautifulSoup
import time
import random
import urllib.parse
import json
import pandas as pd
import re
import traceback

class GoogleScholarScraper:
    def __init__(self, proxies=None):
        self.base_url = "https://scholar.google.com/scholar"
        self.session = requests.Session()
        self.session.trust_env = False  # 禁用系统环境代理

        # 应用传入的代理配置
        if proxies:
            self.session.proxies.update(proxies)

        # 更真实的浏览器头部
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Cache-Control': 'max-age=0'
        }
        self.session.headers.update(self.headers)

    def test_proxy(self):
        """测试代理是否工作"""
        try:
            response = self.session.get("https://httpbin.org/ip", timeout=10)
            print(f"✅ 代理测试成功，当前IP: {response.json()}")
            return True
        except Exception as e:
            print(f"❌ 代理测试失败: {e}")
            return False

    def test_connection(self):
        """测试连接是否正常"""
        try:
            response = self.session.get("https://scholar.google.com", timeout=10)
            print(f"✅ 连接测试成功，状态码: {response.status_code}")
            if response.text:
                soup = BeautifulSoup(response.text, 'html.parser')
                title = soup.title.text if soup.title else "无标题"
                print(f"页面标题: {title[:50]}...")
            return True
        except Exception as e:
            print(f"❌ 连接测试失败: {e}")
            return False

    def search_papers(self, keywords, num_results=10, lang='zh', search_mode='AND', year=None):
        if isinstance(keywords, str):
            keywords = [keywords]

        # 构造查询语句
        if search_mode == 'AND':
            query = ' AND '.join(keywords)
        elif search_mode == 'OR':
            query = ' OR '.join(keywords)
        else:
            query = ' '.join(keywords)

        return self._search_single_query(query, num_results, lang, year)

    def _search_single_query(self, query, num_results, lang, year=None):
        """执行单次搜索查询 - 增强版，支持年份/年份范围限制"""
        papers = []
        start_index = 0
        max_pages = min(70, (num_results // 10) + 2)

        page_count = 0

        while len(papers) < num_results and page_count < max_pages:
            try:
                params = {
                    'q': query,
                    'start': start_index,
                    'hl': lang,
                    'as_sdt': '0,5'
                }

                if year is not None:
                    # 兼容 JSON 传过来的 list 以及 Python 原生的 tuple
                    if isinstance(year, (tuple, list)):
                        if len(year) == 1 and isinstance(year[0], int):
                            single_year = year[0]
                            params['as_ylo'] = str(single_year)
                            params['as_yhi'] = str(single_year)
                            print(f"📅 已设置年份限制为 {single_year}")
                        elif len(year) == 2 and all(isinstance(y, int) for y in year):
                            start_year, end_year = year
                            if start_year > end_year:
                                print(f"⚠️  年份范围起始年份 ({start_year}) 大于结束年份 ({end_year})，将进行交换。")
                                start_year, end_year = end_year, start_year
                            params['as_ylo'] = str(start_year)
                            params['as_yhi'] = str(end_year)
                            print(f"📅 已设置年份范围限制为 {start_year} - {end_year}")
                        else:
                            print(f"⚠️  年份参数格式不正确: {year}。应为 [年份] 或 [开始年份, 结束年份]。将忽略年份限制。")
                    else:
                        print(f"⚠️  年份参数类型不正确: {type(year)}。将忽略年份限制。")

                url = f"{self.base_url}?{urllib.parse.urlencode(params)}"
                print(f"\n🔍 正在搜索: {query}")
                print(f"📄 第{start_index//10 + 1}页")
                print(f"🔗 请求URL: {url}")

                response = self.session.get(url, timeout=15)

                if response.status_code == 429:
                    print("⚠️  请求过于频繁，等待更长时间...")
                    time.sleep(random.uniform(10, 15))
                    continue
                elif response.status_code != 200:
                    print(f"❌ HTTP错误: {response.status_code}")
                    print(f"响应内容预览: {response.text[:200]}")
                    break

                if "captcha" in response.text.lower() or "机器人" in response.text or "robot" in response.text.lower():
                    print("🚨 检测到验证码，请手动访问Google Scholar解除限制")
                    break

                soup = BeautifulSoup(response.text, 'html.parser')
                results = soup.find_all('div', class_='gs_r gs_or gs_scl')
                if not results:
                    results = soup.find_all('div', class_='gs_r')

                if not results:
                    print("🔍 未找到搜索结果")
                    break

                print(f"📄 本页找到 {len(results)} 个结果")

                new_papers_count = 0
                for result in results:
                    if len(papers) >= num_results:
                        break

                    paper_info = self._extract_paper_info(result)
                    if paper_info:
                        papers.append(paper_info)
                        new_papers_count += 1
                        print(f"✅ 已获取 {len(papers)} 篇论文: {paper_info.get('title', '')[:50]}...")

                if new_papers_count == 0:
                    print("⚠️  本页没有有效论文数据")

                start_index += 10
                page_count += 1

                delay = random.uniform(3, 8)
                print(f"⏳ 等待 {delay:.1f} 秒...")
                time.sleep(delay)

            except requests.exceptions.RequestException as e:
                print(f"🌐 网络请求错误: {e}")
                time.sleep(random.uniform(5, 10))
                continue
            except Exception as e:
                print(f"❌ 搜索出错: {e}")
                print(f"详细错误信息: {traceback.format_exc()}")
                break

        print(f"\n🎯 搜索完成，共获取 {len(papers)} 篇论文")
        return papers[:num_results]

    def _extract_paper_info(self, result_element):
        """提取论文信息"""
        try:
            paper_info = {}
            title_element = result_element.find('h3', class_='gs_rt')
            if title_element:
                title_link = title_element.find('a')
                if title_link:
                    paper_info['title'] = title_link.get_text().strip()
                    paper_info['link'] = title_link.get('href', '')
                else:
                    paper_info['title'] = title_element.get_text().strip()
                    paper_info['link'] = ''
            else:
                return None

            author_element = result_element.find('div', class_='gs_a')
            if author_element:
                paper_info['authors'] = author_element.get_text().strip()

            abstract_element = result_element.find('div', class_='gs_rs')
            if abstract_element:
                paper_info['abstract'] = abstract_element.get_text().strip()

            citation_element = result_element.find('div', class_='gs_fl')
            if citation_element:
                citation_links = citation_element.find_all('a')
                for link in citation_links:
                    text = link.get_text()
                    if any(keyword in text for keyword in ['被引用', 'Cited by', 'citazioni', 'citations']):
                        match = re.search(r'(\d+)', text)
                        if match:
                            paper_info['citations'] = int(match.group(1))
                        else:
                            paper_info['citations'] = text.strip()
                        break
            return paper_info
        except Exception as e:
            print(f"提取论文信息时出错: {e}")
            return None

    def save_to_json(self, papers, filename):
        """保存为JSON文件"""
        full_filename = f"{filename}.json"
        with open(full_filename, 'w', encoding='utf-8') as jsonfile:
            json.dump(papers, jsonfile, ensure_ascii=False, indent=2)
        print(f"💾 结果已保存到 {full_filename}")


def json_titles_to_excel(json_filename, excel_filename):
    """
    从 JSON 文件中读取论文数据，提取标题，并保存到 Excel 文件。
    """
    try:
        with open(json_filename, 'r', encoding='utf-8') as f:
            papers_data = json.load(f)
        
        print(f"✅ 成功读取 JSON 文件: {json_filename}")

        titles = []
        for paper in papers_data:
            title = paper.get('title', 'N/A')
            titles.append(title)
        
        print(f"✅ 提取到 {len(titles)} 个标题")

        df = pd.DataFrame({'Title': titles})
        df.to_excel(excel_filename, index=False, engine='openpyxl')
        print(f"✅ 标题已成功提取并保存到 Excel 文件: {excel_filename}")

    except FileNotFoundError:
        print(f"❌ 错误：找不到文件 {json_filename}")
    except json.JSONDecodeError:
        print(f"❌ 错误：JSON 文件 {json_filename} 格式不正确")
    except Exception as e:
        print(f"❌ 处理文件时发生错误: {e}")