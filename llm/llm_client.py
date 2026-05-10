"""
llm/llm_client.py
-----------------
LLM client abstraction for the Minervini SEPA AI stock system.

Provides:
- LLMClient abstract base class with complete_with_fallback mixin
- 6 provider adapters: Groq, Anthropic, OpenAI, OpenRouter, Nvidia NIM, Ollama
- Factory function get_llm_client(config) with availability-based fallback
- Session-level token usage tracking (_session_tokens)
"""

from __future__ import annotations

import os
import socket
from abc import ABC, abstractmethod
from typing import Optional

from utils.exceptions import LLMError, LLMUnavailableError
from utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Session token usage tracking (rough estimate: ~4 chars per token)
# ---------------------------------------------------------------------------

_session_tokens: dict[str, int] = {"prompt": 0, "completion": 0}


def get_session_token_usage() -> dict:
    """Returns current session token usage estimate."""
    return dict(_session_tokens)


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _track_usage(prompt: str, response: str) -> None:
    _session_tokens["prompt"] += _estimate_tokens(prompt)
    _session_tokens["completion"] += _estimate_tokens(response)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class LLMClient(ABC):
    """Abstract base class for all LLM provider clients."""

    @abstractmethod
    def complete(self, prompt: str, max_tokens: int = 350) -> str:
        """
        Send prompt and return response text.
        Raises LLMError (from utils/exceptions.py) on unrecoverable failure.
        Raises LLMUnavailableError if API key missing or service unreachable.
        """

    @abstractmethod
    def is_available(self) -> bool:
        """Returns True if client is configured and reachable."""

    def complete_with_fallback(
        self, prompt: str, fallback: str = "", max_tokens: int = 350
    ) -> str:
        """
        Calls complete(). Returns fallback string on any exception.
        Logs a warning with the error. Never raises.
        """
        try:
            return self.complete(prompt, max_tokens=max_tokens)
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM complete_with_fallback caught exception: %s", exc)
            return fallback


# ---------------------------------------------------------------------------
# Provider: Groq
# ---------------------------------------------------------------------------


class GroqClient(LLMClient):
    """
    Groq API via OpenAI-compatible endpoint.
    Model default: llama-3.3-70b-versatile
    API key: GROQ_API_KEY (lazy — only validated on complete())
    """

    PROVIDER = "groq"

    def __init__(self, config: dict | None = None) -> None:
        self._config = config or {}
        self._model: str = self._config.get("llm", {}).get(
            "model", "llama-3.3-70b-versatile"
        )
        self._api_key: str = os.environ.get("GROQ_API_KEY", "")

    def is_available(self) -> bool:
        return bool(self._api_key)

    def complete(self, prompt: str, max_tokens: int = 350) -> str:
        if not self._api_key:
            raise LLMUnavailableError("GROQ_API_KEY not set", detail="provider=groq")
        try:
            import openai

            client = openai.OpenAI(
                api_key=self._api_key,
                base_url="https://api.groq.com/openai/v1",
            )
            resp = client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
            )
            response_text: str = resp.choices[0].message.content or ""
            logger.debug(
                "LLM [groq] prompt=%d chars, response=%d chars",
                len(prompt), len(response_text),
            )
            _track_usage(prompt, response_text)
            return response_text
        except LLMUnavailableError:
            raise
        except Exception as exc:
            raise LLMError(f"Groq completion failed: {exc}", detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Provider: Anthropic
# ---------------------------------------------------------------------------


class AnthropicClient(LLMClient):
    """
    Anthropic Claude API.
    Model default: claude-haiku-4-5 (cheapest, fastest)
    API key: ANTHROPIC_API_KEY
    """

    PROVIDER = "anthropic"

    def __init__(self, config: dict | None = None) -> None:
        self._config = config or {}
        self._model: str = self._config.get("llm", {}).get("model", "claude-haiku-4-5")
        self._api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")

    def is_available(self) -> bool:
        return bool(self._api_key)

    def complete(self, prompt: str, max_tokens: int = 350) -> str:
        if not self._api_key:
            raise LLMUnavailableError(
                "ANTHROPIC_API_KEY not set", detail="provider=anthropic"
            )
        try:
            import anthropic

            client = anthropic.Anthropic(api_key=self._api_key)
            msg = client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            response_text: str = msg.content[0].text if msg.content else ""
            logger.debug(
                "LLM [anthropic] prompt=%d chars, response=%d chars",
                len(prompt), len(response_text),
            )
            _track_usage(prompt, response_text)
            return response_text
        except LLMUnavailableError:
            raise
        except Exception as exc:
            raise LLMError(
                f"Anthropic completion failed: {exc}", detail=str(exc)
            ) from exc


# ---------------------------------------------------------------------------
# Provider: OpenAI
# ---------------------------------------------------------------------------


class OpenAIClient(LLMClient):
    """
    OpenAI API.
    Model default: gpt-4o-mini
    API key: OPENAI_API_KEY
    """

    PROVIDER = "openai"

    def __init__(self, config: dict | None = None) -> None:
        self._config = config or {}
        self._model: str = self._config.get("llm", {}).get("model", "gpt-4o-mini")
        self._api_key: str = os.environ.get("OPENAI_API_KEY", "")

    def is_available(self) -> bool:
        return bool(self._api_key)

    def complete(self, prompt: str, max_tokens: int = 350) -> str:
        if not self._api_key:
            raise LLMUnavailableError(
                "OPENAI_API_KEY not set", detail="provider=openai"
            )
        try:
            import openai

            client = openai.OpenAI(api_key=self._api_key)
            resp = client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
            )
            response_text: str = resp.choices[0].message.content or ""
            logger.debug(
                "LLM [openai] prompt=%d chars, response=%d chars",
                len(prompt), len(response_text),
            )
            _track_usage(prompt, response_text)
            return response_text
        except LLMUnavailableError:
            raise
        except Exception as exc:
            raise LLMError(
                f"OpenAI completion failed: {exc}", detail=str(exc)
            ) from exc


