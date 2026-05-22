"""Phase 17 — Pack page (v0.17.0).

Product-level entry point for the Recipe / Pack System.

The page name has prefix `0_` so Streamlit places it FIRST in the
sidebar — ahead of Plan / Execute / Rollback / Memory. Phase 17's
core productisation move (per §5.1 of the productisation guide):
users land on "pick a deliverable pack" instead of "pick a skill".

Three sub-flows on this one page:
  1. **Browse** — every loaded recipe listed as a card with its
     description, tags, and stage count.
  2. **Suggest** — scan the active workspace + (optional) goal
     hint, rank recipes by fit, surface the top match.
  3. **Run** — compile the chosen recipe to a TaskGraph, show the
     plan for approval, kick off the runner inline, render the
     per-stage result table.

§10.7 invariant maintained: the page reuses
``app.harness.taskgraph_runner.run_taskgraph`` — the kernel never
learns Recipe exists.

v0.19.0 (Phase 19) — full bilingual i18n via :func:`app.ui._i18n.t`
so the page honours the sidebar's Language switch like every other
page. Strings that depend on user / runtime data (recipe titles,
stage_ids, exception messages) stay literal — they aren't translated
content.
"""

from __future__ import annotations

import streamlit as st

from app.harness.taskgraph_runner import run_taskgraph
from app.harness.trace import TraceLogger
from app.recipes import get_default_registry
from app.schemas import RecipeSpec
from app.storage.run_store import RunStore
from app.tools.file_scan import scan_workspace
from app.ui._i18n import current_locale, t
from app.ui._layout import (
    configure_page,
    render_sandbox_sidebar,
    render_unsafe_banner,
    require_workspace,
)

PACK_RUN_KEY = "_pack_run_request"
PACK_RESULT_KEY = "_pack_last_result"
# Home → Pack handoff: when the landing page's "Try X" button is
# clicked, it stashes the recipe name here so we auto-expand the
# matching card. Consumed once per click.
PACK_SELECT_KEY = "_home_pack_select"


def main() -> None:
    configure_page("app.page_title.pack", icon="📦")
    st.markdown(f"## {t('pack.heading')}")
    st.caption(t("pack.subtitle"))
    render_unsafe_banner()
    render_sandbox_sidebar()
    workspace = require_workspace()

    registry = get_default_registry()
    recipes = registry.all()
    if registry.load_errors:
        with st.expander(
            t("pack.load_errors_title", n=len(registry.load_errors)),
            expanded=False,
        ):
            for path, err in registry.load_errors:
                st.error(f"**{path.name}**: {err}")

    if not recipes:
        st.warning(t("pack.no_recipes_loaded", path=registry.recipes_dir))
        return

    _render_suggest_block(recipes, workspace)
    st.divider()
    _render_recipe_cards(recipes)

    # If a previous render queued a pack run, execute it now (after
    # the cards rendered so the user sees what's running).
    pending = st.session_state.pop(PACK_RUN_KEY, None)
    if pending is not None:
        _execute_pack(pending, workspace)

    result = st.session_state.get(PACK_RESULT_KEY)
    if result is not None:
        st.divider()
        _render_result(result)


