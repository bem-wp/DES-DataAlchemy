import os
import yaml
from dataclasses import dataclass
from dotenv import load_dotenv

# ==================== 1. 数据结构定义 (Dataclasses) ====================
@dataclass(frozen=True)
class PdfRenamerConfig:
    """PDF 重命名工具配置"""
    pdf_dir: str
    rate_limit_sleep: float
    api_timeout: int
    user_agent: str
    log_level: str
    retry_times: int

@dataclass(frozen=True)
class AsExtractionConfig:
    """应用场景提取专用配置"""
    api_key: str
    pdf_input_folder: str
    pdf_to_txt_log : str
    output_csv: str
    prompt_templates_path: str
    model_base_url: str
    model_name: str
    timeout: int
    max_workers: int

@dataclass(frozen=True)
class MainScreeningConfig:
    """预筛选任务专用配置"""
    api_key: str
    pdf_input_folder: str
    output_csv: str
    prompt_templates_path: str
    model_base_url: str
    model_name: str
    timeout: int
    max_workers: int

@dataclass(frozen=True)
class MainSelectorConfig:
    pdf_input_folder: str
    main_screening_result: str
    pdf_output_folder: str

@dataclass(frozen=True)
class SiScreeningConfig:
    api_key: str
    pdf_input_folder: str
    output_csv: str
    prompt_templates_path: str
    model_base_url: str
    model_name: str
    timeout: int
    max_workers: int

@dataclass(frozen=True)
class SiSelectorConfig:
    pdf_input_folder: str
    si_screening_result: str
    pdf_output_folder: str

@dataclass(frozen=True)
class ExtractionConfig:
    """数据抽取任务专用配置"""
    api_key: str
    pdf_input_folder: str
    schema_template_path: str
    result_output_folder: str
    parse_model_name: str
    extract_model_name : str

@dataclass(frozen=True)
class JsonFilterConfig:
    json_input_folder: str
    target_property : str
    condition_vars : list
    json_output_folder: str


@dataclass(frozen=True)
class VisualizerConfig:
    pdf_folder: str
    parsed_json_folder : str
    filtered_json_folder : str
    revised_json_folder : str

@dataclass(frozen=True)
class JsontotableConfig:
    property_name : str
    json_folder : str
    table_folder : str

# ==================== 2. 基础环境初始化 ====================

def _init_env_and_read_yaml():
    """内部通用函数：定位目录、加载 .env、读取 config.yaml"""
    current_file_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(current_file_dir)

    # 只要调用任何配置，都默认加载一次环境变量
    load_dotenv(dotenv_path=os.path.join(project_root, ".env"))

    yaml_path = os.path.join(project_root, "configs", "config.yaml")
    if not os.path.exists(yaml_path):
        raise FileNotFoundError(f"配置文件未找到: {yaml_path}")
    
    with open(yaml_path, "r", encoding="utf-8") as f:
        yaml_data = yaml.safe_load(f) or {}

    return project_root, yaml_data


# ==================== 3. 为每个 Pipeline 节点暴露专属加载器 ====================
def load_pdf_renamer_config() -> PdfRenamerConfig:
    """Pipeline 节点 1：专属加载重命名工具配置"""
    project_root, yaml_data = _init_env_and_read_yaml()
    
    cfg = yaml_data.get("pdf_renamer")
    if not cfg:
        raise RuntimeError("config.yaml 中缺少 'pdf_renamer' 配置节")

    pdf_dir_raw = cfg.get("pdf_dir")
    if not pdf_dir_raw:
        raise RuntimeError("pdf_renamer 配置中缺少 pdf_dir")

    full_pdf_dir = os.path.join(project_root, pdf_dir_raw)
    os.makedirs(full_pdf_dir, exist_ok=True)

    return PdfRenamerConfig(
        pdf_dir=full_pdf_dir,
        rate_limit_sleep=float(cfg.get("rate_limit_sleep", 0.5)),
        api_timeout=int(cfg.get("api_timeout", 20)),
        user_agent=str(cfg.get("user_agent", "PDFBot/1.0")),
        log_level=str(cfg.get("log_level", "INFO")),
        retry_times=int(cfg.get("retry_times", 3))
    )

