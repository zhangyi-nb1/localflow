"""Phase 8.1 / v0.8.0 — i18n framework contract tests.

The UI's translation lookup is built on a flat dict in ``app/ui/_i18n.py``.
These tests guarantee:

  * Every key has both ``en`` and ``zh`` translations (we ship a
    bilingual UI; partial translations would surface as ``!!key!!``
    sentinels in the wild).
  * Keys follow the ``<scope>.<element>[.<purpose>]`` dotted
    convention so they sort and group sensibly.
  * Missing keys return the sentinel (never silently empty).
  * Placeholder interpolation works through ``str.format``.
  * Without a Streamlit session, ``t()`` falls back to ``DEFAULT_LANG``.

No Streamlit runtime is touched here — the tests live in pure Python.
"""

from __future__ import annotations

import pytest

from app.ui._i18n import (
    DEFAULT_LANG,
    KEY_PATTERN,
    all_keys,
    current_lang,
    get_dict,
    t,
)


def test_default_lang_is_english() -> None:
    """English is the default until the user flips the toggle. Pin it
    so refactors don't silently swap the default."""
    assert DEFAULT_LANG == "en"


def test_every_key_has_en_and_zh() -> None:
    """No key may ship without both translations. A missing zh string
    would degrade to the en fallback at runtime, but visually
    inconsistent UI is what we want to prevent — fail at test time."""
    missing: list[tuple[str, str]] = []
    for key, entry in get_dict().items():
        if "en" not in entry or not entry["en"].strip():
            missing.append((key, "en"))
        if "zh" not in entry or not entry["zh"].strip():
            missing.append((key, "zh"))
    assert not missing, f"keys missing translations: {missing}"


def test_keys_follow_naming_convention() -> None:
    """Keys must be lower-case dotted segments (``scope.element[.purpose]``).
    Catches typos like 'Plan.Goal' or 'plan-goal' before they ship."""
    bad = [k for k in all_keys() if not KEY_PATTERN.match(k)]
    assert not bad, f"keys violate <scope>.<element>[.<purpose>] convention: {bad}"


def test_missing_key_returns_sentinel() -> None:
    """An unknown key returns ``!!key!!`` so it's obvious on screen
    during development. Don't return an empty string — that would be
    invisible."""
    assert t("totally.bogus.key") == "!!totally.bogus.key!!"


def test_placeholder_interpolation() -> None:
    """Existing keys with ``{path}`` / ``{n}`` should accept kwargs.
    Pick a known key that takes a placeholder."""
    out = t("home.active_workspace", path="C:\\foo\\bar")
    assert "C:\\foo\\bar" in out


def test_placeholder_missing_arg_does_not_crash() -> None:
    """If a caller forgets a placeholder, return the unformatted
    template rather than raising — the placeholder stays visible and
    the UI keeps rendering."""
    # home.active_workspace template includes {path}. Call without it.
    out = t("home.active_workspace")
    assert "{path}" in out


def test_t_without_streamlit_uses_default_lang() -> None:
    """In test mode there's no Streamlit script context; ``t()`` must
    still return the default-language string (English)."""
    # ``app.title`` is identical across languages so use ``app.subtitle.home``
    # which is distinct.
    out = t("app.subtitle.home")
    en = get_dict()["app.subtitle.home"]["en"]
    assert out == en


def test_current_lang_default() -> None:
    """Without Streamlit, ``current_lang()`` returns the default."""
    assert current_lang() == DEFAULT_LANG


def test_dict_has_minimum_coverage() -> None:
    """Smoke check: the dict should hold enough strings to be useful.
    If someone accidentally wipes most of it, this catches it."""
    assert len(all_keys()) >= 100, f"only {len(all_keys())} keys — did the dict get trimmed?"


def test_critical_pages_have_at_least_one_key() -> None:
    """Every top-level page-scope (plan / execute / rollback / memory)
    should have at least 5 keys, otherwise a translation pass got
    skipped."""
    by_scope: dict[str, int] = {}
    for k in all_keys():
        scope = k.split(".", 1)[0]
        by_scope[scope] = by_scope.get(scope, 0) + 1
    for required in ("plan", "execute", "rollback", "memory", "sidebar"):
        n = by_scope.get(required, 0)
        assert n >= 5, f"scope `{required}` has only {n} keys"


@pytest.mark.parametrize(
    "key",
    [
        "app.title",
        "common.workspace_warning",
        "plan.button.create",
        "execute.stage1.button",
        "rollback.btn.clean",
        "memory.tab.forbidden",
    ],
)
def test_critical_keys_present(key: str) -> None:
    """Pin a handful of load-bearing keys so a rename without test
    update raises a flag here."""
    assert key in get_dict(), f"critical key missing: {key}"