def _render_suggest_block(recipes: list[RecipeSpec], workspace) -> None:
    """Phase 18 — Goal Interpreter block.

    Replaces the v0.17 router-only "Suggest" block. Asks the user for
    a goal; calls :class:`GoalInterpreter` which routes deterministically
    when confident, or invokes the LLM for clarifying questions when
    ambiguous. Clarification answers persist across reruns via session
    state so the user doesn't lose their place.
    """
    import pandas as pd

    from app.agent.goal_interpreter import GoalInterpreter

    with st.expander(t("pack.goal.expander_title"), expanded=False):
        st.markdown(t("pack.goal.description"))
        goal = st.text_input(
            t("pack.goal.input_label"),
            placeholder=t("pack.goal.input_placeholder"),
            key="pack_goal_input",
        )
        use_llm = st.checkbox(
            t("pack.goal.use_llm_label"),
            value=True,
            key="pack_goal_use_llm",
            help=t("pack.goal.use_llm_help"),
        )
        if st.button(t("pack.goal.button_interpret"), key="pack_goal_btn"):
            st.session_state["_pack_goal_run"] = {
                "goal": goal,
                "use_llm": use_llm,
                "answers": [],
            }
            st.session_state.pop("_pack_goal_result", None)

        # Replay any pending interpretation across reruns.
        pending = st.session_state.get("_pack_goal_run")
        if pending is not None and pending.get("goal"):
            with st.spinner(t("pack.goal.scanning_workspace")):
                snap = scan_workspace(workspace, task_id="goal", compute_hash=False)
            client = None
            if pending["use_llm"]:
                try:
                    from app.agent.planner import _default_client

                    client = _default_client()
                except Exception:
                    client = None
                    st.warning(t("pack.goal.no_llm_fallback"))
            interpreter = GoalInterpreter(client=client)
            interpretation = interpreter.interpret(
                user_goal=pending["goal"],
                snapshot=snap,
                prior_answers=pending.get("answers") or [],
            )
            st.session_state["_pack_goal_result"] = interpretation

        result = st.session_state.get("_pack_goal_result")
        if result is None:
            return

        # Render decision. Prefer the i18n key + args when populated
        # (router-only branches set it). LLM-driven rationales are
        # already in the user's language (prompt enforces it) so we
        # render them verbatim.
        rationale_text = result.rationale
        if result.rationale_key:
            rationale_text = t(result.rationale_key, **result.rationale_args)

        if result.decision == "pick":
            st.success(
                t(
                    "pack.goal.suggested",
                    name=result.recipe_name,
                    source=result.source,
                    rationale=rationale_text,
                )
            )
            # Look up the recipe title (translated label still ASCII-safe).
            try:
                recipe_title = get_default_registry().get(result.recipe_name).title
            except Exception:
                recipe_title = result.recipe_name
            if st.button(
                t("pack.goal.run_button", title=recipe_title),
                key="pack_goal_run_btn",
                type="primary",
            ):
                st.session_state[PACK_RUN_KEY] = {
                    "recipe_name": result.recipe_name,
                    "enable_repair": False,
                }
                st.rerun()
        else:
            st.warning(
                t(
                    "pack.goal.need_clarification",
                    source=result.source,
                    rationale=rationale_text,
                )
            )
            for i, q in enumerate(result.clarifying_questions, 1):
                st.markdown(f"  {i}. {q}")
            answer = st.text_input(
                t("pack.goal.answer_label"),
                key="pack_goal_clarify_answer",
                placeholder=t("pack.goal.answer_placeholder"),
            )
            if st.button(t("pack.goal.submit_clarify"), key="pack_goal_clarify_btn"):
                pending = st.session_state.get("_pack_goal_run") or {}
                pending.setdefault("answers", []).append(answer)
                st.session_state["_pack_goal_run"] = pending
                st.session_state.pop("_pack_goal_result", None)
                st.rerun()

        # Audit: full router ranking.
        if result.router_scores:
            no_signals = t("pack.goal.audit_no_signals")
            df = pd.DataFrame(
                [
                    {
                        t("pack.goal.audit_col_rank"): i,
                        t("pack.goal.audit_col_recipe"): s["recipe"],
                        t("pack.goal.audit_col_score"): s["score"],
                        t("pack.goal.audit_col_why"): ("; ".join(s["why"]) or no_signals),
                    }
                    for i, s in enumerate(result.router_scores, 1)
                ]
            )
            with st.expander(t("pack.goal.router_audit_title"), expanded=False):
                st.dataframe(df, hide_index=True, use_container_width=True)


