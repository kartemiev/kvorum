"""Pre-flight context check tests: limits, FIT/OVERFLOW, skip-overflow."""

from __future__ import annotations

import json

import httpx

from kvorum.config import Settings
from kvorum.panel import (DEFAULT_CONTEXT_LIMITS, GLOBAL_CONTEXT_LIMIT, Panel,
                          ProviderConfig, Seat, load_panel, seat_context_limit)
from kvorum.preflight import (check_seats, estimate_tokens, format_table,
                              run_preflight)
from kvorum.runner import run_panel

from conftest import answer_text


def test_estimate_tokens_is_len_over_four():
    assert estimate_tokens("") == 0
    assert estimate_tokens("1234") == 1
    assert estimate_tokens("12345678") == 2


def test_default_context_limit_via_alias():
    seat = Seat("s", "S", "role", "p", "glm-5.2")
    assert seat_context_limit(seat) == DEFAULT_CONTEXT_LIMITS["glm-5.2"]


def test_default_context_limit_via_provider_prefixed_id():
    seat = Seat("s", "S", "role", "openrouter", "z-ai/glm-5.2")
    assert seat_context_limit(seat) == DEFAULT_CONTEXT_LIMITS["glm-5.2"]


def test_explicit_override_beats_default():
    seat = Seat("s", "S", "role", "p", "glm-5.2", max_context_tokens=12_345)
    assert seat_context_limit(seat) == 12_345


def test_unknown_model_uses_global_limit():
    seat = Seat("s", "S", "role", "p", "some/unknown-model")
    assert seat_context_limit(seat) == GLOBAL_CONTEXT_LIMIT


def test_builtin_table_lists_every_shipped_model_at_one_million():
    """The built-in table covers all 6 shipped seats and every entry is 1 M.

    Guards the 128 k / 256 k → 1 M bump: a regression back to a smaller default
    would silently turn large packets into OVERFLOW.
    """
    assert set(DEFAULT_CONTEXT_LIMITS) == {
        "deepseek-v4-pro", "glm-5.2", "kimi-k3",
        "qwen3.8-flash", "longcat-2.0", "minimax-m3",
    }
    assert set(DEFAULT_CONTEXT_LIMITS.values()) == {1_000_000}
    assert GLOBAL_CONTEXT_LIMIT == 1_000_000


def test_short_aliases_without_override_resolve_to_one_million():
    """Formerly 128 k / 256 k models now resolve to 1 M through the table."""
    for model in ("deepseek-v4-pro", "qwen3.8-flash", "kimi-k3", "minimax-m3"):
        seat = Seat("s", "S", "role", "p", model)
        assert seat_context_limit(seat) == 1_000_000, model


def test_provider_prefixed_ids_resolve_to_one_million():
    """OpenRouter ids (``moonshotai/kimi-k3``) hit the same 1 M table entry."""
    for model in ("deepseek/deepseek-v4-pro", "qwen/qwen3.8-flash",
                  "moonshotai/kimi-k3", "minimax/minimax-m3"):
        seat = Seat("s", "S", "role", "openrouter", model)
        assert seat_context_limit(seat) == 1_000_000, model


def test_check_seats_fit_and_overflow():
    seats = [
        Seat("big", "Big", "r", "p", "m", max_context_tokens=1000),
        Seat("small", "Small", "r", "p", "m", max_context_tokens=100),
    ]
    rows = check_seats(seats, "x" * 800)  # 800 // 4 = 200 tokens
    by_id = {r.seat.seat_id: r for r in rows}
    assert by_id["big"].status == "FIT"
    assert by_id["small"].status == "OVERFLOW"
    assert by_id["big"].packet_tokens == 200


def test_format_table_has_required_columns():
    seat = Seat("s", "Seat", "r", "p", "glm-5.2", max_context_tokens=1000)
    rows = check_seats([seat], "x" * 100)
    table = format_table(rows, estimate_tokens("x" * 100), 100)
    assert "| Seat | Model | Limit (tokens) | Packet Size | Status |" in table
    assert "| FIT |" in table


