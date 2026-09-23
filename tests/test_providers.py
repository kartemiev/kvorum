"""Provider layer tests: fallback, budget-retry, missing keys (no network)."""

from __future__ import annotations

import json

import httpx

from kvorum.panel import Fallback, Panel, ProviderConfig, Seat, load_panel
from kvorum.providers import call_seat

from conftest import answer_text


def _providers() -> dict[str, ProviderConfig]:
    return {
        "primary": ProviderConfig("primary", "https://p.example/v1", ("PRIMARY_KEY",)),
        "backup": ProviderConfig("backup", "https://b.example/v1", ("BACKUP_KEY",)),
    }


def _seat() -> Seat:
    return Seat("s1", "Seat 1", "role", "primary", "primary-model",
                max_tokens=20000, fallback=Fallback("backup", "backup-model"))


def _env() -> dict[str, str]:
    return {"PRIMARY_KEY": "sk-primary-12345678", "BACKUP_KEY": "sk-backup-12345678"}


def _panel() -> Panel:
    return Panel(providers=_providers(), seats=[_seat()], quorum_required=1)


def test_primary_success_no_fallback():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {"content": answer_text()}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4},
        })

    result = call_seat(_seat(), _panel(), "prompt", _env(),
                       transport=httpx.MockTransport(handler))
    assert result["fallback_used"] is False
    assert result["provider"] == "primary"
    assert result["text"].startswith("## Verdict")
    assert result["usage"] == {"prompt_tokens": 10, "completion_tokens": 4}
    assert len(result["attempts"]) == 1
    assert seen[0]["temperature"] == 0.2
    assert seen[0]["max_tokens"] == 20000


def test_fallback_when_primary_returns_no_text():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["model"] == "primary-model":
            return httpx.Response(500, text='{"error": "provider outage"}')
        return httpx.Response(200, json={
            "choices": [{"message": {"content": answer_text()}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        })

    result = call_seat(_seat(), _panel(), "prompt", _env(),
                       transport=httpx.MockTransport(handler))
    assert result["fallback_used"] is True
    assert result["provider"] == "backup"
    assert result["model"] == "backup-model"
    assert result["text"].startswith("## Verdict")
    assert len(result["attempts"]) == 2
    assert result["attempts"][0]["http"] == 500
    assert result["attempts"][1]["fallback"] is True
    assert "provider outage" in result["primary_failure"]


def test_budget_retry_on_400():
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body["max_tokens"])
        if body["max_tokens"] == 20000:
            return httpx.Response(400, text='{"error": "max_tokens too large"}')
        return httpx.Response(200, json={
            "choices": [{"message": {"content": answer_text()}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 2},
        })

    panel = _panel()
    panel.max_tokens_retry = 1000
    result = call_seat(_seat(), panel, "prompt", _env(),
                       transport=httpx.MockTransport(handler))
    assert result["text"].startswith("## Verdict")
    assert result["fallback_used"] is False
    assert seen == [20000, 1000]
    assert len(result["attempts"]) == 1
    assert result["attempts"][0]["budget_retry"] is True
    assert result["attempts"][0]["max_tokens"] == 1000


def test_no_retry_when_budget_not_larger():
    # If seat.max_tokens <= budget, a 400 is NOT retried with a smaller cap.
    panel = _panel()
    panel.max_tokens_retry = 20000  # equal, not greater
    result = call_seat(_seat(), panel, "prompt", _env(),
                       transport=httpx.MockTransport(
                           lambda r: httpx.Response(400, text='{"error": "bad"}')))
    assert result["text"] == ""
    assert result["attempts"][0]["budget_retry"] is False


def test_missing_keys_record_attempts():
    result = call_seat(_seat(), _panel(), "prompt", {},
                       transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    assert result["text"] == ""
    assert len(result["attempts"]) == 2
    assert all(a["key_source"] == "MISSING" for a in result["attempts"])


def test_fallback_disabled():
    result = call_seat(_seat(), _panel(), "prompt", _env(),
                       use_fallback=False,
                       transport=httpx.MockTransport(
                           lambda r: httpx.Response(500, text="boom")))
    assert result["text"] == ""
    assert len(result["attempts"]) == 1  # no fallback attempt


def test_fallback_uses_provider_specific_model_name():
    """The fallback is called with ITS OWN model name, never the primary's."""
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append((request.url.host, body["model"]))
        if body["model"] == "primary-model":
            return httpx.Response(500, text="provider outage")
        return httpx.Response(200, json={
            "choices": [{"message": {"content": answer_text()}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    seat = Seat("s1", "Seat 1", "role", "primary", "primary-model",
                fallback=[Fallback("backup", "backup-model")])
    panel = Panel(providers=_providers(), seats=[seat], quorum_required=1)

    result = call_seat(seat, panel, "prompt", _env(),
                       transport=httpx.MockTransport(handler))

    assert result["fallback_used"] is True
    assert result["provider"] == "backup"
    assert result["model"] == "backup-model"
    assert seen == [("p.example", "primary-model"), ("b.example", "backup-model")]


def test_fallback_inherits_parent_model_when_omitted():
    """A fallback without an explicit `model` uses the seat's model (parent default)."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["model"])
        if len(seen) == 1:
            return httpx.Response(500, text="provider outage")
        return httpx.Response(200, json={
            "choices": [{"message": {"content": answer_text()}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    seat = Seat("s1", "Seat 1", "role", "primary", "shared-model",
                fallback=[Fallback("backup")])  # model omitted on purpose
    panel = Panel(providers=_providers(), seats=[seat], quorum_required=1)

    result = call_seat(seat, panel, "prompt", _env(),
                       transport=httpx.MockTransport(handler))

    assert result["provider"] == "backup"
    assert seen == ["shared-model", "shared-model"]


def test_model_missing_walks_alias_alternatives(tmp_path):
    """If a provider retired the id, the next id from the alias is tried in place."""
    data = {
        "providers": {"primary": {"base_url": "https://p.example/v1",
                                  "api_key_env": ["PRIMARY_KEY"]}},
        "model_aliases": {"demo-model": {"primary": ["retired-id", "renamed-id"]}},
        "seats": [{"seat_id": "s1", "seat": "S", "role": "r", "provider": "primary",
                   "model": "demo-model"}],
        "quorum": {"required": 1},
    }
    path = tmp_path / "panel.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    seat = load_panel(path).seats[0]
    assert seat.model == "retired-id"

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        seen.append(model)
        if model == "retired-id":
            return httpx.Response(404, text='{"error": "model not found"}')
        return httpx.Response(200, json={
            "choices": [{"message": {"content": answer_text()}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    panel = Panel(providers=_providers(), seats=[seat], quorum_required=1)
    result = call_seat(seat, panel, "prompt", {"PRIMARY_KEY": "sk-x-12345678"},
                       transport=httpx.MockTransport(handler))

    assert seen == ["retired-id", "renamed-id"]
    assert result["model"] == "renamed-id"
    assert result["fallback_used"] is False       # stayed on the primary provider
    assert len(result["attempts"]) == 2
    assert result["attempts"][0]["model_index"] == 0
    assert result["attempts"][1]["model_index"] == 1

