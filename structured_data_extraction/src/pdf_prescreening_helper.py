import requests
import json
import os
import re
import csv
import glob
import pandas as pd
from pathlib import Path
from datetime import datetime

# ==========================================
# Dify API 交互与文件处理逻辑
# ==========================================

def upload_file(file_path, user, api_key, api_base_url):
    upload_url = f"{api_base_url}/files/upload"
    headers = {
        "Authorization": f"Bearer {api_key}"
    }
    
    try:
        print(f"上传文件中: {os.path.basename(file_path)}")
        if not os.path.exists(file_path):
            print(f"文件不存在: {file_path}")
            return None
            
        with open(file_path, 'rb') as file:
            files = {
                'file': (os.path.basename(file_path), file, 'application/pdf')
            }
            data = {
                "user": user,
                "type": "document"
            }
            
            response = requests.post(upload_url, headers=headers, files=files, data=data)
            
            if response.status_code == 201:
                print("文件上传成功")
                return response.json().get("id")
            else:
                print(f"文件上传失败，状态码: {response.status_code}")
                try:
                    print(response.json())
                except:
                    pass
                return None
    except Exception as e:
        print(f"发生错误: {str(e)}")
        return None

def decode_unicode_text(text):
    """解码Unicode转义序列"""
    try:
        decoded_text = text.encode('utf-8').decode('unicode_escape')
        return decoded_text
    except:
        return text

def clean_response_text(text):
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    return text.strip()

def run_workflow(file_id, user, api_key, api_base_url, response_mode="blocking"):
    workflow_url = f"{api_base_url}/workflows/run"
    headers = {
        "Authorization": f"Bearer {api_key}",
    }
    data = {
        "inputs": {
            "pdf": {
                "transfer_method": "local_file",
                "upload_file_id": file_id,
                "type": "document"
            }
        },
        "response_mode": response_mode,
        "user": user
    }

    try:
        print("运行工作流...")
        response = requests.post(workflow_url, headers=headers, json=data)
        
        if response.status_code == 200:
            print("工作流执行成功")
            result = response.json()
            if 'data' in result and 'outputs' in result['data']:
                outputs = result['data']['outputs']
                if 'text' in outputs:
                    raw_text = outputs['text']
                    cleaned_text = clean_response_text(raw_text)
                    result['data']['outputs']['cleaned_text'] = cleaned_text
                    return cleaned_text
            return None
        else:
            print(f"工作流执行失败，状态码: {response.status_code}")
            return None
    except Exception as e:
        print(f"发生错误: {str(e)}")
        return None

