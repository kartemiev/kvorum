"""Config tests: env precedence, .env merging, redaction."""

from __future__ import annotations

from pathlib import Path

from kvorum.config import clean, load_env_file, resolve_key


def test_resolve_key_env_wins(monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "env-value")
    key, source = resolve_key(("TEST_API_KEY",), {"TEST_API_KEY": "file-value"})
    assert key == "env-value"
    assert source == "env:TEST_API_KEY"


def test_resolve_key_falls_back_to_file(monkeypatch):
    monkeypatch.delenv("TEST_API_KEY", raising=False)
    key, source = resolve_key(("TEST_API_KEY",), {"TEST_API_KEY": "file-value"})
    assert key == "file-value"
    assert source == "env-file:TEST_API_KEY"


def test_resolve_key_missing(monkeypatch):
    monkeypatch.delenv("TEST_API_KEY", raising=False)
    key, source = resolve_key(("TEST_API_KEY",), {})
    assert key == ""
    assert source == "MISSING"


def test_resolve_key_alias_order(monkeypatch):
    monkeypatch.delenv("A", raising=False)
    monkeypatch.delenv("B", raising=False)
    key, _ = resolve_key(("A", "B"), {"B": "second"})
    assert key == "second"


def test_load_env_file_blank_ignored(tmp_path):
    p = tmp_path / ".env"
    p.write_text("A=1\nB=\n# comment\nC='quoted'\n", encoding="utf-8")
    env = load_env_file((p,))
    assert env == {"A": "1", "C": "quoted"}


def test_load_env_file_precedence(tmp_path):
    first = tmp_path / "first.env"
    second = tmp_path / "second.env"
    first.write_text("K=from-first\n", encoding="utf-8")
    second.write_text("K=from-second\n", encoding="utf-8")
    env = load_env_file((first, second))
    assert env["K"] == "from-first"


def test_clean_redacts_secrets():
    assert "sk-secret" not in clean("key sk-abcdef12345678 leaked")
    assert "Bearer" not in clean("Authorization: Bearer abcdefghijklmnopqrstuvwx")
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature"
    assert "eyJ" not in clean(f"token {jwt} here")
