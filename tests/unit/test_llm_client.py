"""
tests/unit/test_llm_client.py
------------------------------
Unit tests for llm/llm_client.py.

Rules:
- No real LLM API calls — all HTTP traffic is mocked.
- Tests are isolated and stateless (session counters reset where needed).
"""

from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

import pytest

import llm.llm_client as llm_mod
from llm.llm_client import (
    CLIENTS,
    AnthropicClient,
    GroqClient,
    OllamaClient,
    OpenAIClient,
    OpenRouterClient,
    get_llm_client,
    get_session_token_usage,
)
from utils.exceptions import LLMError, LLMUnavailableError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_session_tokens() -> None:
    llm_mod._session_tokens["prompt"] = 0
    llm_mod._session_tokens["completion"] = 0


# ---------------------------------------------------------------------------
# 1. get_llm_client with provider="groq" → returns GroqClient
# ---------------------------------------------------------------------------

def test_get_llm_client_returns_groq_when_key_set(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "sk-test-groq")
    config = {"llm": {"provider": "groq", "model": "llama-3.3-70b-versatile"}}
    client = get_llm_client(config)
    assert isinstance(client, GroqClient), (
        f"Expected GroqClient, got {type(client)}"
    )


# ---------------------------------------------------------------------------
# 2. get_llm_client with missing GROQ_API_KEY → falls back to OllamaClient
# ---------------------------------------------------------------------------

def test_get_llm_client_falls_back_to_ollama_on_missing_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    config = {"llm": {"provider": "groq", "model": "llama-3.3-70b-versatile"}}
    with patch.object(OllamaClient, "is_available", return_value=True):
        client = get_llm_client(config)
    assert isinstance(client, OllamaClient), (
        f"Expected OllamaClient fallback, got {type(client)}"
    )