def _render_recipe_cards(recipes: list[RecipeSpec]) -> None:
    """Bottom section: one expander per recipe, each with a Run button."""
    st.markdown(t("pack.cards.heading"))
    tags_none = t("pack.cards.tags_none")
    preselected = st.session_state.pop(PACK_SELECT_KEY, None)
    for r in recipes:
        with st.expander(f"**{r.title}** — `{r.name}`", expanded=(r.name == preselected)):
            st.markdown(r.description.strip())
            cols = st.columns([2, 1])
            with cols[0]:
                st.markdown(
                    t(
                        "pack.cards.stats",
                        stages=len(r.stages),
                        outputs=len(r.expected_outputs),
                        tags=", ".join(r.tags) or tags_none,
                    )
                )
                st.markdown(t("pack.cards.stages_label"))
                for i, s in enumerate(r.stages, 1):
                    badge = {
                        "abort": "🛑",
                        "continue": "➡️",
                        "skip": "⏭️",
                        "repair": "🔧",
                    }.get(s.failure_policy.value, "·")
                    st.markdown(
                        t(
                            "pack.cards.stage_line",
                            idx=i,
                            badge=badge,
                            stage_id=s.stage_id,
                            title=s.title,
                            skill=s.skill,
                            planner=s.planner,
                        )
                    )
                with st.popover(t("pack.cards.expected_outputs_popover")):
                    for p in r.expected_outputs:
                        st.markdown(f"- `{p}`")
                with st.popover(t("pack.cards.verifiers_popover", n=len(r.verifiers))):
                    if not r.verifiers:
                        st.markdown(t("pack.cards.verifiers_none"))
                    else:
                        for v in r.verifiers:
                            st.markdown(f"- `{v}`")
                        if r.repair_policy.enabled:
                            st.markdown(t("pack.cards.repair_map_label"))
                            for v in r.verifiers:
                                target = r.resolve_repair_target(v)
                                if target and v in r.repair_target_map:
                                    st.markdown(
                                        t(
                                            "pack.cards.repair_map_line",
                                            verifier=v,
                                            stage_id=target,
                                        )
                                    )
                            st.markdown(t("pack.cards.repair_map_default"))
            with cols[1]:
                enable_repair = st.checkbox(
                    t("pack.cards.enable_repair_label"),
                    key=f"pack_repair_{r.name}",
                    value=r.repair_policy.enabled,
                    help=t("pack.cards.enable_repair_help"),
                )
                if st.button(
                    t("pack.cards.run_button", title=r.title),
                    key=f"pack_run_{r.name}",
                    type="primary",
                ):
                    st.session_state[PACK_RUN_KEY] = {
                        "recipe_name": r.name,
                        "enable_repair": enable_repair,
                    }
                    st.rerun()


def _execute_pack(request: dict, workspace) -> None:
    """Compile + run a pack inline, then stash the result in session state.

    Phase 21.1: also run recipe-level verifiers + (conditionally) the
    auto-repair loop so the UI matches what `localflow pack run` shows.
    Previously the UI stopped at the stage table and the user had to
    re-run from the CLI to see verifier verdicts."""
    registry = get_default_registry()
    recipe = registry.get(request["recipe_name"])

    if request.get("enable_repair"):
        recipe = recipe.model_copy(
            update={"repair_policy": recipe.repair_policy.model_copy(update={"enabled": True})}
        )

    locale = current_locale()
    tg = recipe.compile_to_taskgraph(workspace_root=str(workspace), locale=locale)
    store = RunStore.create()
    trace = TraceLogger(store.trace_path)

    progress = st.empty()
    progress.info(t("pack.exec.running", name=recipe.name, stages=len(tg.stages)))

    try:
        result = run_taskgraph(tg, store, trace=trace, approved=True)
    except Exception as exc:  # noqa: BLE001 — surface every error to the user
        progress.empty()
        st.error(t("pack.exec.failed", err_type=type(exc).__name__, err=str(exc)))
        return

    # Phase 21.1 — run recipe-level verifiers (Phase 19) + repair loop
    # (Phase 21). Reuses the same helpers the CLI uses so the two paths
    # stay in lockstep.
    verification = None
    repair_result = None
    try:
        from app.cli import _run_recipe_repair, _run_recipe_verifiers

        verification = _run_recipe_verifiers(
            recipe=recipe,
            store=store,
            workspace=workspace,
            result=result,
            locale=locale,
        )
        if (
            verification is not None
            and not verification.passed
            and result.passed
            and recipe.repair_policy.enabled
        ):
            repair_result = _run_recipe_repair(
                recipe=recipe, graph=tg, store=store, verification=verification
            )
            if repair_result is not None and repair_result.final_verification is not None:
                verification = repair_result.final_verification
    except Exception as exc:  # noqa: BLE001 — never crash the UI on verifier/repair issues
        st.warning(
            t(
                "pack.exec.verifier_exception",
                err_type=type(exc).__name__,
                err=str(exc),
            )
        )

    progress.empty()
    st.session_state[PACK_RESULT_KEY] = {
        "recipe_name": recipe.name,
        "recipe_title": recipe.title,
        "task_id": result.task_id,
        "passed": result.passed,
        "duration_ms": result.duration_ms,
        "stages": [
            {
                "stage_id": s.stage_id,
                "status": s.status.value,
                "action_count": s.action_count,
                "verifier_passed": s.verifier_passed,
                "duration_ms": s.duration_ms,
            }
            for s in result.stages
        ],
        "verification": (
            None
            if verification is None
            else {
                "passed": verification.passed,
                "verdicts": [
                    {
                        "name": v.name,
                        "passed": v.passed,
                        "skipped": v.skipped,
                        "detail": v.detail,
                        "suggested_hint": v.suggested_hint,
                    }
                    for v in verification.verdicts
                ],
            }
        ),
        "repair": (
            None
            if repair_result is None or repair_result.rounds_used == 0
            else {
                "repaired": repair_result.repaired,
                "rounds_used": repair_result.rounds_used,
                "halt_reason": repair_result.halt_reason,
                "attempts": [
                    {
                        "attempt": a.attempt,
                        "verifier": a.triggered_by_verifier,
                        "target": a.target_stage,
                        "hint": a.suggested_hint,
                        "passed": a.post_attempt_passed,
                        "still_failing": list(a.failed_after_attempt),
                        "error": a.error,
                        "duration_ms": a.duration_ms,
                    }
                    for a in repair_result.attempts
                ],
            }
        ),
    }


