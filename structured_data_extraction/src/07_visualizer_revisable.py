import gradio as gr
from pathlib import Path
from typing import Tuple, List, Optional, Dict
import json
import os
import copy
import re

# 从工具库中仅导入纯数据处理和绘图的函数，不再导入任何路径和文件读取相关的函数
from gradio_utils import (
    parse_extraction_metadata,
    get_pdf_page_count,
    render_pdf_page,
    format_json_for_display,
    get_entry_info,
    get_extraction_summary,
    group_data_by_system
)

# ================== 1. 动态路径配置 ==================
# ================== 1. 动态路径配置 ==================
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)

# 0. 读取配置文件获取动态物性名称
CONFIG_PATH = os.path.join(project_root, "configs", "03_json_filter_config.json")
prop_name = "unknown" # 默认 fallback 名称
try:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            config_data = json.load(f)
            # 获取 property_json_field 的值，例如 "conductivity"
            prop_name = config_data.get('property_json_field', 'unknown')
    else:
        print(f"⚠️ 警告: 找不到配置文件 {CONFIG_PATH}")
except Exception as e:
    print(f"⚠️ 警告: 读取配置文件出错: {e}")

# 1. 原始PDF存放目录
PDF_FOLDER = Path(os.path.normpath(os.path.join(
    project_root, "data", "in_out", f"pdfs_has_{prop_name}"
)))

# 2. 最终过滤后的数据提取结果
EXTRACTED_FOLDER = Path(os.path.normpath(os.path.join(
    project_root, "data", "in_out", f"{prop_name}_02_filtered"
)))

# 3. LandingAI 解析生成的 chunks 
PARSED_FOLDER = Path(os.path.normpath(os.path.join(
    project_root, "data", "in_out", f"{prop_name}_00_parsed"
)))


# ================== 2. 文件读取与 I/O 逻辑 ==================
def get_pdf_path(pdf_filename: str) -> Path:
    return PDF_FOLDER / pdf_filename

def load_available_pdfs() -> List[str]:
    if not PDF_FOLDER.exists():
        print(f"⚠️ 警告: PDF文件夹不存在: {PDF_FOLDER}")
        return []
    
    available_pdfs = []
    for pdf_path in PDF_FOLDER.glob("*.pdf"):
        extracted_filename = f"{pdf_path.name}_filtered.json"
        parsed_filename = f"{pdf_path.name}_parsed.json"
        
        extracted_path = EXTRACTED_FOLDER / extracted_filename
        parsed_path = PARSED_FOLDER / parsed_filename
        
        # 只有当解析文件和提取过滤文件都存在时，才显示在下拉列表中
        if extracted_path.exists() and parsed_path.exists():
            available_pdfs.append(pdf_path.name)
    
    return sorted(available_pdfs)

