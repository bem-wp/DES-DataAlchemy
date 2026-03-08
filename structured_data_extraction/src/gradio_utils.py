import json
import pymupdf  
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from PIL import Image, ImageDraw

HIGHLIGHT_COLOR = (255, 0, 0)  
HIGHLIGHT_OUTLINE_WIDTH = 4
HIGHLIGHT_FILL_ALPHA = 50  
PDF_SCALE = 2.0

def parse_extraction_metadata(metadata: Dict, chunks_map: Dict[str, Dict], sub_id_map: Dict[str, Dict]) -> List[Dict]:
    """解析 metadata，将高亮坐标信息映射为扁平化列表格式"""
    result = []
    
    # 1. Article Title
    if 'article_title' in metadata:
        article_data = metadata['article_title']
        if isinstance(article_data, dict) and 'references' in article_data:
            refs = article_data.get('references', [])
            if refs:
                ref_id = refs[0]
                chunk = chunks_map.get(ref_id) or sub_id_map.get(ref_id)
                
                if chunk and 'grounding' in chunk:
                    grounding = chunk['grounding']
                    result.append({
                        'system_index': -1,
                        'system_label': 'Article Title',
                        'field': 'article_title',
                        'full_path': 'article_title',
                        'value': article_data.get('value', ''),
                        'page': grounding.get('page', 0),
                        'box': grounding.get('box', {}),
                        'reference_id': ref_id
                    })

    # 2. DES Entries 
    if 'des_entries' in metadata:
        des_entries = metadata['des_entries']
        if isinstance(des_entries, list):
            for sys_idx, system in enumerate(des_entries):
                system_label = f"DES Entry #{sys_idx + 1}"
    
                def traverse_system(obj, field_path=""):
                    if isinstance(obj, dict):
                        if 'value' in obj and 'references' in obj:
                            refs = obj.get('references', [])
                            if refs:
                                ref_id = refs[0]
                                chunk = chunks_map.get(ref_id) or sub_id_map.get(ref_id)
                                
                                if chunk and 'grounding' in chunk:
                                    grounding = chunk['grounding']
                                    result.append({
                                        'system_index': sys_idx,
                                        'system_label': system_label,
                                        'field': field_path,
                                        'full_path': f"des_entries[{sys_idx}].{field_path}",
                                        'value': obj.get('value', ''),
                                        'page': grounding.get('page', 0),
                                        'box': grounding.get('box', {}),
                                        'reference_id': ref_id
                                    })
                        else:
                            for key, val in obj.items():
                                if key not in ['value', 'references']:
                                    new_path = f"{field_path}.{key}" if field_path else key
                                    traverse_system(val, new_path)
                    
                    elif isinstance(obj, list):
                        for i, item in enumerate(obj):
                            new_path = f"{field_path}[{i}]"
                            traverse_system(item, new_path)
                
                traverse_system(system)
    
    return result

def get_pdf_page_count(pdf_path: Path) -> int:
    """获取 PDF 总页数"""
    try:
        doc = pymupdf.open(str(pdf_path))
        page_count = len(doc)
        doc.close()
        return page_count
    except Exception as e:
        print(f"Error reading PDF page count: {e}")
        return 0

def render_pdf_page(
    pdf_path: Path, 
    page_num: int, 
    bbox: Optional[Dict] = None,
    highlight_color: Tuple[int, int, int] = HIGHLIGHT_COLOR
) -> Image.Image:
    """将指定的 PDF 页面渲染为图片，并选择性绘制高亮框"""
    try:
        doc = pymupdf.open(str(pdf_path))
        if page_num < 0 or page_num >= len(doc):
            page_num = 0
        
        page = doc[page_num]
        mat = pymupdf.Matrix(PDF_SCALE, PDF_SCALE)
        pix = page.get_pixmap(matrix=mat)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        doc.close()
        
        if bbox:
            img = draw_bounding_box(img, bbox, highlight_color)
        
        return img
    except Exception as e:
        print(f"Error rendering PDF page: {e}")
        return Image.new('RGB', (800, 1000), color=(240, 240, 240))

def draw_bounding_box(img: Image.Image, bbox: Dict, color: Tuple[int, int, int] = HIGHLIGHT_COLOR) -> Image.Image:
    """在图片上根据左上右下坐标绘制半透明高亮框"""
    img_copy = img.copy()
    draw = ImageDraw.Draw(img_copy, 'RGBA')
    img_width, img_height = img.size
    
    left = bbox['left'] * img_width
    top = bbox['top'] * img_height
    right = bbox['right'] * img_width
    bottom = bbox['bottom'] * img_height
    
    fill_color = (*color, HIGHLIGHT_FILL_ALPHA)
    draw.rectangle([left, top, right, bottom], fill=fill_color)
    
    draw_solid = ImageDraw.Draw(img_copy)
    draw_solid.rectangle([left, top, right, bottom], outline=color, width=HIGHLIGHT_OUTLINE_WIDTH)
    
    return img_copy

def format_json_for_display(json_data: List[Dict]) -> str:
    """将数据美化为便于显示的 JSON 字符串"""
    return json.dumps(json_data, indent=2, ensure_ascii=False)

def get_entry_info(json_data: List[Dict], entry_index: int) -> Optional[Dict]:
    """安全获取指定索引的条目信息"""
    if entry_index < 0 or entry_index >= len(json_data):
        return None
    return json_data[entry_index]

def get_extraction_summary(json_data: List[Dict]) -> str:
    """生成提取数据的统计概览"""
    if not json_data:
        return "No data available"
    
    total_entries = len(json_data)
    pages = set(entry.get('page', 0) for entry in json_data)
    
    des_systems = set(entry.get('system_index', -1) for entry in json_data if entry.get('system_index', -1) >= 0)
    des_count = len(des_systems)
    
    has_article_title = any(entry.get('system_index', -1) == -1 for entry in json_data)
    
    summary = f"""
Extraction Data Summary:
================================
Total Entries: {total_entries}
Pages Involved: {len(pages)}
Number of DES Systems: {des_count}
Contains Article Title: {'Yes' if has_article_title else 'No'}
================================
"""
    return summary.strip()

def group_data_by_system(json_data: List[Dict]) -> Dict[str, List[Dict]]:
    """将提取数据按照归属的 DES System 进行分组"""
    grouped = {}
    for entry in json_data:
        system_label = entry.get('system_label', 'Unknown')
        if system_label not in grouped:
            grouped[system_label] = []
        grouped[system_label].append(entry)
    return grouped