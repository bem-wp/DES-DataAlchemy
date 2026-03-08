import os 
import json
import sys
from scraper_utils import GoogleScholarScraper, json_titles_to_excel

def load_config(config_file="config.json"):
    """加载 JSON 配置文件"""
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"❌ 错误: 找不到配置文件 {config_file}")
        sys.exit(1)
    except json.JSONDecodeError:
        print(f"❌ 错误: 配置文件 {config_file} 格式错误")
        sys.exit(1)


if __name__ == "__main__":
    print("Google Scholar 爬虫")

    # 获取当前脚本所在目录的绝对路径作为基准路径 (即 src 文件夹)
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(current_dir)
    # 1. 加载配置 (拼接 config.json 的绝对路径)
    config_path = os.path.join(project_root,'configs',"00_scraper_config.json")
    config = load_config(config_path) 
    
    proxies = config.get("proxies", {})
    search_cfg = config.get("search_settings", {})
    output_cfg = config.get("output_settings", {})

    # 2. 初始化爬虫实例并注入代理
    scraper = GoogleScholarScraper(proxies=proxies)
    
    # 测试网络（可选）
    # scraper.test_proxy()
    # scraper.test_connection()

    print("\n✅ 开始搜索...")
    
    # 3. 执行搜索
    papers = scraper.search_papers(
        keywords=search_cfg.get("keywords", []),
        num_results=search_cfg.get("num_results", 10),
        lang=search_cfg.get("lang", "en"),
        search_mode=search_cfg.get("search_mode", "AND"),
        year=search_cfg.get("year")
    )

    print(f"\n📊 获得 {len(papers)} 篇论文")
    

# 4. 保存与转换
    if papers:
        # 获取项目根目录 (也就是 src 的上一级)
        project_root = os.path.dirname(current_dir)
        
        # 定义目标输出文件夹路径: ../data/in_out
        output_dir = os.path.join(project_root, "data", "scraper_results")
        
        # 🌟 关键：检查并自动创建 data/in_out 文件夹（如果不存在的话）
        os.makedirs(output_dir, exist_ok=True)

        # 获取文件名，并与目标文件夹路径拼接
        json_basename = output_cfg.get("json_basename", "papers")
        absolute_json_basename = os.path.join(output_dir, json_basename)
        
        # scraper.save_to_json 会自动加上 .json 后缀
        scraper.save_to_json(papers, absolute_json_basename)

        print("\n🔄 开始提取标题并保存为 Excel...")
        full_json_path = f"{absolute_json_basename}.json"
        
        # 获取 Excel 文件名，并与目标文件夹路径拼接
        excel_filename = output_cfg.get("excel_filename", "extracted_titles.xlsx")
        absolute_excel_path = os.path.join(output_dir, excel_filename)
        
        json_titles_to_excel(full_json_path, absolute_excel_path)

    print("\n🎉 所有任务完成！")