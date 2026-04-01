from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class Settings:
    app_name: str
    app_env: str
    data_dir: Path
    enable_langgraph: bool
    enable_autogen: bool
    default_prompt_profile: str
    auto_execute_safe_actions: bool
    openai_api_key: str | None
    openai_model: str


def get_settings() -> Settings:
    load_dotenv()
    base_dir = Path(__file__).resolve().parents[1]
    data_dir = base_dir / os.getenv("DATA_DIR", "data")
    data_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        app_name=os.getenv("APP_NAME", "AI Incident Commander"),
        app_env=os.getenv("APP_ENV", "development"),
        data_dir=data_dir,
        enable_langgraph=_as_bool(os.getenv("ENABLE_LANGGRAPH"), True),
        enable_autogen=_as_bool(os.getenv("ENABLE_AUTOGEN"), True),
        default_prompt_profile=os.getenv("DEFAULT_PROMPT_PROFILE", "balanced-v1"),
        auto_execute_safe_actions=_as_bool(os.getenv("AUTO_EXECUTE_SAFE_ACTIONS"), False),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
    )
