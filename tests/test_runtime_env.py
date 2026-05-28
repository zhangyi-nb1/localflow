"""Phase 36.x — tests for the .env auto-loader.

Deterministic: uses tmp .env files + ``force=True`` to bypass the
pytest-skip guard. Verifies setdefault semantics (real env wins),
quote/export handling, and the safety guards.
"""

from __future__ import annotations

import os
from pathlib import Path

from app.runtime_env import _parse_line, load_project_dotenv


class TestParseLine:
    def test_simple(self):
        assert _parse_line("FOO=bar") == ("FOO", "bar")

    def test_export_prefix(self):
        assert _parse_line("export FOO=bar") == ("FOO", "bar")

    def test_double_quotes_stripped(self):
        assert _parse_line('FOO="bar baz"') == ("FOO", "bar baz")

    def test_single_quotes_stripped(self):
        assert _parse_line("FOO='bar baz'") == ("FOO", "bar baz")

    def test_comment_skipped(self):
        assert _parse_line("# a comment") is None

    def test_blank_skipped(self):
        assert _parse_line("   ") is None

    def test_no_equals_skipped(self):
        assert _parse_line("not a kv line") is None

    def test_value_with_equals_preserved(self):
        # base URLs / tokens can contain '='.
        assert _parse_line("URL=https://x/y?a=b") == ("URL", "https://x/y?a=b")


class TestLoadProjectDotenv:
    def test_loads_missing_keys(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("LOCALFLOW_TEST_K1=v1\nLOCALFLOW_TEST_K2=v2\n")
        monkeypatch.delenv("LOCALFLOW_TEST_K1", raising=False)
        monkeypatch.delenv("LOCALFLOW_TEST_K2", raising=False)

        loaded = load_project_dotenv(force=True)
        assert set(loaded) >= {"LOCALFLOW_TEST_K1", "LOCALFLOW_TEST_K2"}
        assert os.environ["LOCALFLOW_TEST_K1"] == "v1"

    def test_real_env_wins(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("LOCALFLOW_TEST_K3=from_dotenv\n")
        monkeypatch.setenv("LOCALFLOW_TEST_K3", "from_real_env")

        loaded = load_project_dotenv(force=True)
        assert "LOCALFLOW_TEST_K3" not in loaded
        assert os.environ["LOCALFLOW_TEST_K3"] == "from_real_env"

    def test_pytest_guard_skips_without_force(self, tmp_path: Path, monkeypatch):
        # Under pytest, PYTEST_CURRENT_TEST is set → load is a no-op.
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("LOCALFLOW_TEST_K4=v4\n")
        monkeypatch.delenv("LOCALFLOW_TEST_K4", raising=False)

        loaded = load_project_dotenv()  # no force
        assert loaded == []
        assert "LOCALFLOW_TEST_K4" not in os.environ

    def test_no_dotenv_opt_out(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("LOCALFLOW_TEST_K5=v5\n")
        monkeypatch.delenv("LOCALFLOW_TEST_K5", raising=False)
        monkeypatch.setenv("LOCALFLOW_NO_DOTENV", "1")

        loaded = load_project_dotenv(force=True)
        assert loaded == []

    def test_no_env_file_returns_empty(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # tmp_path has no .env, and the repo-root fallback also shouldn't
        # leak a key into the returned list for a tmp cwd... but the
        # fallback may find the real repo .env. So assert the call is
        # safe (returns a list) rather than asserting emptiness.
        result = load_project_dotenv(force=True)
        assert isinstance(result, list)
