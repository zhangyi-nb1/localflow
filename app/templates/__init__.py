"""Phase 22 (v0.22) — bilingual Jinja templates for skill reports.

The 5 ``app/skills/*/reporter.py`` files used to hardcode English
markdown skeletons ("# Final report", "## Execution summary",
"## Verifier verdict", ...). After Phase 22's locale plumbing made
LLM-generated prose respect ``task.locale``, the *structural* reports
were the last English-only surface visible to the user.

This module is the shared infrastructure each reporter now calls:

  >>> from app.templates import render_report
  >>> render_report("final_report", locale="zh-CN", ctx={...})

``locale`` is either ``zh-CN`` or ``en-US`` (the BCP-47 codes Phase 22
standardised on). The renderer picks the label dict for that locale,
hands it to Jinja as ``T``, then renders the template (which lives at
``app/templates/reports/<name>.md.j2``). Unknown locales fall back to
``zh-CN`` per the product default (see ``app.schemas.task.DEFAULT_LOCALE``).

§10.7 invariant: the template engine is in ``app/templates``, NOT in
``app/harness``. Reporters are skill-side, not kernel.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from app.templates._labels import labels_for

_TEMPLATES_ROOT = Path(__file__).parent


@lru_cache(maxsize=1)
def _get_env() -> Environment:
    """Build the Jinja env once per process. ``StrictUndefined`` so a
    typo in a template variable name is loud instead of silently
    producing empty strings in the rendered markdown."""
    env = Environment(
        loader=FileSystemLoader(_TEMPLATES_ROOT),
        autoescape=select_autoescape(disabled_extensions=("j2", "md")),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
    )
    return env


def render_report(name: str, *, locale: str | None, ctx: dict[str, Any]) -> str:
    """Render ``reports/<name>.md.j2`` with the locale-appropriate
    label dict bound as ``T`` and every key in ``ctx`` exposed at top
    level. ``locale`` is normalised: anything other than ``en-US``
    treated as ``zh-CN`` so legacy callers without the field still
    produce Chinese output (the v0.22 default).
    """
    tmpl = _get_env().get_template(f"reports/{name}.md.j2")
    T = labels_for(locale)
    return tmpl.render(T=T, locale=T["_locale_code"], **ctx)
