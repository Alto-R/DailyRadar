"""
config_loader.py - 加载 config.yaml + .env，返回统一配置字典
"""

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

# 容器内路径 / 本地开发路径
_BASE_DIR = Path(os.environ.get("APP_BASE_DIR", Path(__file__).parent))
_CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", _BASE_DIR.parent / "config" / "config.yaml"))
_ENV_PATH = _BASE_DIR.parent / ".env"


def load_config() -> dict:
    """
    加载并合并 config.yaml + .env，返回 AppConfig dict。
    .env 文件仅在本地开发时使用，Docker 中由 docker-compose 注入 env vars。
    """
    load_dotenv(_ENV_PATH, override=False)

    if not _CONFIG_PATH.exists():
        raise FileNotFoundError(f"配置文件未找到: {_CONFIG_PATH}")

    with open(_CONFIG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    if not cfg:
        raise ValueError("config.yaml 为空或格式错误")

    # 确保必要的顶层 key 存在
    cfg.setdefault("app", {})
    cfg.setdefault("schedules", [])
    cfg.setdefault("collectors", {})
    cfg.setdefault("ai", {})
    cfg.setdefault("agent", {})
    cfg.setdefault("notifications", {})
    cfg.setdefault("storage", {})

    # agent 默认配置（集中在配置层，避免业务代码写死默认值）
    cfg["agent"].setdefault("max_steps", 6)
    cfg["agent"].setdefault("max_steps_hard_limit", 20)
    cfg["agent"].setdefault("schedule_allow_side_effects", True)
    cfg["agent"].setdefault("recent_turns_context_limit", 6)
    cfg["agent"].setdefault("policy", {})
    cfg["agent"]["policy"].setdefault("allow_tools", [])
    cfg["agent"]["policy"].setdefault("deny_tools", [])
    cfg["agent"]["policy"].setdefault("allow_side_effects", False)

    # 注入 storage data_dir（容器内固定路径或本地 data/）
    if not cfg["storage"].get("data_dir"):
        cfg["storage"]["data_dir"] = str(_BASE_DIR.parent / "data")

    # personal YAML 文件路径
    cfg["_personal_dir"] = str(_CONFIG_PATH.parent / "personal")

    return cfg
