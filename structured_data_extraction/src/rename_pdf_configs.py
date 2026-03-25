import os
import yaml
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class AppConfig:
    pdf_dir: str
    rate_limit_sleep: float
    api_timeout: int
    user_agent: str
    log_level: str
    retry_times: int


def load_config() -> AppConfig:
    # 定位配置文件
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(current_dir)

    yaml_path = os.path.join(project_root, "configs", "config.yaml")
    if not os.path.exists(yaml_path):
        raise RuntimeError(f"配置文件 config.yaml 未找到，请确保路径正确: {yaml_path}")

    # 加载 YAML
    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            yaml_config = yaml.safe_load(f)
    except Exception as e:
        raise RuntimeError(f"加载配置文件时发生错误: {e}") from e

    # 提取 pdf_renamer 部分
    pdf_renamer = yaml_config.get("pdf_renamer")
    if not pdf_renamer:
        raise RuntimeError("config.yaml 中缺少 'pdf_renamer' 配置节")

    # 定义必要字段及默认值
    required_fields = {
        "pdf_dir": pdf_renamer.get("pdf_dir"),
        # "rate_limit_sleep": pdf_renamer.get("rate_limit_sleep"),
        # "api_timeout": pdf_renamer.get("api_timeout"),
        # "user_agent": pdf_renamer.get("user_agent"),
        # "log_level": pdf_renamer.get("log_level"),
        # "retry_times": pdf_renamer.get("retry_times"),
    }

    missing = [key for key, val in required_fields.items() if val is None]
    if missing:
        raise RuntimeError(
            f"pdf_renamer 配置节中缺少以下必填字段: {', '.join(missing)}"
        )

    # 校验必填字段类型
    try:
        pdf_dir = str(required_fields["pdf_dir"])
    except (ValueError, TypeError) as e:
        raise RuntimeError(f"配置字段类型错误: {e}") from e

    # 可选字段，取不到时使用默认值
    rate_limit_sleep = float(pdf_renamer.get("rate_limit_sleep", 1.0))
    api_timeout      = int(pdf_renamer.get("api_timeout", 30))
    user_agent       = str(pdf_renamer.get("user_agent", ""))
    log_level        = str(pdf_renamer.get("log_level", "INFO"))
    retry_times      = int(pdf_renamer.get("retry_times", 3))

    # 可选：验证目录是否存在
    full_pdf_dir = os.path.join(project_root, pdf_dir)
    if not os.path.exists(full_pdf_dir):
        raise RuntimeError(f"PDF 文件夹不存在: {full_pdf_dir}")

    return AppConfig(
        pdf_dir=full_pdf_dir,
        rate_limit_sleep=rate_limit_sleep,
        api_timeout=api_timeout,
        user_agent=user_agent,
        log_level=log_level,
        retry_times=retry_times,
    )