def _render_result(result: dict) -> None:
    """Render the most recent pack run result."""
    badge = "✅" if result["passed"] else "❌"
    st.markdown(
        t(
            "pack.result.heading",
            badge=badge,
            name=result["recipe_name"],
            ms=result["duration_ms"],
        )
    )
    st.caption(t("pack.result.run_id", run_id=result["task_id"]))

    import pandas as pd

    df = pd.DataFrame(
        [
            {
                t("pack.result.col_stage"): s["stage_id"],
                t("pack.result.col_status"): s["status"],
                t("pack.result.col_actions"): s["action_count"],
                t("pack.result.col_verifier"): (
                    "—"
                    if s["verifier_passed"] is None
                    else ("✅" if s["verifier_passed"] else "❌")
                ),
                t("pack.result.col_ms"): s["duration_ms"],
            }
            for s in result["stages"]
        ]
    )
    st.dataframe(df, hide_index=True, use_container_width=True)

    # Phase 21.1 — recipe-level verifier verdicts. Mirrors what
    # ``localflow pack run`` prints after stage execution.
    verification = result.get("verification")
    if verification is not None:
        st.markdown(t("pack.result.verifier_heading"))
        if verification["passed"]:
            st.success(t("pack.result.verifier_passed"))
        else:
            st.error(t("pack.result.verifier_failed"))

        def _vstatus(v: dict) -> str:
            if v["skipped"]:
                return t("pack.result.verifier_status_skipped")
            return (
                t("pack.result.verifier_status_passed")
                if v["passed"]
                else t("pack.result.verifier_status_failed")
            )

        vdf = pd.DataFrame(
            [
                {
                    t("pack.result.col_verifier_name"): v["name"],
                    t("pack.result.col_verifier_status"): _vstatus(v),
                    t("pack.result.col_verifier_detail"): v["detail"] or "",
                    t("pack.result.col_verifier_hint"): v["suggested_hint"] or "",
                }
                for v in verification["verdicts"]
            ]
        )
        st.dataframe(vdf, hide_index=True, use_container_width=True)

    # Phase 21.1 — auto-repair attempts table.
    repair = result.get("repair")
    if repair is not None:
        st.markdown(t("pack.result.repair_heading"))
        verb_key = (
            "pack.result.repair_verb_repaired"
            if repair["repaired"]
            else "pack.result.repair_verb_still_failing"
        )
        st.markdown(
            t(
                "pack.result.repair_summary",
                rounds=repair["rounds_used"],
                halt=repair["halt_reason"],
                verb=t(verb_key),
            )
        )

        def _outcome(a: dict) -> str:
            if a.get("error"):
                return t("pack.result.repair_outcome_error", err=a["error"])
            return (
                t("pack.result.repair_outcome_passed")
                if a["passed"]
                else t("pack.result.repair_outcome_failed")
            )

        rdf = pd.DataFrame(
            [
                {
                    t("pack.result.col_repair_attempt"): a["attempt"],
                    t("pack.result.col_repair_verifier"): a["verifier"],
                    t("pack.result.col_repair_target"): a["target"],
                    t("pack.result.col_repair_hint"): a["hint"] or "",
                    t("pack.result.col_repair_passed"): _outcome(a),
                    t("pack.result.col_repair_ms"): a["duration_ms"],
                }
                for a in repair["attempts"]
            ]
        )
        st.dataframe(rdf, hide_index=True, use_container_width=True)

    st.info(t("pack.result.rollback_hint", run_id=result["task_id"]))


main()
