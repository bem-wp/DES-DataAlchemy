import os
import yaml
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv


# ==================== Dataclasses ====================

@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    pdf_input_folder: str
    result_output_folder: str
    output_csv: str
    prompt_templates_path: str
    model_base_url: str
    model_name: str
    timeout: int
    max_workers: int


@dataclass(frozen=True)
class PdfRenamerConfig:
    pdf_dir: str
    rate_limit_sleep: float
    api_timeout: int
    user_agent: str
    log_level: str
    retry_times: int


@dataclass(frozen=True)
class AppConfig:
    # 改为 Optional，允许按需加载时为空
    llm: Optional[LLMConfig] = None
    pdf_renamer: Optional[PdfRenamerConfig] = None


# ==================== 各配置节解析函数 ====================

def _load_llm_config(yaml_config: dict, project_root: str, api_key: str) -> LLMConfig:
    paths_cfg   = yaml_config.get("paths")    or {}
    prompt_cfg  = yaml_config.get("prompt")   or {}
    model_cfg   = yaml_config.get("model")    or {}
    setting_cfg = yaml_config.get("settings") or {}

    required_pipeline = {
        "paths.input_folder":   paths_cfg.get("input_folder"),
        "paths.output_folder":  paths_cfg.get("output_folder"),
        "paths.output_csv":     paths_cfg.get("output_csv"),
        "prompt.template":      prompt_cfg.get("template"),
        "model.base_url":       model_cfg.get("base_url"),
        "model.name":           model_cfg.get("name"),
        "settings.max_workers": setting_cfg.get("max_workers"),
    }
    missing = [k for k, v in required_pipeline.items() if not v]
    if missing:
        raise RuntimeError(f"config.yaml 中以下必填配置项缺失或为空: {', '.join(missing)}")

    pdf_input_folder      = os.path.join(project_root, paths_cfg["input_folder"])
    result_output_folder  = os.path.join(project_root, paths_cfg["output_folder"])
    output_csv            = os.path.join(project_root, paths_cfg["output_csv"])
    prompt_templates_path = os.path.join(project_root, prompt_cfg["template"])

    if not os.path.exists(pdf_input_folder):
        raise RuntimeError(f"输入文件夹不存在: {pdf_input_folder}")
    if not os.path.exists(prompt_templates_path):
        raise RuntimeError(f"提示模板文件不存在: {prompt_templates_path}")

    return LLMConfig(
        api_key=api_key,
        pdf_input_folder=pdf_input_folder,
        result_output_folder=result_output_folder,
        output_csv=output_csv,
        prompt_templates_path=prompt_templates_path,
        model_base_url=model_cfg["base_url"],
        model_name=model_cfg["name"],
        timeout=int(model_cfg.get("timeout", 300)),
        max_workers=int(setting_cfg.get("max_workers", 3)),
    )


def _load_pdf_renamer_config(yaml_config: dict, project_root: str) -> PdfRenamerConfig:
    pdf_renamer_cfg = yaml_config.get("pdf_renamer")
    if not pdf_renamer_cfg:
        raise RuntimeError("config.yaml 中缺少 'pdf_renamer' 配置节")

    if not pdf_renamer_cfg.get("pdf_dir"):
        raise RuntimeError("pdf_renamer 配置节中缺少必填字段: pdf_dir")
    try:
        pdf_dir = str(pdf_renamer_cfg["pdf_dir"])
    except (ValueError, TypeError) as e:
        raise RuntimeError(f"pdf_dir 字段类型错误: {e}") from e

    full_pdf_dir = os.path.join(project_root, pdf_dir)
    if not os.path.exists(full_pdf_dir):
        raise RuntimeError(f"PDF 文件夹不存在: {full_pdf_dir}")

    return PdfRenamerConfig(
        pdf_dir=full_pdf_dir,
        rate_limit_sleep=float(pdf_renamer_cfg.get("rate_limit_sleep", 1.0)),
        api_timeout=int(pdf_renamer_cfg.get("api_timeout", 30)),
        user_agent=str(pdf_renamer_cfg.get("user_agent", "")),
        log_level=str(pdf_renamer_cfg.get("log_level", "INFO")),
        retry_times=int(pdf_renamer_cfg.get("retry_times", 3)),
    )


# ==================== 入口 ====================

def load_config(require_llm: bool = True, require_pdf_renamer: bool = True) -> AppConfig:
    # 定位项目根目录
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(current_dir)

    # 加载 config.yaml
    yaml_path = os.path.join(project_root, "configs", "config.yaml")
    if not os.path.exists(yaml_path):
        raise RuntimeError(f"配置文件 config.yaml 未找到: {yaml_path}")
    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            yaml_config = yaml.safe_load(f)
    except Exception as e:
        raise RuntimeError(f"加载配置文件时发生错误: {e}") from e

    llm_config = None
    if require_llm:
        # 只有在需要 LLM 的时候，才加载 .env 并校验 API Key
        load_dotenv(dotenv_path=os.path.join(project_root, ".env"))
        api_key = os.getenv("LLM_API_KEY")
        if not api_key:
            raise RuntimeError("LLM_API_KEY 未找到，请确保已在 .env 文件中设置")
        llm_config = _load_llm_config(yaml_config, project_root, api_key)

    pdf_renamer_config = None
    if require_pdf_renamer:
        pdf_renamer_config = _load_pdf_renamer_config(yaml_config, project_root)

    return AppConfig(
        llm=llm_config,
        pdf_renamer=pdf_renamer_config,
    )