# ---------------------------------------------------------------------------
# Provider: OpenRouter
# ---------------------------------------------------------------------------


class OpenRouterClient(LLMClient):
    """
    OpenRouter API (OpenAI-compatible).
    Model default: deepseek/deepseek-r1:free (best reasoning, free tier)
    API key: OPENROUTER_API_KEY
    Extra headers: HTTP-Referer for attribution
    """

    PROVIDER = "openrouter"

    def __init__(self, config: dict | None = None) -> None:
        self._config = config or {}
        self._model: str = self._config.get("llm", {}).get(
            "model", "deepseek/deepseek-r1:free"
        )
        self._api_key: str = os.environ.get("OPENROUTER_API_KEY", "")

    def is_available(self) -> bool:
        return bool(self._api_key)

    def complete(self, prompt: str, max_tokens: int = 350) -> str:
        if not self._api_key:
            raise LLMUnavailableError(
                "OPENROUTER_API_KEY not set", detail="provider=openrouter"
            )
        try:
            import openai

            client = openai.OpenAI(
                api_key=self._api_key,
                base_url="https://openrouter.ai/api/v1",
                default_headers={"HTTP-Referer": "https://github.com/sepa-ai"},
            )
            resp = client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
            )
            response_text: str = resp.choices[0].message.content or ""
            logger.debug(
                "LLM [openrouter] prompt=%d chars, response=%d chars",
                len(prompt), len(response_text),
            )
            _track_usage(prompt, response_text)
            return response_text
        except LLMUnavailableError:
            raise
        except Exception as exc:
            raise LLMError(
                f"OpenRouter completion failed: {exc}", detail=str(exc)
            ) from exc


# ---------------------------------------------------------------------------
# Provider: Nvidia NIM
# ---------------------------------------------------------------------------


class NvidiaClient(LLMClient):
    """
    NVIDIA NIM API via OpenAI-compatible endpoint.

    NIM hosts a catalogue of open models (Llama 3, Mistral, Mixtral, etc.)
    accessible at https://integrate.api.nvidia.com/v1.

    API key  : NVIDIA_API_KEY  (generate at https://build.nvidia.com)
    Model    : config["llm"]["model"]
               Defaults to "meta/llama-3.3-70b-instruct" — a strong free-tier
               model.  Other good options:
                 "mistralai/mistral-7b-instruct-v0.3"   (fast, cheap)
                 "mistralai/mixtral-8x7b-instruct-v0.1" (high quality)
                 "nvidia/llama-3.1-nemotron-70b-instruct" (nvidia flagship)
    """

    PROVIDER = "nvidia"
    _BASE_URL = "https://integrate.api.nvidia.com/v1"

    def __init__(self, config: dict | None = None) -> None:
        self._config = config or {}
        self._model: str = self._config.get("llm", {}).get(
            "model", "meta/llama-3.3-70b-instruct"
        )
        self._api_key: str = os.environ.get("NVIDIA_API_KEY", "")

    def is_available(self) -> bool:
        return bool(self._api_key)

    def complete(self, prompt: str, max_tokens: int = 350) -> str:
        if not self._api_key:
            raise LLMUnavailableError(
                "NVIDIA_API_KEY not set", detail="provider=nvidia"
            )
        try:
            import openai

            client = openai.OpenAI(
                api_key=self._api_key,
                base_url=self._BASE_URL,
            )
            resp = client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                # NIM supports temperature / top_p; keep defaults sensible
                temperature=0.7,
                top_p=0.9,
            )
            response_text: str = resp.choices[0].message.content or ""
            logger.debug(
                "LLM [nvidia] model=%s prompt=%d chars, response=%d chars",
                self._model, len(prompt), len(response_text),
            )
            _track_usage(prompt, response_text)
            return response_text
        except LLMUnavailableError:
            raise
        except Exception as exc:
            raise LLMError(
                f"Nvidia NIM completion failed: {exc}", detail=str(exc)
            ) from exc


