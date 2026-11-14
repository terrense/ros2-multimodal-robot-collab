"""The hosted model client and the policy that asks it what to do.

No network: the client is exercised against a stubbed URL opener, and the
policy against a stubbed chat. What is checked here is the part that is
this repository's own: that a key is never taken from the repository, that
a provider is chosen from the environment, that the model's answer is
parsed, and that a model which asks for a capability this robot does not
have is refused before anything moves.
"""

from __future__ import annotations

import io
import json
import re
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from agent_harness.agent import hosted_model
from agent_harness.agent.hosted_model import PROVIDERS, chat_json, parse_json, resolve_provider
from agent_harness.agent.policies import HostedModelPolicy
from agent_harness.agent.policy import Decide, Finish


class _Response(io.BytesIO):
    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


def _stub_opener(payload: dict[str, Any], captured: list[dict[str, Any]]):
    def _open(request: urllib.request.Request, timeout: float | None = None):
        captured.append(
            {
                "url": request.full_url,
                "headers": dict(request.headers),
                "body": json.loads(request.data.decode("utf-8")),
            }
        )
        return _Response(json.dumps(payload).encode("utf-8"))

    return _open


@pytest.fixture(autouse=True)
def _no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for provider in PROVIDERS.values():
        monkeypatch.delenv(provider.key_env, raising=False)


def test_a_provider_is_chosen_by_its_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    provider = resolve_provider()
    assert provider.name == "deepseek"
    assert provider.base_url.startswith("https://api.deepseek.com")


def test_a_named_provider_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    assert resolve_provider("minimax").name == "minimax"


def test_the_environment_can_name_the_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    monkeypatch.setenv("MODEL_PROVIDER", "minimax")
    assert resolve_provider().name == "minimax"


def test_no_key_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(RuntimeError) as error:
        resolve_provider()
    assert "DEEPSEEK_API_KEY" in str(error.value)


def test_the_key_is_never_written_into_the_source() -> None:
    """A key in a public repository is a key that is lost."""
    source = Path(hosted_model.__file__).read_text(encoding="utf-8")
    assert "sk-" not in source
    assert not re.search(r"[\"'][A-Za-z0-9_\-]{32,}[\"']", source)


def test_the_chat_carries_the_key_in_the_header_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    captured: list[dict[str, Any]] = []
    payload = {"choices": [{"message": {"content": '{"ok": true}'}}]}
    monkeypatch.setattr(urllib.request, "urlopen", _stub_opener(payload, captured))

    answer = chat_json("do the thing", system="be careful")

    assert answer == {"ok": True}
    request = captured[0]
    assert request["url"].endswith("/chat/completions")
    assert request["headers"]["Authorization"] == "Bearer secret"
    assert "secret" not in json.dumps(request["body"])


def test_a_fenced_answer_is_still_json() -> None:
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('here you go: {"a": 1} thanks') == {"a": 1}


def test_an_answer_that_is_not_an_object_is_refused() -> None:
    with pytest.raises(RuntimeError):
        parse_json("[1, 2, 3]")


def test_the_policy_asks_for_one_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    monkeypatch.setattr(
        hosted_model,
        "chat_json",
        lambda *args, **kwargs: {
            "capability": "navigation.navigate_to_station",
            "arguments": {"station_id": "tool_shelf"},
            "reason": "the tool is on the shelf",
        },
    )
    policy = HostedModelPolicy(["navigation.navigate_to_station"])
    decision = policy.decide({"view": "anything"})
    assert isinstance(decision, Decide)
    assert decision.capability == "navigation.navigate_to_station"
    assert decision.arguments == {"station_id": "tool_shelf"}


def test_the_policy_refuses_a_capability_the_robot_does_not_have(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    monkeypatch.setattr(
        hosted_model,
        "chat_json",
        lambda *args, **kwargs: {"capability": "manipulation.launch_missiles"},
    )
    policy = HostedModelPolicy(["navigation.navigate_to_station"])
    decision = policy.decide({})
    assert isinstance(decision, Finish)
    assert decision.claimed_success is False
    assert "cannot do" in decision.reason


def test_the_policy_finishes_when_the_model_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    monkeypatch.setattr(
        hosted_model,
        "chat_json",
        lambda *args, **kwargs: {"finish": True, "claimed_success": True, "reason": "done"},
    )
    policy = HostedModelPolicy(["navigation.navigate_to_station"])
    decision = policy.decide({})
    assert isinstance(decision, Finish)
    assert decision.claimed_success is True