def load_parsed_chunks(pdf_filename: str) -> Tuple[Dict[str, Dict], Dict[str, Dict]]:
    parsed_filename = f"{pdf_filename}_parsed.json"
    parsed_path = PARSED_FOLDER / parsed_filename
    
    if not parsed_path.exists():
        return {}, {}
    
    with open(parsed_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    chunks = data.get('chunks', [])
    chunk_map = {}
    sub_id_map = {}
    
    id_pattern = re.compile(r'id=[\'"]([^\'"]+)[\'"]')
    
    for chunk in chunks:
        chunk_id = chunk.get('id')
        if chunk_id:
            chunk_map[chunk_id] = chunk
        
        markdown = chunk.get('markdown', '')
        if markdown:
            found_ids = id_pattern.findall(markdown)
            for sub_id in found_ids:
                if sub_id != chunk_id:
                    sub_id_map[sub_id] = chunk
    
    return chunk_map, sub_id_map

def load_extraction_data(pdf_filename: str) -> List[Dict]:
    extracted_filename = f"{pdf_filename}_filtered.json"
    extracted_path = EXTRACTED_FOLDER / extracted_filename
    
    if not extracted_path.exists():
        return []
    
    with open(extracted_path, 'r', encoding='utf-8') as f:
        extracted_data = json.load(f)
    
    metadata = extracted_data.get('extraction_metadata', {})
    if not metadata:
        return []
        
    chunks_map, sub_id_map = load_parsed_chunks(pdf_filename)
    if not chunks_map:
        return []
    
    result = parse_extraction_metadata(metadata, chunks_map, sub_id_map)
    return result


# ================== 3. 状态管理 ==================
class AppState:
    def __init__(self):
        self.current_pdf = None
        self.current_pdf_path = None
        self.json_data = []  
        self.grouped_data = {} 
        self.current_page = 0
        self.total_pages = 0
        self.selected_entry_idx = None
        self.selected_system = None  
        
        self.full_json_content = {} 
        self.original_extraction_data = {} 
    
    def load_pdf(self, pdf_filename: str):
        if not pdf_filename:
            return
        
        self.current_pdf = pdf_filename
        self.current_pdf_path = str(get_pdf_path(pdf_filename))
        
        self.json_data = load_extraction_data(pdf_filename)
        self.grouped_data = group_data_by_system(self.json_data)
        
        self.current_page = 0
        self.selected_entry_idx = None
        self.selected_system = None
        self.total_pages = get_pdf_page_count(get_pdf_path(pdf_filename))
        
        file_path = EXTRACTED_FOLDER / f"{pdf_filename}_filtered.json"
        if file_path.exists():
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    self.full_json_content = json.load(f)
                self.original_extraction_data = self.full_json_content.get('extraction', {})
            except Exception as e:
                print(f"Failed to load original file: {e}")
                self.full_json_content = {}
                self.original_extraction_data = {}
        
        print(f"[Load] PDF: {pdf_filename}, Entries: {len(self.json_data)}")
    
    def select_entry(self, entry_idx: int):
        self.selected_entry_idx = entry_idx
        if entry_idx is not None and 0 <= entry_idx < len(self.json_data):
            entry = self.json_data[entry_idx]
            self.current_page = entry.get('page', 0)
    
    def get_current_bbox(self):
        if self.selected_entry_idx is None:
            return None
        entry = get_entry_info(self.json_data, self.selected_entry_idx)
        if entry and entry.get('page') == self.current_page:
            return entry.get('box')
        return None
    
    def get_system_choices(self) -> List[str]:
        return sorted(self.grouped_data.keys(), key=lambda x: (-1 if x == 'Article Title' else int(x.split('#')[1])))
    
    def get_fields_for_system(self, system_label: str) -> List[str]:
        if system_label not in self.grouped_data:
            return []
        
        choices = []
        for idx, entry in enumerate(self.json_data):
            if entry.get('system_label') == system_label:
                field = entry.get('field', '')
                value = str(entry.get('value', ''))
                page = entry.get('page', 0) + 1

                if len(value) > 40:
                    value_display = value[:40] + "..."
                else:
                    value_display = value
                
                label = f"{field} (P.{page}) | {value_display}"
                choices.append(label)
        
        return choices
    
    def get_system_json(self, system_label: str) -> str:
        if not self.original_extraction_data:
            return "{}"
        
        if system_label == 'Article Title':
            title = self.original_extraction_data.get('article_title', "")
            return json.dumps(title, indent=2, ensure_ascii=False)
        
        elif system_label.startswith('DES Entry #'):
            try:
                entry_num = int(system_label.split('#')[1])
                des_list = self.original_extraction_data.get('des_entries', [])
                
                if 0 < entry_num <= len(des_list):
                    entry_obj = des_list[entry_num - 1]
                    return json.dumps(entry_obj, indent=2, ensure_ascii=False)
            except (ValueError, IndexError) as e:
                print(f"Failed to parse entry: {e}")
        
        return "{}"

    def update_metadata_recursively(self, meta_obj, data_obj):
        if isinstance(meta_obj, dict) and 'value' in meta_obj and 'references' in meta_obj:
            meta_obj['value'] = data_obj
            return

        if isinstance(data_obj, list) and isinstance(meta_obj, list):
            for i in range(min(len(data_obj), len(meta_obj))):
                self.update_metadata_recursively(meta_obj[i], data_obj[i])
            return

        if isinstance(data_obj, dict) and isinstance(meta_obj, dict):
            for k, v in data_obj.items():
                if k in meta_obj:
                    self.update_metadata_recursively(meta_obj[k], v)
            return

    def save_system_json(self, system_label: str, new_json_text: str) -> Tuple[bool, str]:
        if not self.current_pdf:
            return False, "No PDF loaded"

        try:
            new_data_obj = json.loads(new_json_text)
        except json.JSONDecodeError as e:
            return False, f"JSON format error: {e}"

        file_path = EXTRACTED_FOLDER / f"{self.current_pdf}_filtered.json"
        if not file_path.exists():
             return False, "Original file not found"
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                full_file_data = json.load(f)
            
            extraction_section = full_file_data.get('extraction')
            metadata_section = full_file_data.get('extraction_metadata')

            if extraction_section is None:
                return False, "File is missing the 'extraction' section"
            
            list_key = 'des_entries'
            
            if system_label == 'Article Title':
                extraction_section['article_title'] = new_data_obj
                if metadata_section and 'article_title' in metadata_section:
                    self.update_metadata_recursively(metadata_section['article_title'], new_data_obj)
            
            elif system_label.startswith('DES Entry #'):
                if list_key not in extraction_section:
                    return False, f"'{list_key}' not found in Extraction"
                
                entry_num = int(system_label.split('#')[1])
                des_list = extraction_section[list_key]
                
                if 0 < entry_num <= len(des_list):
                    des_list[entry_num - 1] = new_data_obj
                    if metadata_section and list_key in metadata_section:
                        meta_list = metadata_section[list_key]
                        if 0 < entry_num <= len(meta_list):
                            self.update_metadata_recursively(meta_list[entry_num - 1], new_data_obj)
                else:
                    return False, f"Index out of bounds: {entry_num}"
            else:
                return False, f"Unable to locate data path"

            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(full_file_data, f, indent=2, ensure_ascii=False)
            
            return True, "Saved successfully! (Extraction overwritten, Metadata synced)"

        except Exception as e:
            import traceback
            traceback.print_exc()
            return False, f"Error during saving: {str(e)}"

app_state = AppState()

# ================== 4. UI 及渲染逻辑 ==================
def on_next_page() -> Tuple:
    if not app_state.current_pdf_path:
        return None, "No PDF Loaded"
    if app_state.current_page < app_state.total_pages - 1:
        app_state.current_page += 1
    return render_page_common()

def on_prev_page() -> Tuple:
    if not app_state.current_pdf_path:
        return None, "No PDF Loaded"
    if app_state.current_page > 0:
        app_state.current_page -= 1
    return render_page_common()

def render_page_common():
    pdf_path = Path(app_state.current_pdf_path)
    bbox = app_state.get_current_bbox()
    img = render_pdf_page(pdf_path, app_state.current_page, bbox)
    page_info = f"Page {app_state.current_page + 1} / {app_state.total_pages}"
    return img, page_info

def create_interface():
    available_pdfs = load_available_pdfs()
    if available_pdfs:
        app_state.load_pdf(available_pdfs[0])
    
    with gr.Blocks(title="PDF Data Extraction & Verification Tool", css="""
        #field_radio_scrollable {
            max-height: 500px;
            overflow-y: auto;
            padding-right: 10px;
        }
        #field_radio_scrollable label {
            margin-bottom: 4px;
            padding: 8px;
            border: 1px solid #eee;
            border-radius: 4px;
            font-size: 13px;
        }
        #field_radio_scrollable label:hover {
            background-color: #f5f5f5;
        }
        #field_radio_scrollable input:checked + span {
            font-weight: bold;
            color: #1976d2;
        }
        .success-msg { color: green; font-weight: bold; }
        .error-msg { color: red; font-weight: bold; }
    """) as app:
        
        pdf_dropdown = gr.Dropdown(
            choices=available_pdfs if available_pdfs else [],
            label="Select PDF File",
            value=available_pdfs[0] if available_pdfs else None,
            interactive=True
        )

        with gr.Row():
            with gr.Column(scale=6): 
                gr.Markdown("### PDF Preview")
                pdf_image = gr.Image(label="", type="pil", interactive=False, height=800, show_label=False)
                
                with gr.Row():
                    prev_btn = gr.Button("◀ Previous", size="sm")
                    page_info = gr.Textbox(
                        value="Not loaded", interactive=False, show_label=False, container=False, text_align="center"
                    )
                    next_btn = gr.Button("Next ▶", size="sm")
            
            with gr.Column(scale=4): 
                with gr.Tab("Data Verification & Editing"):
                    entry_count_md = gr.Markdown(f"*Total {len(app_state.json_data)} entries*")
                    
                    gr.Markdown("#### 1. Select Data Group")
                    system_dropdown = gr.Dropdown(
                        label="",
                        choices=app_state.get_system_choices() if app_state.json_data else [],
                        interactive=True,
                        show_label=False
                    )

                    field_selector_container = gr.Column(visible=False)
                    with field_selector_container:
                        gr.Markdown("#### 2. Quick Field Navigation")
                        field_radio = gr.Radio(
                            label="", choices=[], interactive=True, show_label=False,
                            elem_id="field_radio_scrollable"
                        )
                    
                    edit_container = gr.Column(visible=False)
                    with edit_container:
                        gr.Markdown("#### 3. Edit Extraction Data")
                        gr.Markdown("The 'extraction' section (pure data) is displayed here. Modifications will automatically sync with 'metadata'.")
                        
                        selected_json_box = gr.Code(
                            label="", value="{}", language="json", lines=15, 
                            interactive=True,  
                            show_label=False
                        )
                        
                        with gr.Row():
                            save_btn = gr.Button("💾 Save Changes", variant="primary")
                            status_msg = gr.Markdown("", visible=True)

                with gr.Tab("Statistical Overview"):
                    summary_box = gr.Textbox(
                        label="", value="No data available", lines=15, interactive=False, show_label=False
                    )

        def on_pdf_change(pdf_filename):
            if not pdf_filename:
                return None, "Not loaded", "", "*No data available*", "{}", [], gr.update(visible=False), [], gr.update(visible=False), ""
            
            app_state.load_pdf(pdf_filename)
            img, page_info_text = render_page_common()
            
            summary = get_extraction_summary(app_state.json_data)
            entry_count = f"*Total {len(app_state.json_data)} entries*"
            system_choices = app_state.get_system_choices()
            
            return (
                img, 
                page_info_text, 
                summary, 
                entry_count, 
                "{}", 
                gr.update(choices=system_choices, value=None),
                gr.update(visible=False), 
                gr.update(choices=[], value=None), 
                gr.update(visible=False), 
                "" 
            )
        
        def on_system_select(system_label):
            if not system_label or not app_state.json_data:
                return (
                    gr.update(visible=False), 
                    gr.update(choices=[], value=None), 
                    gr.update(visible=True), 
                    "{}",
                    ""
                )
            
            app_state.selected_system = system_label
            system_json = app_state.get_system_json(system_label)
            field_choices = app_state.get_fields_for_system(system_label)
            
            return (
                gr.update(visible=True), 
                gr.update(choices=field_choices, value=None),  
                gr.update(visible=True), 
                system_json,
                "" 
            )
        
        def on_save_click(new_json_text):
            if not app_state.selected_system:
                return "Please select a data group first", gr.update()
            
            success, msg = app_state.save_system_json(app_state.selected_system, new_json_text)
            
            if success:
                app_state.load_pdf(app_state.current_pdf)
                field_choices = app_state.get_fields_for_system(app_state.selected_system)
                summary = get_extraction_summary(app_state.json_data)
                status_md = f"<span class='success-msg'>✅ {msg}</span>"
                return status_md, gr.update(choices=field_choices), summary 
            else:
                status_md = f"<span class='error-msg'>❌ {msg}</span>"
                return status_md, gr.update(), gr.update()

        pdf_dropdown.change(
            fn=on_pdf_change,
            inputs=[pdf_dropdown],
            outputs=[pdf_image, page_info, summary_box, entry_count_md, 
                    selected_json_box, system_dropdown, 
                    field_selector_container, field_radio, 
                    edit_container, status_msg]
        )
        
        system_dropdown.change(
            fn=on_system_select,
            inputs=[system_dropdown],
            outputs=[field_selector_container, field_radio, edit_container, selected_json_box, status_msg]
        )
        
        save_btn.click(
            fn=on_save_click,
            inputs=[selected_json_box],
            outputs=[status_msg, field_radio, summary_box]
        )

        def on_field_select(field_choice):
            if not field_choice or not app_state.json_data: return None, ""
            try:
                target_idx = -1
                for idx, entry in enumerate(app_state.json_data):
                    if entry.get('system_label') == app_state.selected_system:
                        field = entry.get('field', '')
                        value = str(entry.get('value', ''))
                        page = entry.get('page', 0) + 1
                        if len(value) > 40: value = value[:40] + "..."
                        label_to_match = f"{field} (P.{page}) | {value}"
                        if label_to_match == field_choice:
                            target_idx = idx
                            break
                if target_idx != -1:
                    app_state.select_entry(target_idx)
                    return render_page_common()
            except: pass
            return None, ""

        field_radio.change(fn=on_field_select, inputs=[field_radio], outputs=[pdf_image, page_info])
        next_btn.click(fn=on_next_page, outputs=[pdf_image, page_info])
        prev_btn.click(fn=on_prev_page, outputs=[pdf_image, page_info])
        
        if app_state.current_pdf_path:
            app.load(fn=lambda: render_page_common()[0], outputs=[pdf_image])

    return app

if __name__ == "__main__":
    app = create_interface()
    app.launch(server_name="127.0.0.1", server_port=7880, inbrowser=True)