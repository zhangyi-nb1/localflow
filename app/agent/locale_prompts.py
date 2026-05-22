"""v0.22 — single source of truth for the locale instruction injected
into every LLM prompt LocalFlow makes.

Per the productisation guide §5.3, the locale has to flow into:
planner prompts, the agent meta-skill, the semantic verifier, the repair
prompt, the report generator, and the goal interpreter's clarifying
question prompt. Hand-writing the same paragraph at every call site is a
recipe for drift; this module centralises it.

Internal schema names (ActionType, verifier codes, JSON keys) stay
English — those aren't "user-facing content". The instruction is
explicit about that so the model doesn't try to translate paths,
file names, or code identifiers.
"""

from __future__ import annotations

from app.schemas.task import DEFAULT_LOCALE, Locale

_INSTRUCTIONS: dict[Locale, str] = {
    "zh-CN": (
        "**语言要求** — 所有面向用户的输出(包括计划摘要、动作原因、"
        "report 正文、verifier 说明、repair 建议、澄清问题、错误提示)"
        "都必须使用中文。\n"
        "**保持原格式** — 文件路径、文件名、扩展名、代码标识符、JSON key、"
        "命令行参数、URL 不要翻译,保持原样。"
    ),
    "en-US": (
        "**Language requirement** — All user-facing output (plan "
        "summaries, action reasons, report bodies, verifier explanations, "
        "repair suggestions, clarifying questions, error hints) MUST be "
        "written in English.\n"
        "**Preserve original format** — Do not translate file paths, file "
        "names, extensions, code identifiers, JSON keys, command-line "
        "arguments, or URLs."
    ),
}


def locale_instruction(locale: Locale | str | None = None) -> str:
    """Return the language-discipline paragraph to splice into a prompt.

    Always returns a non-empty string; unknown locales fall back to the
    project default (zh-CN) rather than empty — that way a bug in the
    caller can't accidentally drop the rule entirely.
    """
    key: Locale = DEFAULT_LOCALE
    if locale in _INSTRUCTIONS:
        key = locale  # type: ignore[assignment]
    return _INSTRUCTIONS[key]


def locale_system_suffix(locale: Locale | str | None = None) -> str:
    """The same instruction wrapped as a standalone trailing system-prompt
    paragraph. Use when appending to an existing system message:

        sys = base_system + "\\n\\n" + locale_system_suffix(task.locale)
    """
    return locale_instruction(locale)
