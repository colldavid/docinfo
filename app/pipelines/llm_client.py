"""
Provider-agnostic LLM client.

All reasoning-layer calls go through call_llm() so the model and provider are
configured in exactly one place (app/config.py). This keeps the project from
being tied to a single vendor: a firm that mandates Azure OpenAI / a Copilot
tenant can point llm_provider="openai" + llm_base_url at their gateway without
touching pipeline code.

Supported providers:
  - "anthropic"  (default) — Anthropic Messages API
  - "openai"     — OpenAI-compatible Chat Completions (covers Azure OpenAI,
                   enterprise gateways, and self-hosted OpenAI-compatible servers)

The two APIs differ in how the system prompt is passed; call_llm() normalizes
that so callers use one signature regardless of provider.
"""

import logging

from app.config import settings

logger = logging.getLogger(__name__)

_client = None  # cached provider client (created once per process)


def _get_client():
    global _client
    if _client is not None:
        return _client

    if settings.llm_provider == "anthropic":
        import anthropic
        _client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key,
            base_url=settings.llm_base_url,  # None → default endpoint
        )
    elif settings.llm_provider == "openai":
        from openai import OpenAI
        _client = OpenAI(
            api_key=settings.openai_api_key or settings.anthropic_api_key,
            base_url=settings.llm_base_url,  # None → default; set for Azure/gateway
        )
    else:
        raise ValueError(f"Unknown llm_provider: {settings.llm_provider!r}")

    return _client


def call_llm(
    user_message: str,
    system: str | None = None,
    max_tokens: int = 512,
    temperature: float = 0.0,
) -> str:
    """
    Send a single-turn message and return the model's text response.

    Normalizes across providers: the same call works whether the backend is
    Anthropic or an OpenAI-compatible endpoint.
    """
    client = _get_client()
    model = settings.llm_model

    if settings.llm_provider == "anthropic":
        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": user_message}],
        }
        if system:
            kwargs["system"] = system
        resp = client.messages.create(**kwargs)
        return resp.content[0].text.strip()

    # openai-compatible
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user_message})
    resp = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        messages=messages,
    )
    return resp.choices[0].message.content.strip()
