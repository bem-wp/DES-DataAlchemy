import os
from dataclasses import dataclass
from dotenv import load_dotenv
import yaml


@dataclass(frozen=True)
class AppConfig:

    api_key: str
    pdf_input_folder: str
    result_output_folder: str
    output_csv: str
    prompt_templates_path: str
    model_base_url: str
    model_name: str
    timeout: int = 300          

def load_config() -> AppConfig:

    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(current_dir)

    env_path = os.path.join(project_root, ".env")
    load_dotenv(dotenv_path=env_path)

    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        raise RuntimeError(
            "LLM_API_KEY 未找到，请确保已在 .env 文件中设置"
        )

    yaml_path = os.path.join(project_root, "configs", "config.yaml")
    if not os.path.exists(yaml_path):
        raise RuntimeError(
            f"配置文件 config.yaml 未找到，请确保路径正确: {yaml_path}"
        )

    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            yaml_config = yaml.safe_load(f)
    except Exception as e:
        raise RuntimeError(f"加载配置文件时发生错误: {e}") from e

    paths_cfg = yaml_config.get("paths") or {}
    prompt_cfg = yaml_config.get("prompt") or {}
    model_cfg = yaml_config.get("model") or {}

    required_fields = {
        "paths.input_folder":  paths_cfg.get("input_folder"),
        "paths.output_folder": paths_cfg.get("output_folder"),
        "paths.output_csv":    paths_cfg.get("output_csv"),
        "prompt.template":     prompt_cfg.get("template"),
        "model.base_url":     model_cfg.get("base_url"),
        "model.name":         model_cfg.get("name"),

    }
    missing = [key for key, val in required_fields.items() if not val]
    if missing:
        raise RuntimeError(
            f"config.yaml 中以下必填配置项缺失或为空: {', '.join(missing)}"
        )

    pdf_input_folder = os.path.join(project_root, paths_cfg["input_folder"])
    result_output_folder = os.path.join(project_root, paths_cfg["output_folder"])
    output_csv = os.path.join(project_root, paths_cfg["output_csv"])
    prompt_templates_path = os.path.join(project_root, prompt_cfg["template"])


    if not os.path.exists(pdf_input_folder):
        raise RuntimeError(f"输入文件夹不存在，请检查路径: {pdf_input_folder}")
    if not os.path.exists(prompt_templates_path):
        raise RuntimeError(f"提示模板文件不存在，请检查路径: {prompt_templates_path}")

    return AppConfig(
        api_key=api_key,
        pdf_input_folder=pdf_input_folder,
        result_output_folder=result_output_folder,
        output_csv=output_csv,
        prompt_templates_path=prompt_templates_path,
        model_base_url=model_cfg["base_url"],
        model_name=model_cfg["name"],
        timeout=int(model_cfg.get("timeout", 300)),
    )