def save_json(content, filename, output_dir):
    try:
        os.makedirs(output_dir, exist_ok=True)
        json_filename = f"{os.path.splitext(filename)[0]}.json"
        json_filepath = os.path.join(output_dir, json_filename)
        
        with open(json_filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        
        print(f"结果已保存到: {json_filepath}")
        return True
    except Exception as e:
        print(f"保存json文件失败: {str(e)}")
        return False

# 🌟 修改点：增加了 task_name 参数，让标题动态化
def save_markdown(content, filename, output_dir, task_name="物性数据"):
    """保存内容为markdown文件"""
    try:
        os.makedirs(output_dir, exist_ok=True)
        md_filename = f"{os.path.splitext(filename)[0]}.md"
        md_filepath = os.path.join(output_dir, md_filename)
        
        with open(md_filepath, 'w', encoding='utf-8') as f:
            # 动态插入 task_name 到标题中
            f.write(f"# {filename} - {task_name}提取结果\n\n")
            f.write(f"**处理时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write("## 提取结果\n\n")
            f.write(content)
        
        print(f"结果已保存到: {md_filepath}")
        return True
    except Exception as e:
        print(f"保存markdown文件失败: {str(e)}")
        return False

def update_statistics(stats_list, excel_path):
    """更新统计信息到Excel文件"""
    try:
        df = pd.DataFrame(stats_list, columns=['文献名称', '是否有数据', '处理时间', '备注'])
        df.to_excel(excel_path, index=False, engine='openpyxl')
        print(f"统计信息已保存到: {excel_path}")
        return True
    except Exception as e:
        print(f"保存统计信息失败: {str(e)}")
        return False

def is_no_data(content):
    """判断内容是否为"无"或类似表示无数据的内容"""
    if not content:
        return True
    
    content = content.strip().lower()
    no_data_keywords = ['无', 'none', 'null', 'no data', 'not found', 'not available']
    return any(keyword in content for keyword in no_data_keywords)

def is_file_already_processed(pdf_filename, output_dir):
    json_filename = f"{os.path.splitext(pdf_filename)[0]}.json"
    json_filepath = os.path.join(output_dir, json_filename)
    return os.path.exists(json_filepath)

def process_pdf_folder(pdf_folder, output_dir, user, api_key, api_base_url):
    os.makedirs(output_dir, exist_ok=True)
    statistics = []
    pdf_files = list(Path(pdf_folder).glob("*.pdf"))

    if not pdf_files:
        print(f"在文件夹 {pdf_folder} 中没有找到PDF文件")
        return
    
    print(f"找到 {len(pdf_files)} 个PDF文件")
    skipped_count = 0

    for i, pdf_file in enumerate(pdf_files, 1):
        print(f"\n{'='*60}")
        print(f"处理第 {i}/{len(pdf_files)} 个文件: {pdf_file.name}")
        print(f"{'='*60}")

        if is_file_already_processed(pdf_file.name, output_dir):
            print(f"文件 {pdf_file.name} 已经处理过，跳过处理")
            skipped_count += 1
            statistics.append([
                pdf_file.name,
                "已跳过",
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                "文件已存在，跳过处理"
            ])
            continue
        
        try:
            file_id = upload_file(str(pdf_file), user, api_key, api_base_url)
            
            if file_id:
                result = run_workflow(file_id, user, api_key, api_base_url)
                
                if result:
                    print(f"提取结果: {result[:100]}...")  
                    
                    if is_no_data(result):
                        print("结果为'无'，不保存json文件")
                        has_data = "无"
                        note = "无相关数据"
                    else:
                        print("发现有效数据，保存json文件")
                        if save_json(result, pdf_file.name, output_dir):
                            has_data = "有"
                            note = "已保存json文件"
                        else:
                            has_data = "有"
                            note = "保存文件失败"
                else:
                    print("工作流执行失败")
                    has_data = "处理失败"
                    note = "工作流执行失败"
            else:
                print("文件上传失败")
                has_data = "处理失败"
                note = "文件上传失败"
            
            statistics.append([
                pdf_file.name,
                has_data,
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                note
            ])
            
        except Exception as e:
            print(f"处理文件 {pdf_file.name} 时发生错误: {str(e)}")
            statistics.append([
                pdf_file.name,
                "处理失败",
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                f"错误: {str(e)}"
            ])
    
    excel_path = os.path.join(output_dir, "处理统计.xlsx")
    update_statistics(statistics, excel_path)

    print(f"\n{'='*60}")
    print("处理完成！统计信息：")
    print(f"{'='*60}")
    
    total_files = len(pdf_files)
    has_data_count = sum(1 for stat in statistics if stat[1] == "有")
    no_data_count = sum(1 for stat in statistics if stat[1] == "无")
    failed_count = sum(1 for stat in statistics if stat[1] == "处理失败")
    
    print(f"总文件数: {total_files}")
    print(f"跳过文件数: {skipped_count}")
    print(f"实际处理文件数: {total_files - skipped_count}")
    print(f"有数据文件: {has_data_count}")
    print(f"无数据文件: {no_data_count}")
    print(f"处理失败文件: {failed_count}")
    print(f"json文件保存位置: {output_dir}")
    print(f"统计信息保存位置: {excel_path}")


# ==========================================
# JSON 解析与 CSV 提取逻辑
# ==========================================

class GenericPropertyHandler:
    def __init__(self, config_path="config.json"):
        self.json_content = None
        # 初始化时加载配置
        self.config = self.load_config(config_path)

    def load_config(self, config_path):
        """读取外部配置文件"""
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"找不到配置文件: {config_path}")
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def process_json(self, file_path: str):
        """从文件中提取 JSON 内容"""
        try:
            with open(file_path, 'r', encoding='utf-8') as jf:
                text_json = jf.read()
                # 尝试匹配被 ```json ... ``` 包裹的 JSON
                match = re.search(r"```json(.*?)```", text_json, re.DOTALL)
                if match:
                    self.json_content = match.group(1).strip()
                else:
                    self.json_content = text_json.strip()
                return True
        except Exception as e:
            print(f"提取 JSON 内容出错: {file_path}, 错误: {e}")
            return False

    def process_single_file(self, file_path):
        """处理单个 JSON 文件"""
        if not self.process_json(file_path):
            return []
        
        try:
            content = json.loads(self.json_content)
            filename = os.path.basename(file_path)
            
            # 根据 config 动态获取目标键名
            target_key = self.config.get('target_key', 'unknown_key')
            extracted_value = content.get(target_key, 'N/A')

            row = {
                'filename': filename,
                target_key: extracted_value  # 动态设置字典的 Key
            }
            return [row]

        except json.JSONDecodeError as e:
            print(f"JSON 解析错误 [{file_path}]: {e}")
            return []
        except Exception as e:
            print(f"处理逻辑出错 [{file_path}]: {e}")
            return []

    def process_json_to_csv(self, input_dir=None, output_dir=None):
        """生成 CSV"""
        
        # 1. 确定输入路径（优先用传入的，否则回退到 config 里的）
        input_path = input_dir or self.config.get('input_folder')
        
        # 2. 确定输出路径
        csv_filename = self.config.get('output_csv')
        if output_dir:
            csv_path = os.path.join(output_dir, csv_filename)
        else:
            csv_path = csv_filename
            
        target_key = self.config.get('target_key', 'unknown_key')
        
        # 动态生成 CSV 表头
        fields = ['filename', target_key]
        
        all_rows = []
        json_files = []
        
        if os.path.isfile(input_path):
            json_files = [input_path]
        elif os.path.isdir(input_path):
            json_files = glob.glob(os.path.join(input_path, "*.json"))
        else:
            print(f"错误: 路径 '{input_path}' 不存在。")
            return
        
        print(f"找到 {len(json_files)} 个 JSON 文件，准备提取 '{target_key}' 字段...")
        
        for json_file in json_files:
            rows = self.process_single_file(json_file)
            if rows:
                all_rows.extend(rows)
        
        if all_rows:
            try:
                # 确保输出目录存在
                os.makedirs(os.path.dirname(os.path.abspath(csv_path)) or '.', exist_ok=True)
                
                with open(csv_path, 'w', encoding='utf-8', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=fields)
                    writer.writeheader()
                    writer.writerows(all_rows)
                print(f"处理完成! CSV 已保存至: {csv_path}")
                print(f"总计提取: {len(all_rows)} 条数据")
            except Exception as e:
                print(f"写入 CSV 失败: {e}")
        else:
            print("未提取到任何有效数据。")