# ---------------------------------------------------------------------------
# Provider: Ollama (local, zero cost)
# ---------------------------------------------------------------------------


class OllamaClient(LLMClient):
    """
    Local Ollama instance.
    No API key needed.
    Endpoint: http://localhost:11434/api/chat
    Model default: from config["llm"]["model"] (e.g. "llama3.2")
    is_available(): quick socket probe with timeout=1s
    """

    PROVIDER = "ollama"

    def __init__(self, config: dict | None = None) -> None:
        self._config = config or {}
        self._model: str = self._config.get("llm", {}).get("model", "llama3.2")
        self._base_url = "http://localhost:11434"

    def is_available(self) -> bool:
        """Returns True if localhost:11434 is reachable AND the model is pulled.

        Two-stage check:
          1. Socket probe  — confirms Ollama daemon is running (timeout=1s).
          2. GET /api/tags — confirms the configured model is actually installed.
             A running Ollama with no models still passes the socket check but
             returns 404 on /api/chat, causing a confusing mid-request failure.
        """
        try:
            sock = socket.create_connection(("localhost", 11434), timeout=1)
            sock.close()
        except OSError:
            return False

        # Verify the model is pulled — /api/tags lists all local models
        try:
            import requests as _req

            resp = _req.get(f"{self._base_url}/api/tags", timeout=3)
            if resp.status_code != 200:
                logger.warning(
                    "OllamaClient.is_available: /api/tags returned %d", resp.status_code
                )
                return False

            # Model names from Ollama include an optional tag (e.g. "llama3.2:latest").
            # Strip the tag for a loose match so "llama3.2" matches "llama3.2:latest".
            configured = self._model.split(":")[0].lower()
            pulled = [
                m.get("name", "").split(":")[0].lower()
                for m in resp.json().get("models", [])
            ]
            if configured not in pulled:
                logger.warning(
                    "OllamaClient.is_available: model '%s' not found in pulled models %s. "
                    "Run: ollama pull %s",
                    self._model, pulled, self._model,
                )
                return False

            return True
        except OSError:
            # Requests not installed or network error — treat Ollama as unavailable
            return False

    def complete(self, prompt: str, max_tokens: int = 350) -> str:
        if not self.is_available():
            raise LLMUnavailableError(
                "Ollama not reachable at localhost:11434", detail="provider=ollama"
            )
        try:
            import requests

            payload = {
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"num_predict": max_tokens},
            }
            resp = requests.post(
                f"{self._base_url}/api/chat", json=payload, timeout=30
            )
            resp.raise_for_status()
            response_text: str = resp.json()["message"]["content"]
            logger.debug(
                "LLM [ollama] prompt=%d chars, response=%d chars",
                len(prompt), len(response_text),
            )
            _track_usage(prompt, response_text)
            return response_text
        except LLMUnavailableError:
            raise
        except Exception as exc:
            raise LLMError(
                f"Ollama completion failed: {exc}", detail=str(exc)
            ) from exc


# ---------------------------------------------------------------------------
# Registry & factory
# ---------------------------------------------------------------------------

CLIENTS: dict[str, type[LLMClient]] = {
    "groq": GroqClient,
    "anthropic": AnthropicClient,
    "openai": OpenAIClient,
    "openrouter": OpenRouterClient,
    "nvidia": NvidiaClient,
    "ollama": OllamaClient,
}


def get_llm_client(config: dict) -> Optional[LLMClient]:
    """
    Returns the configured LLM client based on config["llm"]["provider"].

    Fallback chain:
      1. Configured provider   → if is_available()
      2. OllamaClient          → if is_available()
      3. None                  → caller (explainer) must handle gracefully
    """
    provider = config.get("llm", {}).get("provider", "groq").lower()
    client_cls = CLIENTS.get(provider, GroqClient)
    client = client_cls(config=config)

    if client.is_available():
        logger.info("LLM client ready: provider=%s model=%s", provider,
                    getattr(client, "_model", "?"))
        return client

    logger.warning(
        "LLM provider '%s' not available; attempting Ollama fallback.", provider
    )
    ollama = OllamaClient(config=config)
    if ollama.is_available():
        logger.info("LLM client ready: fallback provider=ollama")
        return ollama

    logger.warning(
        "Ollama also unavailable. LLM client returning None — "
        "AI explanations will be disabled this session."
    )
    return None