def load_as_extraction_config() -> AsExtractionConfig:

    project_root, yaml_data = _init_env_and_read_yaml()

    task_cfg = yaml_data.get("llms", {}).get("as_extraction", {})
    if not task_cfg:
        raise RuntimeError("config.yaml 中缺失 llms.as_extraction 节点")

    paths_cfg   = task_cfg.get("paths")    or {}
    prompt_cfg  = task_cfg.get("prompt")   or {}
    model_cfg   = task_cfg.get("model")    or {}
    setting_cfg = task_cfg.get("settings") or {}

    env_name = model_cfg.get("api_key_env")
    api_key = os.getenv(env_name)
    if not api_key:
        raise RuntimeError(f"未找到环境变量 {env_name}，请检查 .env 文件")

    # 必填项预检
    required = {
        "paths.input_folder": paths_cfg.get("input_folder"),
        "paths.in-out_folder" : paths_cfg.get("in-out_folder"),
        "paths.output_csv":    paths_cfg.get("output_csv"),
        "prompt.template":     prompt_cfg.get("template"),
        "model.name":          model_cfg.get("name"),
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise RuntimeError(f"as_extraction 配置缺失必填项: {', '.join(missing)}")

    return AsExtractionConfig(
        api_key=api_key,
        pdf_input_folder=os.path.join(project_root, paths_cfg["input_folder"]),
        pdf_to_txt_log= os.path.join(project_root, paths_cfg["in-out_folder"]),
        output_csv=os.path.join(project_root, paths_cfg["output_csv"]),
        prompt_templates_path=os.path.join(project_root, prompt_cfg["template"]),
        model_base_url=model_cfg.get("base_url", ""),
        model_name=model_cfg["name"],
        timeout=int(model_cfg.get("timeout", 300)),
        max_workers=int(setting_cfg.get("max_workers", 3)),
    )

def load_main_screening_config() -> MainScreeningConfig:
    """Pipeline 节点 2：专属加载预筛选配置"""
    project_root, yaml_data = _init_env_and_read_yaml()
    
    task_cfg = yaml_data.get("llms", {}).get("main_screening", {})
    if not task_cfg:
        raise RuntimeError("config.yaml 中缺失 llms.main_screening 节点")

    paths_cfg   = task_cfg.get("paths")    or {}
    prompt_cfg  = task_cfg.get("prompt")   or {}
    model_cfg   = task_cfg.get("model")    or {}
    setting_cfg = task_cfg.get("settings") or {}

    env_name = model_cfg.get("api_key_env")
    api_key = os.getenv(env_name)
    if not api_key:
        raise RuntimeError(f"未找到环境变量 {env_name}，请检查 .env 文件")

    # 必填项预检
    required = {
        "paths.input_folder": paths_cfg.get("input_folder"),
        "paths.output_csv":    paths_cfg.get("output_csv"),
        "prompt.template":     prompt_cfg.get("template"),
        "model.name":          model_cfg.get("name"),
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise RuntimeError(f"pdf_screening 配置缺失必填项: {', '.join(missing)}")

    return MainScreeningConfig(
        api_key=api_key,
        pdf_input_folder=os.path.join(project_root, paths_cfg["input_folder"]),
        output_csv=os.path.join(project_root, paths_cfg["output_csv"]),
        prompt_templates_path=os.path.join(project_root, prompt_cfg["template"]),
        model_base_url=model_cfg.get("base_url", ""),
        model_name=model_cfg["name"],
        timeout=int(model_cfg.get("timeout", 300)),
        max_workers=int(setting_cfg.get("max_workers", 3)),
    )

def load_main_selector_config() -> MainSelectorConfig:

    project_root, yaml_data = _init_env_and_read_yaml()

    pdf_selector_cfg = yaml_data.get("main_selector", {})


    return MainSelectorConfig(

        pdf_input_folder = os.path.join(project_root, pdf_selector_cfg["pdf_input_dir"]),
        main_screening_result = os.path.join(project_root, pdf_selector_cfg["main_screening_result"]),
        pdf_output_folder = os.path.join(project_root, pdf_selector_cfg["pdf_output_dir"])
        
    )

def load_si_screening_config() -> SiScreeningConfig:
    """Pipeline 节点 2：专属加载预筛选配置"""
    project_root, yaml_data = _init_env_and_read_yaml()
    
    task_cfg = yaml_data.get("llms", {}).get("si_screening", {})
    if not task_cfg:
        raise RuntimeError("config.yaml 中缺失 llms.si_screening 节点")

    paths_cfg   = task_cfg.get("paths")    or {}
    prompt_cfg  = task_cfg.get("prompt")   or {}
    model_cfg   = task_cfg.get("model")    or {}
    setting_cfg = task_cfg.get("settings") or {}

    env_name = model_cfg.get("api_key_env")
    api_key = os.getenv(env_name)
    if not api_key:
        raise RuntimeError(f"未找到环境变量 {env_name}，请检查 .env 文件")

    # 必填项预检
    required = {
        "paths.input_folder": paths_cfg.get("input_folder"),
        "paths.output_csv":    paths_cfg.get("output_csv"),
        "prompt.template":     prompt_cfg.get("template"),
        "model.name":          model_cfg.get("name"),
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise RuntimeError(f"si_screening 配置缺失必填项: {', '.join(missing)}")

    return SiScreeningConfig(
        api_key=api_key,
        pdf_input_folder=os.path.join(project_root, paths_cfg["input_folder"]),
        output_csv=os.path.join(project_root, paths_cfg["output_csv"]),
        prompt_templates_path=os.path.join(project_root, prompt_cfg["template"]),
        model_base_url=model_cfg.get("base_url", ""),
        model_name=model_cfg["name"],
        timeout=int(model_cfg.get("timeout", 300)),
        max_workers=int(setting_cfg.get("max_workers", 3)),
    )

def load_si_selector_config() -> SiSelectorConfig:

    project_root, yaml_data = _init_env_and_read_yaml()

    pdf_selector_cfg = yaml_data.get("si_selector", {})


    return SiSelectorConfig(

        pdf_input_folder = os.path.join(project_root, pdf_selector_cfg["pdf_input_dir"]),
        si_screening_result = os.path.join(project_root, pdf_selector_cfg["si_screening_result"]),
        pdf_output_folder = os.path.join(project_root, pdf_selector_cfg["pdf_output_dir"])
        
    )

def load_extraction_config() -> ExtractionConfig:
    """Pipeline 节点 N：专属加载数据抽取配置"""
    project_root, yaml_data = _init_env_and_read_yaml()
    
    extract_cfg = yaml_data.get("llms", {}).get("data_extraction", {})
    if not extract_cfg:
        raise RuntimeError("config.yaml 中缺失 llms.data_extraction 节点")

    paths_cfg   = extract_cfg.get("paths")    or {}
    schema_cfg  = extract_cfg.get("schema")   or {}
    model_cfg   = extract_cfg.get("model")    or {}

    env_name = model_cfg.get("api_key_env")
    api_key = os.getenv(env_name)
    if not api_key:
        raise RuntimeError(f"未找到环境变量 {env_name}，请检查 .env 文件")

    return ExtractionConfig(
        
        api_key=api_key,
        pdf_input_folder=os.path.join(project_root, paths_cfg.get("input_folder", "")),
        schema_template_path=os.path.join(project_root, schema_cfg.get("template", "")),
        result_output_folder=os.path.join(project_root, paths_cfg.get("output_folder", "")),
        parse_model_name = model_cfg.get("parse_model_name", ""),
        extract_model_name = model_cfg.get("extract_model_name", "")

    )

def load_json_filter_config() -> JsonFilterConfig:
    project_root, yaml_data = _init_env_and_read_yaml()

    json_filter_cfg = yaml_data.get('json_filter', {})

    return JsonFilterConfig(
        json_input_folder = os.path.join(project_root, json_filter_cfg["json_input_dir"]),
        # 直接取值，注意 YAML 键名是 targe_property（拼写保持原样）
        target_property = json_filter_cfg.get("targe_property", ""),
        condition_vars = json_filter_cfg.get("condition_vars", []),
        json_output_folder = os.path.join(project_root, json_filter_cfg["json_output_dir"])
    )

def load_visualizer_config() -> VisualizerConfig:
    project_root, yaml_data = _init_env_and_read_yaml()

    visualizer_cfg = yaml_data.get('visualizer', {})

    return VisualizerConfig(

         pdf_folder = os.path.join(project_root, visualizer_cfg["pdf_files_dir"]),
         parsed_json_folder  = os.path.join(project_root, visualizer_cfg["parsed_json_dir"]),
         filtered_json_folder = os.path.join(project_root, visualizer_cfg["filtered_json_dir"]),
         revised_json_folder = os.path.join(project_root, visualizer_cfg["revised_json_dir"])
         
    )


def load_json_to_table_config() -> JsontotableConfig:

    project_root, yaml_data = _init_env_and_read_yaml()

    json_to_table_cfg = yaml_data.get('json_to_table', {})

    return JsontotableConfig(

        property_name = json_to_table_cfg['targe_property'],

        json_folder = os.path.join(project_root, json_to_table_cfg['json_files_dir']),

        table_folder = os.path.join(project_root, json_to_table_cfg['table_dir'])

    )