def test_run_preflight_strict_blocks_on_overflow():
    seats = [Seat("a", "A", "r", "p", "m", max_context_tokens=10),
             Seat("b", "B", "r", "p", "m", max_context_tokens=10_000)]
    remaining, code = run_preflight(seats, "x" * 100, quorum_required=1)
    assert code == 1
    assert remaining == seats  # unchanged on strict block


def test_run_preflight_skip_overflow_drops_and_keeps_quorum():
    seats = [Seat("a", "A", "r", "p", "m", max_context_tokens=10),
             Seat("b", "B", "r", "p", "m", max_context_tokens=10_000),
             Seat("c", "C", "r", "p", "m", max_context_tokens=10_000)]
    remaining, code = run_preflight(seats, "x" * 100, quorum_required=2,
                                    skip_overflow=True)
    assert code is None
    assert [s.seat_id for s in remaining] == ["b", "c"]


def test_run_preflight_skip_overflow_insufficient_seats():
    seats = [Seat("a", "A", "r", "p", "m", max_context_tokens=10),
             Seat("b", "B", "r", "p", "m", max_context_tokens=10_000)]
    remaining, code = run_preflight(seats, "x" * 100, quorum_required=2,
                                    skip_overflow=True)
    assert code == 1  # INSUFFICIENT_SEATS
    assert [s.seat_id for s in remaining] == ["b"]


def test_panel_json_parses_max_context_tokens(tmp_path):
    data = {
        "providers": {"p": {"base_url": "https://p.example/v1", "api_key_env": ["K"]}},
        "seats": [{"seat_id": "s", "seat": "S", "role": "r", "provider": "p",
                   "model": "m", "max_context_tokens": 777}],
        "quorum": {"required": 1},
    }
    path = tmp_path / "panel.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    seat = load_panel(path).seats[0]
    assert seat.max_context_tokens == 777
    assert seat_context_limit(seat) == 777


def test_run_panel_preflight_blocks_before_any_call(tmp_path):
    panel = Panel(
        providers={"p": ProviderConfig("p", "https://p.example/v1", ("TEST_KEY",))},
        seats=[Seat("s0", "S0", "r", "p", "m0", max_context_tokens=1),
               Seat("s1", "S1", "r", "p", "m1", max_context_tokens=1)],
        quorum_required=1)
    packet = tmp_path / "packet.md"
    packet.write_text("# packet\n" + ("x" * 100), encoding="utf-8")
    settings = Settings(packet_path=packet, runs_dir=tmp_path / "runs")

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["model"])
        return httpx.Response(200, json={
            "choices": [{"message": {"content": answer_text()}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    rc = run_panel(panel, settings, {"TEST_KEY": "sk-x-12345678"},
                   transport=httpx.MockTransport(handler), archive=False,
                   preflight=True)
    assert rc == 1
    assert seen == []  # no API call was made


def test_run_panel_skip_overflow_runs_survivors(tmp_path):
    panel = Panel(
        providers={"p": ProviderConfig("p", "https://p.example/v1", ("TEST_KEY",))},
        seats=[Seat("s0", "S0", "r", "p", "m0", max_context_tokens=1),
               Seat("s1", "S1", "r", "p", "m1", max_context_tokens=100_000),
               Seat("s2", "S2", "r", "p", "m2", max_context_tokens=100_000)],
        quorum_required=2)
    packet = tmp_path / "packet.md"
    packet.write_text("# packet\n" + ("x" * 40), encoding="utf-8")  # ~12 tokens
    settings = Settings(packet_path=packet, runs_dir=tmp_path / "runs")

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["model"])
        return httpx.Response(200, json={
            "choices": [{"message": {"content": answer_text()}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    rc = run_panel(panel, settings, {"TEST_KEY": "sk-x-12345678"},
                   transport=httpx.MockTransport(handler), archive=False,
                   preflight=True, skip_overflow=True)
    assert rc == 0
    assert seen == ["m1", "m2"]
    meta = json.loads((settings.runs_dir / "run_meta.json").read_text(encoding="utf-8"))
    assert meta["run_panel"] == ["s1", "s2"]
    assert meta["answered"] == 2