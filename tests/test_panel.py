"""Panel composition tests: seats/roles, exclusions, refs, legacy import."""

from __future__ import annotations

import json

import pytest

from kvorum.panel import (Seat, default_panel, effective_seats, load_panel,
                          resolve_seat_refs, seat_file_name)


def test_default_panel_shape():
    panel = default_panel()
    assert len(panel.seats) == 6
    assert panel.quorum_required == 3
    assert panel.fail_closed is True
    assert "code-expert" in panel.excluded_by_default
    for seat in panel.seats:
        assert seat.provider in panel.providers


def test_every_seat_is_fully_described():
    """Each shipped seat carries the full slot schema (id/role/model/provider/fallback)."""
    for seat in default_panel().seats:
        assert seat.seat_id and seat.seat and seat.role
        assert seat.description, f"{seat.seat_id} has no description"
        assert seat.model and seat.provider
        assert seat.fallback_chain, f"{seat.seat_id} has no fallback configured"
        assert isinstance(seat.enabled, bool)


def test_default_panel_excludes_disabled_seat():
    slugs = [s.seat_id for s in effective_seats(default_panel())]
    assert "code-expert" not in slugs
    assert len(slugs) == 5


def test_include_excluded_re_enables():
    slugs = [s.seat_id for s in effective_seats(default_panel(), include_excluded=True)]
    assert "code-expert" in slugs


def test_exclude_env_override():
    panel = default_panel()
    # an explicit (even empty) exclusion list replaces the shipped policy
    slugs = [s.seat_id for s in effective_seats(panel, exclude_env="")]
    assert "code-expert" in slugs
    slugs = [s.seat_id for s in effective_seats(panel, exclude_env="security")]
    assert "security" not in slugs


def test_per_seat_enabled_flag(tmp_path):
    data = {
        "providers": {"p": {"base_url": "https://p.example/v1", "api_key_env": ["K"]}},
        "seats": [
            {"seat_id": "on", "seat": "On", "role": "r", "provider": "p", "model": "m1"},
            {"seat_id": "off", "seat": "Off", "role": "r", "provider": "p", "model": "m2",
             "enabled": False},
        ],
        "quorum": {"required": 1},
    }
    path = tmp_path / "panel.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    panel = load_panel(path)
    assert panel.seats[1].enabled is False
    assert [s.seat_id for s in effective_seats(panel)] == ["on"]


def test_per_seat_excluded_by_default_alias(tmp_path):
    data = {
        "providers": {"p": {"base_url": "https://p.example/v1", "api_key_env": ["K"]}},
        "seats": [{"seat_id": "s", "seat": "S", "role": "r", "provider": "p",
                   "model": "m", "excluded_by_default": True}],
        "quorum": {"required": 1},
    }
    path = tmp_path / "panel.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert load_panel(path).seats[0].enabled is False


def test_resolve_seat_refs_reports_typos():
    panel = default_panel()
    matched, unmatched = resolve_seat_refs(panel, ["logic", "nope", "GLM-5.2"])
    assert [s.seat_id for s in matched] == ["logic", "architect"]
    assert unmatched == ["nope"]


def test_fallback_chain_is_supported(tmp_path):
    data = {
        "providers": {
            "a": {"base_url": "https://a.example/v1", "api_key_env": ["KA"]},
            "b": {"base_url": "https://b.example/v1", "api_key_env": ["KB"]},
            "c": {"base_url": "https://c.example/v1", "api_key_env": ["KC"]},
        },
        "seats": [{"seat_id": "s", "seat": "S", "role": "r", "provider": "a", "model": "m",
                   "fallback": [{"provider": "b", "model": "m"},
                                {"provider": "c", "model": "m"}]}],
        "quorum": {"required": 1},
    }
    path = tmp_path / "panel.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    seat = load_panel(path).seats[0]
    assert [f.provider for f in seat.fallback_chain] == ["b", "c"]


def test_seat_file_name_is_fs_safe():
    seat = Seat("z-ai/glm-5.2", "GLM", "role", "p", "m")
    assert seat_file_name(seat) == "z-ai_glm-5.2.json"


def test_role_line_includes_description():
    seat = Seat("s", "S", "Architect", "p", "m", description="layering")
    assert seat.role_line == "Architect — layering"


def test_load_legacy_council(tmp_path):
    council = {
        "quorum": {"core_required": 3, "fail_closed": True},
        "model_timeout_s": 240,
        "excluded_by_default": ["minimax/minimax-m3"],
        "models": [
            {"slug": "deepseek/deepseek-v4-pro", "role": "Logic / Bug-Hunter",
             "tier": "core", "max_output_tokens": 16384},
            {"slug": "z-ai/glm-5.2", "role": "System Architect",
             "tier": "core", "max_output_tokens": 16384},
        ],
    }
    path = tmp_path / "council.json"
    path.write_text(json.dumps(council), encoding="utf-8")
    panel = load_panel(path)
    assert panel.quorum_required == 3
    assert panel.timeout_s == 240.0
    assert len(panel.seats) == 2
    assert panel.seats[0].seat_id == "deepseek/deepseek-v4-pro"
    assert panel.seats[0].provider == "deepseek"
    assert panel.seats[0].model == "deepseek-v4-pro"
    # z-ai/glm-5.2 maps to siliconflow with the real id
    assert panel.seats[1].provider == "siliconflow"
    assert panel.seats[1].model == "zai-org/GLM-5.2"
    assert panel.seats[0].fallback_chain[0].provider == "openrouter"


def test_load_panel_json_with_custom_provider(tmp_path):
    data = {
        "providers": {"local": {"base_url": "http://127.0.0.1:11434/v1",
                                "api_key_env": ["OLLAMA_API_KEY"]}},
        "seats": [{"seat_id": "local/qwen", "seat": "Qwen", "role": "arch",
                   "provider": "local", "model": "qwen3:14b", "max_tokens": 1000}],
        "quorum": {"required": 1, "fail_closed": True},
    }
    path = tmp_path / "panel.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    panel = load_panel(path)
    assert panel.seats[0].provider == "local"
    assert panel.provider("local").base_url == "http://127.0.0.1:11434/v1"
    assert panel.provider("local").chat_url() == "http://127.0.0.1:11434/v1/chat/completions"


def test_load_missing_panel_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_panel(tmp_path / "nope.json")
