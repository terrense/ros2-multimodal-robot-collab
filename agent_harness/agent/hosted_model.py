"""A hosted model, reached over an OpenAI-compatible API.

DeepSeek and MiniMax speak the same protocol: a chat completion with a
system prompt and a user prompt, and a JSON object back. One client
covers both, and the difference between them is a base URL and a model
name.

The API key comes from the environment and never from the repository:
this repository is public, and a key that is in it is a key that is
lost. `MINIMAX_API_KEY` and `DEEPSEEK_API_KEY` are read, in that order,
unless a provider is named.

Nothing here decides anything. The model is asked for a suggestion and
the suggestion is checked against what the harness can actually do
before it goes anywhere near a robot.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Provider:
    """One hosted model, and where it lives."""

    name: str
    base_url: str
    model: str
    key_env: str

    def api_key(self) -> str:
        key = os.getenv(self.key_env, "").strip()
        if not key:
            raise RuntimeError(
                f"{self.key_env} is not set. Export it, or put it in a local "
                ".env file. It is not kept in the repository."
            )
        return key


#: The hosted models this harness knows how to talk to.
PROVIDERS: dict[str, Provider] = {
    "minimax": Provider(
        name="minimax",
        base_url="https://api.minimax.chat/v1",
        model="MiniMax-M3",
        key_env="MINIMAX_API_KEY",
    ),
    "deepseek": Provider(
        name="deepseek",
        base_url="https://api.deepseek.com/v1",
        # The flash model. The pro one is better and costs several times
        # more, and for planning a mission plan the difference is not
        # worth the money.
        model="deepseek-flash",
        key_env="DEEPSEEK_API_KEY",
    ),
}


def resolve_provider(name: str | None = None) -> Provider:
    """The provider to use, and its settings.

    A named provider wins. Otherwise the first one whose key is in the
    environment wins, so a machine with one key configured does not have
    to be told which model it is talking to.
    """
    if name is None:
        name = os.getenv("MODEL_PROVIDER", "").strip() or None
    if name:
        provider = PROVIDERS.get(name.strip().lower())
        if provider is None:
            known = ", ".join(sorted(PROVIDERS))
            raise RuntimeError(f"unknown model provider {name!r}; known: {known}")
        return provider
    for provider in PROVIDERS.values():
        if os.getenv(provider.key_env, "").strip():
            return provider
    expected = ", ".join(provider.key_env for provider in PROVIDERS.values())
    raise RuntimeError(
        f"no model API key is configured. Set one of {expected} in the "
        "environment, or put it in a local .env file."
    )


def chat(
    prompt: str,
    *,
    system: str,
    provider: Provider | None = None,
    temperature: float = 0.1,
    timeout_sec: float = 60.0,
    json_mode: bool = True,
) -> str:
    """One chat completion, and the text the model answered with."""
    chosen = provider or resolve_provider()
    url = chosen.base_url.rstrip("/") + "/chat/completions"
    payload: dict[str, Any] = {
        "model": chosen.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {chosen.api_key()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"{chosen.name} returned HTTP {error.code}: {detail[:600]}"
        ) from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"{chosen.name} request failed: {error}") from error
    try:
        return str(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as error:
        raise RuntimeError(
            f"unexpected {chosen.name} response shape: "
            f"{json.dumps(data, ensure_ascii=False)[:600]}"
        ) from error


def parse_json(text: str) -> dict[str, Any]:
    """The JSON object the model answered with."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(stripped[start : end + 1])
    if not isinstance(parsed, dict):
        raise RuntimeError(f"expected a JSON object, got {type(parsed).__name__}")
    return parsed


def chat_json(
    prompt: str,
    *,
    system: str,
    provider: Provider | None = None,
    temperature: float = 0.1,
    timeout_sec: float = 60.0,
) -> dict[str, Any]:
    """One chat completion, and the JSON object the model answered with."""
    return parse_json(
        chat(
            prompt,
            system=system,
            provider=provider,
            temperature=temperature,
            timeout_sec=timeout_sec,
        )
    )
