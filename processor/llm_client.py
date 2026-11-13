"""大模型客户端：本地 Ollama / DeepSeek / 自定义 OpenAI 兼容后端 + mock 兜底。

四者都走 OpenAI 兼容协议（POST {base_url}/chat/completions），
所以只有一个实现，差异仅在 base_url / model / key / 超时。
"""

import logging
from typing import Dict, List, Optional

import requests

from config.settings import settings

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(
        self,
        provider: str | None = None,
        model: str | None = None,
        provider_config: Optional[Dict] = None,
    ):
        self.provider = (provider or settings.llm_provider).strip().lower()
        self.model = (model or "").strip() or None
        self.provider_config = {
            key: value
            for key, value in (provider_config or {}).items()
            if isinstance(value, dict)
        }
        self.last_provider: str | None = None

    def _chain(self) -> List[str]:
        """降级顺序：指定后端 → DeepSeek（仅本地失败且配了 key）→ mock。"""
        if self.provider == "mock":
            return ["mock"]

        chain = [self.provider]
        if self.provider == "local" and settings.deepseek_api_key.strip():
            chain.append("deepseek")
        if settings.llm_fallback_to_mock:
            chain.append("mock")
        return chain

    def chat(self, messages: List[dict], mock_answer: str, temperature: float = 0.3) -> str:
        last_error: Exception | None = None
        for name in self._chain():
            try:
                if name == "mock":
                    self.last_provider = "mock"
                    return mock_answer
                content = self._remote(name, messages, temperature)
                self.last_provider = name
                return content
            except Exception as exc:  # 网络、超时、4xx/5xx 都统一降级
                last_error = exc
                logger.warning("LLM 调用失败 provider=%s: %s", name, exc)
        raise LLMError(f"所有模型后端均不可用: {last_error}")

    def _remote(self, name: str, messages: List[dict], temperature: float) -> str:
        if name == "local":
            base_url = settings.local_llm_base_url.rstrip("/")
            model = self.model or settings.local_llm_model
            api_key = "ollama"
            timeout = settings.local_llm_timeout
        elif name == "deepseek":
            base_url = settings.deepseek_base_url.rstrip("/")
            model = self.model or settings.deepseek_model
            api_key = settings.deepseek_api_key
            timeout = settings.deepseek_timeout
            if not api_key:
                raise LLMError("DEEPSEEK_API_KEY 未配置")
        elif name in self.provider_config:
            item = self.provider_config[name]
            base_url = str(item.get("base_url") or "").strip().rstrip("/")
            model = self.model or ""
            api_key = str(item.get("api_key") or "").strip() or "not-needed"
            timeout = settings.local_llm_timeout
            if not base_url.startswith(("http://", "https://")):
                raise LLMError(f"自定义后端 {name} 的 Base URL 无效")
            if not model:
                raise LLMError(f"自定义后端 {name} 未配置模型")
        else:
            raise LLMError(f"未知模型后端: {name}")

        response = requests.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "stream": False,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        content = (data["choices"][0]["message"]["content"] or "").strip()
        if not content:
            raise LLMError("模型返回空内容")
        return content