def test_get_llm_client_returns_none_when_both_unavailable(monkeypatch):
    """Both primary provider and Ollama unavailable → None returned."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    config = {"llm": {"provider": "groq"}}
    with patch.object(OllamaClient, "is_available", return_value=False):
        client = get_llm_client(config)
    assert client is None


# ---------------------------------------------------------------------------
# 3. complete_with_fallback returns fallback string on exception, never raises
# ---------------------------------------------------------------------------

def test_complete_with_fallback_returns_fallback_string(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "sk-test")
    client = GroqClient(config={})
    with patch.object(client, "complete", side_effect=LLMError("boom")):
        result = client.complete_with_fallback("What is VCP?", fallback="N/A")
    assert result == "N/A"


def test_complete_with_fallback_does_not_reraise(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "sk-test")
    client = GroqClient(config={})
    with patch.object(client, "complete", side_effect=RuntimeError("network err")):
        # Must not raise
        result = client.complete_with_fallback("prompt", fallback="safe")
    assert result == "safe"


def test_complete_with_fallback_returns_actual_response_on_success(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "sk-test")
    client = GroqClient(config={})
    with patch.object(client, "complete", return_value="VCP detected"):
        result = client.complete_with_fallback("analyse RELIANCE", fallback="")
    assert result == "VCP detected"


# ---------------------------------------------------------------------------
# 4. OllamaClient.is_available() – True on reachable, False on OSError
# ---------------------------------------------------------------------------

def test_ollama_is_available_returns_true_when_socket_connects():
    mock_sock = MagicMock()
    with patch("socket.create_connection", return_value=mock_sock):
        client = OllamaClient(config={})
        assert client.is_available() is True
    mock_sock.close.assert_called_once()


def test_ollama_is_available_returns_false_on_connection_refused():
    with patch("socket.create_connection", side_effect=OSError("Connection refused")):
        client = OllamaClient(config={})
        assert client.is_available() is False


def test_ollama_is_available_returns_false_on_timeout():
    with patch(
        "socket.create_connection",
        side_effect=socket.timeout("timed out"),
    ):
        client = OllamaClient(config={})
        assert client.is_available() is False


# ---------------------------------------------------------------------------
# 5. All provider classes instantiate without error even with empty API key
#    (lazy validation — only fail on actual complete() call)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("client_cls", list(CLIENTS.values()))
def test_all_clients_instantiate_with_empty_env(client_cls, monkeypatch):
    """No provider should raise during __init__ when keys are absent."""
    for key in (
        "GROQ_API_KEY", "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY", "OPENROUTER_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    client = client_cls(config={})  # must not raise
    assert client is not None


@pytest.mark.parametrize("client_cls,env_var", [
    (GroqClient,        "GROQ_API_KEY"),
    (AnthropicClient,   "ANTHROPIC_API_KEY"),
    (OpenAIClient,      "OPENAI_API_KEY"),
    (OpenRouterClient,  "OPENROUTER_API_KEY"),
])
def test_cloud_clients_is_available_false_without_key(client_cls, env_var, monkeypatch):
    monkeypatch.delenv(env_var, raising=False)
    client = client_cls(config={})
    assert client.is_available() is False


@pytest.mark.parametrize("client_cls,env_var", [
    (GroqClient,        "GROQ_API_KEY"),
    (AnthropicClient,   "ANTHROPIC_API_KEY"),
    (OpenAIClient,      "OPENAI_API_KEY"),
    (OpenRouterClient,  "OPENROUTER_API_KEY"),
])
def test_cloud_clients_complete_raises_unavailable_without_key(
    client_cls, env_var, monkeypatch
):
    """complete() must raise LLMUnavailableError, not crash, when key absent."""
    monkeypatch.delenv(env_var, raising=False)
    client = client_cls(config={})
    with pytest.raises(LLMUnavailableError):
        client.complete("test prompt")


# ---------------------------------------------------------------------------
# 6. Mock GroqClient.complete() → verify get_session_token_usage increments
# ---------------------------------------------------------------------------

def test_session_token_usage_increments_after_groq_complete(monkeypatch):
    """After a successful complete(), both prompt and completion counters grow."""
    _reset_session_tokens()

    monkeypatch.setenv("GROQ_API_KEY", "sk-test-groq")
    client = GroqClient(config={"llm": {"model": "llama-3.3-70b-versatile"}})

    prompt = "Analyse the VCP setup for RELIANCE with Minervini criteria."
    fake_response = "3-contraction VCP detected; volume contraction confirms setup."

    # Build a mock openai client whose .chat.completions.create() returns
    # an object shaped like openai.types.chat.ChatCompletion
    mock_choice = MagicMock()
    mock_choice.message.content = fake_response

    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]

    mock_openai_instance = MagicMock()
    mock_openai_instance.chat.completions.create.return_value = mock_completion

    mock_openai_cls = MagicMock(return_value=mock_openai_instance)

    with patch("openai.OpenAI", mock_openai_cls):
        result = client.complete(prompt)

    assert result == fake_response, f"Unexpected response: {result!r}"

    usage = get_session_token_usage()
    assert usage["prompt"] > 0,     "Prompt token counter should have incremented"
    assert usage["completion"] > 0, "Completion token counter should have incremented"


def test_session_tokens_accumulate_across_calls(monkeypatch):
    """Each call to complete() adds to the running totals."""
    _reset_session_tokens()

    monkeypatch.setenv("GROQ_API_KEY", "sk-test-key")
    client = GroqClient(config={})

    mock_choice = MagicMock()
    mock_choice.message.content = "short reply"
    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]
    mock_instance = MagicMock()
    mock_instance.chat.completions.create.return_value = mock_completion

    with patch("openai.OpenAI", MagicMock(return_value=mock_instance)):
        client.complete("first prompt")
        after_first = get_session_token_usage()["prompt"]
        client.complete("second prompt that is longer than the first one")
        after_second = get_session_token_usage()["prompt"]

    assert after_second > after_first, (
        "Prompt counter should grow with each call"
    )
