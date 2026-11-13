"""运行时模型配置：管理员可在前端切换后端与模型，存库后立即生效，无需重启。"""

import json
import logging
import uuid
from typing import Dict, List

import requests
from sqlalchemy.orm import Session

from config.settings import settings
from database.models import SystemSetting

logger = logging.getLogger(__name__)

SETTING_KEY = "llm"
PROVIDERS = ("local", "deepseek", "mock")
PROVIDER_LABELS = {"local": "本地 Ollama", "deepseek": "DeepSeek", "mock": "离线兜底"}
DEEPSEEK_MODELS = ["deepseek-chat", "deepseek-reasoner"]
MAX_CUSTOM_MODELS = 50
MAX_CUSTOM_PROVIDERS = 10


def _defaults() -> Dict:
    provider = settings.llm_provider if settings.llm_provider in PROVIDERS else "local"
    return {
        "provider": provider,
        "models": {
            "local": settings.local_llm_model,
            "deepseek": settings.deepseek_model,
            "mock": "mock",
        },
        "custom_models": {},
        "custom_providers": {},
    }


def _normalize_base_url(url: str) -> str:
    """允许粘贴完整接口地址，统一去掉尾部斜杠和 /chat/completions。"""
    url = (url or "").strip().rstrip("/")
    if url.endswith("/chat/completions"):
        url = url[: -len("/chat/completions")].rstrip("/")
    return url


def get_llm_config(db: Session) -> Dict:
    config = _defaults()
    row = db.get(SystemSetting, SETTING_KEY)
    if row is None or not row.value:
        return {**config, "model": config["models"][config["provider"]]}

    try:
        data = json.loads(row.value)
    except json.JSONDecodeError:
        logger.warning("模型配置解析失败，回退默认值: %s", row.value)
        return {**config, "model": config["models"][config["provider"]]}

    for provider, item in (data.get("custom_providers") or {}).items():
        if provider in PROVIDERS or not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        base_url = _normalize_base_url(str(item.get("base_url") or ""))
        if not name or not base_url.startswith(("http://", "https://")):
            continue
        config["custom_providers"][provider] = {
            "name": name[:20],
            "base_url": base_url,
            "api_key": str(item.get("api_key") or "").strip(),
        }
        config["models"].setdefault(provider, "")

    known = set(PROVIDERS) | set(config["custom_providers"])
    if data.get("provider") in known:
        config["provider"] = data["provider"]
    for provider, model in (data.get("models") or {}).items():
        if provider in known and isinstance(model, str) and model.strip():
            config["models"][provider] = model.strip()
    for provider, models in (data.get("custom_models") or {}).items():
        if provider in known and isinstance(models, list):
            config["custom_models"][provider] = [
                model.strip()
                for model in models
                if isinstance(model, str) and model.strip()
            ][:MAX_CUSTOM_MODELS]

    # 自定义后端没有单独保存过选中模型时，默认取第一个可用模型
    for provider in config["custom_providers"]:
        if not config["models"].get(provider) and config["custom_models"].get(provider):
            config["models"][provider] = config["custom_models"][provider][0]
    return {**config, "model": config["models"].get(config["provider"], "")}


def _persist(db: Session, config: Dict) -> None:
    value = json.dumps(
        {
            "provider": config["provider"],
            "models": config["models"],
            "custom_models": config["custom_models"],
            "custom_providers": config["custom_providers"],
        },
        ensure_ascii=False,
    )
    row = db.get(SystemSetting, SETTING_KEY)
    if row is None:
        db.add(SystemSetting(key=SETTING_KEY, value=value))
    else:
        row.value = value
    db.flush()


def save_llm_config(db: Session, provider: str, model: str | None = None) -> Dict:
    config = get_llm_config(db)
    config["provider"] = provider
    if model:
        config["models"][provider] = model

    _persist(db, config)
    return {"provider": config["provider"], "model": config["models"].get(provider, "")}


def add_llm_model(db: Session, provider: str, model: str) -> Dict:
    """把管理员手动填写的模型名记到该后端下，返回最新配置（调用方负责 commit）。"""
    config = get_llm_config(db)
    models = config["custom_models"].setdefault(provider, [])
    if model not in models and len(models) < MAX_CUSTOM_MODELS:
        models.append(model)
    _persist(db, config)
    return config


def add_custom_provider(
    db: Session, name: str, base_url: str, api_key: str, model: str
) -> tuple:
    """新增（或按名称更新）一个 OpenAI 兼容后端，返回 (配置, 后端标识)。"""
    config = get_llm_config(db)
    base_url = _normalize_base_url(base_url)
    provider = next(
        (key for key, item in config["custom_providers"].items() if item["name"] == name),
        None,
    )
    if provider is None:
        if len(config["custom_providers"]) >= MAX_CUSTOM_PROVIDERS:
            raise ValueError("自定义后端数量已达上限")
        provider = f"custom_{uuid.uuid4().hex[:6]}"

    config["custom_providers"][provider] = {
        "name": name,
        "base_url": base_url,
        "api_key": api_key,
    }
    models = config["custom_models"].setdefault(provider, [])
    if model and model not in models:
        models.append(model)
    if model:
        config["models"][provider] = model
    elif not config["models"].get(provider) and models:
        config["models"][provider] = models[0]

    _persist(db, config)
    return config, provider


def provider_models(
    config: Dict, provider: str, local_models: List[str] | None = None
) -> List[str]:
    """后端可选模型 = 当前生效模型 + 内置/本地探测模型 + 管理员添加的模型。"""
    if provider == "local":
        return sorted(
            {
                config["models"]["local"],
                *(local_models or []),
                *config["custom_models"].get("local", []),
            }
        )
    if provider == "deepseek":
        return sorted(
            {
                config["models"]["deepseek"],
                *DEEPSEEK_MODELS,
                *config["custom_models"].get("deepseek", []),
            }
        )
    if provider in config["custom_providers"]:
        return sorted(
            {
                config["models"].get(provider, ""),
                *config["custom_models"].get(provider, []),
            }
            - {""}
        )
    return ["mock"]


def list_local_models() -> List[str]:
    """读取 Ollama 已拉取的模型；服务不可达时返回空列表（前端仍可手填模型名）。"""
    base = settings.local_llm_base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    try:
        response = requests.get(f"{base}/api/tags", timeout=3)
        response.raise_for_status()
        return sorted(
            item["name"] for item in response.json().get("models", []) if item.get("name")
        )
    except Exception as exc:  # 网络、超时、格式异常都按“不可用”处理
        logger.warning("读取 Ollama 模型列表失败: %s", exc)
        return []
