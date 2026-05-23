"""Phase 18 — GoalInterpreter (v0.18.0).

Productisation guide §6.2 / §7 entry point: "User Goal → Goal
Interpreter → TaskGraph Planner". Sits above Phase 17's
:class:`RecipeRouter` and adds an LLM-driven clarifying path for
ambiguous goals.

Decision tree (deterministic):

  1. Run :meth:`RecipeRouter.score_all` against the workspace.
  2. If the top recipe scores ≥ CONFIDENT_SCORE_THRESHOLD and beats
     the runner-up by ≥ CONFIDENT_MARGIN, return it immediately —
     no LLM call needed.
  3. Otherwise ask the LLM to either (a) pick one of the recipes
     given the goal + workspace summary, or (b) emit 1–3 short
     clarifying questions if the goal is too vague to commit.

The LLM is called via the same :class:`LLMClient` Protocol the rest
of LocalFlow already uses. If no client is configured, we degrade to
"return the router's best pick even at low confidence" rather than
crashing — same graceful-degradation pattern as the agent meta-skill.

§10.7 invariant maintained: zero kernel changes. This module is pure
application logic — uses recipes, snapshots, and the LLM client; never
touches the executor / verifier / rollback.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from app.agent.client import LLMClient, LLMClientError
from app.agent.locale_prompts import locale_instruction
from app.recipes import RecipeRegistry, RecipeRouter, get_default_registry
from app.recipes.router import RecipeScore
from app.schemas import RecipeSpec, WorkspaceSnapshot
from app.schemas.task import DEFAULT_LOCALE, Locale

CONFIDENT_SCORE_THRESHOLD = 6
"""Minimum score the router's top recipe must reach to skip the LLM
call. Tuned against the three flagship recipes' default keyword +
file-kind tables — a score of 6 means at least one strong keyword
hit plus a few file-kind matches."""

CONFIDENT_MARGIN = 2
"""Minimum gap between top and runner-up score. A tie at high scores
still triggers the LLM (it disambiguates "research" vs "data report"
when both keyword sets fire)."""

NO_LLM_MIN_KEYWORD_HITS = 1
"""Phase 21.1 — when no LLM client is configured, a router-only pick
requires at least one keyword hit from the user's goal. A vague goal
like '随便弄一下' or 'do something' produces zero keyword hits but may
still score positively on file-kind alone — previously this returned
a low-confidence pick; now it clarifies instead. Set to 0 to restore
the pre-fix behaviour."""


def _had_keyword_hit(top: RecipeScore) -> bool:
    """True iff the router's scoring recorded at least one 'goal
    mentions: ...' reason for this recipe (i.e. the user's text matched
    at least one of the recipe's keyword triggers)."""
    return any(reason.startswith("goal mentions") for reason in top.why)


INTERPRETER_TOOL_NAME = "decide_recipe"
INTERPRETER_TOOL_DESCRIPTION = (
    "Decide which deliverable pack (recipe) fits the user's goal + "
    "workspace, or ask the user to clarify if the goal is too vague."
)

SYSTEM_PROMPT = (
    "You are LocalFlow's Goal Interpreter. Your job is to map a vague user "
    "goal + a workspace summary onto one of the loaded recipes (deliverable "
    "packs). You do NOT write code, you do NOT plan stages — that happens "
    "downstream once a recipe is chosen.\n\n"
    "Decision rules:\n"
    "  1. Read the recipe catalog the user passes you. Each recipe has a "
    "name, a title, a description, and a list of expected_outputs.\n"
    "  2. Read the workspace summary. Note which file kinds dominate.\n"
    "  3. If the user's goal — even when vague — clearly maps onto exactly "
    "one recipe given the workspace, return decision='pick' with the "
    "recipe_name + a one-sentence rationale.\n"
    "  4. If two recipes are plausible AND the user's goal doesn't say "
    "anything that disambiguates, return decision='clarify' with 1 to 3 "
    "short clarifying questions (each <= 20 words) that, once answered, "
    "would let you pick. Phrase questions in the SAME LANGUAGE the user "
    "used in their goal.\n"
    "  5. NEVER invent a recipe name. NEVER suggest 'create a new recipe'. "
    "Only refer to the names you were given.\n"
    "  6. When in doubt, prefer clarify over a low-confidence pick.\n"
)


class GoalInterpretation(BaseModel):
    """The Goal Interpreter's verdict. One of two shapes:

    * ``decision == "pick"`` — confident enough to commit a recipe.
      ``recipe_name`` is set; ``clarifying_questions`` is empty.
    * ``decision == "clarify"`` — needs more info. ``recipe_name``
      is None; ``clarifying_questions`` has 1–3 items.

    Callers (CLI / UI) read ``decision`` and dispatch.
    """

    decision: Literal["pick", "clarify"]
    recipe_name: str | None = None
    rationale: str = Field(
        ...,
        description=(
            "One sentence explaining the verdict. English by default for "
            "CLI / log consumption. The UI prefers ``rationale_key`` + "
            "``rationale_args`` (i18n) when populated and falls back to "
            "this string otherwise."
        ),
    )
    rationale_key: str | None = Field(
        default=None,
        description=(
            "v0.19.x — optional i18n key into ``app.ui._i18n._DICT`` "
            "(e.g. ``goal_interp.rationale.router_confident``). Router-"
            "only branches set this so the UI can render the rationale "
            "in the active language. LLM-driven branches leave it None "
            "(LLM is already prompted to use the user's language)."
        ),
    )
    rationale_args: dict[str, Any] = Field(
        default_factory=dict,
        description="Format arguments for ``rationale_key``.",
    )
    clarifying_questions: list[str] = Field(default_factory=list)
    source: Literal["router", "llm"] = Field(
        ...,
        description=(
            "Which path produced this interpretation. 'router' = high-"
            "confidence determ. match (no LLM call). 'llm' = LLM was "
            "consulted."
        ),
    )
    router_scores: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Audit trail of the deterministic router's full ranking. "
            "Surfaced in trace events + the CLI so users can see what "
            "the model was choosing between."
        ),
    )


def _build_decision_tool_schema(recipe_names: list[str]) -> dict[str, Any]:
    """Strict JSON schema for the forced tool call.

    Constrains ``recipe_name`` to an enum of currently-loaded recipe
    names so the LLM can't hallucinate a non-existent pack.

    OpenAI strict mode contract (enforced by chat.completions when the
    server is in strict-tool-schema mode):
      * ``required`` MUST list every key in ``properties`` — there is
        no "optional" field; every key must be present in the LLM's
        output. Conceptually-optional fields use ``anyOf`` with a
        ``null`` branch.
      * ``additionalProperties`` MUST be false.

    Mirrors the schema-shaping pattern from :mod:`app.agent.prompts`.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        # OpenAI strict: required must include every key in properties.
        "required": [
            "decision",
            "recipe_name",
            "rationale",
            "clarifying_questions",
        ],
        "properties": {
            "decision": {"type": "string", "enum": ["pick", "clarify"]},
            "recipe_name": {
                # Nullable enum: when decision='clarify' the LLM emits
                # null. Strict mode rejects ``type: ["string", "null"]``
                # plus an enum that includes null — use anyOf instead.
                "anyOf": [
                    {"type": "string", "enum": list(recipe_names)},
                    {"type": "null"},
                ],
                "description": (
                    "Required when decision='pick'; must equal one of the "
                    "loaded recipe names. Null when decision='clarify'."
                ),
            },
            "rationale": {
                "type": "string",
                "minLength": 1,
                "maxLength": 400,
                "description": "One sentence explaining the verdict.",
            },
            "clarifying_questions": {
                "type": "array",
                "minItems": 0,
                "maxItems": 3,
                "items": {"type": "string", "minLength": 1, "maxLength": 200},
                "description": (
                    "Required (1-3 items) when decision='clarify'. Empty "
                    "array when decision='pick'."
                ),
            },
        },
    }


def _summarise_recipes(recipes: list[RecipeSpec]) -> str:
    lines = ["Recipes available:"]
    for r in recipes:
        outputs = ", ".join(r.expected_outputs[:5])
        if len(r.expected_outputs) > 5:
            outputs += f", … (+{len(r.expected_outputs) - 5} more)"
        lines.append(
            f"- {r.name}: {r.title}\n"
            f"    description: {r.description.strip().splitlines()[0]}\n"
            f"    expected_outputs: {outputs}"
        )
    return "\n".join(lines)


def _summarise_workspace(snapshot: WorkspaceSnapshot | None) -> str:
    if snapshot is None or not snapshot.files:
        return "Workspace summary: (no workspace provided)"
    from collections import Counter

    counts: Counter[str] = Counter(f.file_type for f in snapshot.files)
    parts = [f"{k}={v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]
    return f"Workspace summary: {len(snapshot.files)} file(s) total — " + ", ".join(parts)


class GoalInterpreter:
    """Maps (user_goal, workspace) → (recipe pick OR clarifying questions).

    Parameters
    ----------
    registry
        Recipe registry to choose from. Defaults to the repo's
        ``recipes/`` dir.
    client
        Optional :class:`LLMClient`. When ``None``, the interpreter
        degrades to "return router pick at whatever confidence" with
        ``source="router"``.
    """

    def __init__(
        self,
        *,
        registry: RecipeRegistry | None = None,
        client: LLMClient | None = None,
        locale: Locale | str | None = None,
    ) -> None:
        self.registry = registry or get_default_registry()
        self.router = RecipeRouter(self.registry)
        self.client = client
        # v0.22 — when the interpreter consults the LLM, the rationale +
        # clarifying questions need to come back in the user's language.
        # Caller passes `task.locale` (or the UI's current language); we
        # default to zh-CN to match TaskSpec's own default.
        self.locale: Locale | str = locale if locale is not None else DEFAULT_LOCALE

    def interpret(
        self,
        *,
        user_goal: str,
        snapshot: WorkspaceSnapshot | None = None,
        prior_answers: list[str] | None = None,
    ) -> GoalInterpretation:
        """Return one verdict.

        ``prior_answers`` lets the caller feed back user answers to a
        previous clarifying round — the answers are simply appended
        to the goal text. Two rounds of clarification is a sensible
        upper bound; callers can keep calling but the LLM is unlikely
        to converge after that.
        """
        ranked = self.router.score_all(user_goal=user_goal, snapshot=snapshot)
        scores_audit = [
            {"recipe": s.recipe.name, "score": s.score, "why": list(s.why)} for s in ranked
        ]

        if not ranked:
            return GoalInterpretation(
                decision="clarify",
                rationale="No recipes are loaded; ask the user to install or configure recipes.",
                rationale_key="goal_interp.rationale.no_recipes_loaded",
                rationale_args={},
                clarifying_questions=[
                    "No deliverable packs are installed — set LOCALFLOW_RECIPES_DIR or "
                    "drop a YAML into the repo's recipes/ folder before continuing."
                ],
                source="router",
                router_scores=scores_audit,
            )

        # Fast path — router is confident enough.
        top = ranked[0]
        runner_up_score = ranked[1].score if len(ranked) > 1 else -999
        if (
            top.score >= CONFIDENT_SCORE_THRESHOLD
            and (top.score - runner_up_score) >= CONFIDENT_MARGIN
        ):
            return GoalInterpretation(
                decision="pick",
                recipe_name=top.recipe.name,
                rationale=(
                    f"Router scored {top.recipe.name} at {top.score:+d} "
                    f"(margin {top.score - runner_up_score:+d} over next "
                    f"candidate); deterministic pick."
                ),
                rationale_key="goal_interp.rationale.router_confident",
                rationale_args={
                    "name": top.recipe.name,
                    "score": f"{top.score:+d}",
                    "margin": f"{top.score - runner_up_score:+d}",
                },
                source="router",
                router_scores=scores_audit,
            )

        # LLM path — but only if a client is configured.
        if self.client is None:
            # Graceful degradation. Phase 21.1: a positive score alone
            # is NOT enough — require at least one keyword hit from the
            # user's goal text. Without that, the score is driven purely
            # by workspace file kinds, which is a hallucinated commitment
            # ("you have PDFs so I picked Research Pack" with no signal
            # that the user actually WANTS that deliverable). Clarify
            # instead.
            if top.score > 0 and _had_keyword_hit(top):
                return GoalInterpretation(
                    decision="pick",
                    recipe_name=top.recipe.name,
                    rationale=(
                        f"No LLM available; falling back to router top pick "
                        f"({top.recipe.name}, score {top.score:+d}). Confidence "
                        f"is low — consider re-running with a clearer goal."
                    ),
                    rationale_key="goal_interp.rationale.no_llm_router_pick",
                    rationale_args={
                        "name": top.recipe.name,
                        "score": f"{top.score:+d}",
                    },
                    source="router",
                    router_scores=scores_audit,
                )
            # No keyword hits OR no positive score — ask the user to be
            # more specific. The router scores are still surfaced via
            # router_scores for the CLI / UI to display.
            return GoalInterpretation(
                decision="clarify",
                rationale=(
                    "No LLM available and the user's goal doesn't keyword-match "
                    "any recipe. Asking the user to clarify which deliverable "
                    "they want."
                ),
                rationale_key="goal_interp.rationale.no_llm_clarify",
                rationale_args={},
                clarifying_questions=[
                    "Which kind of deliverable do you want — knowledge pack, "
                    "data report, or project handoff?"
                ],
                source="router",
                router_scores=scores_audit,
            )

        # LLM path.
        return self._llm_decide(
            user_goal=user_goal,
            snapshot=snapshot,
            ranked=ranked,
            scores_audit=scores_audit,
            prior_answers=prior_answers or [],
        )

    def _llm_decide(
        self,
        *,
        user_goal: str,
        snapshot: WorkspaceSnapshot | None,
        ranked: list[RecipeScore],
        scores_audit: list[dict[str, Any]],
        prior_answers: list[str],
    ) -> GoalInterpretation:
        recipes_block = _summarise_recipes([s.recipe for s in ranked])
        workspace_block = _summarise_workspace(snapshot)
        router_block = "Router's deterministic ranking (you may agree or override):\n" + "\n".join(
            f"  - {s.recipe.name}: score {s.score:+d}" for s in ranked
        )

        user_block = (
            f"User goal: {user_goal!r}\n\n{workspace_block}\n\n{recipes_block}\n\n{router_block}"
        )
        if prior_answers:
            answers_block = "\n".join(
                f"  Q{i + 1} → user answered: {a!r}" for i, a in enumerate(prior_answers)
            )
            user_block += f"\n\nPrior clarification round answers:\n{answers_block}"

        recipe_names = [r.name for r in self.registry.all()]
        schema = _build_decision_tool_schema(recipe_names)

        system_for_call = SYSTEM_PROMPT + "\n\n" + locale_instruction(self.locale)
        try:
            response = self.client.generate_structured(  # type: ignore[union-attr]
                system=system_for_call,
                messages=[{"role": "user", "content": user_block}],
                tool_name=INTERPRETER_TOOL_NAME,
                tool_description=INTERPRETER_TOOL_DESCRIPTION,
                tool_schema=schema,
            )
        except LLMClientError as exc:
            # The LLM call itself failed — degrade to router pick.
            top = ranked[0]
            err_short = str(exc)
            if len(err_short) > 160:
                err_short = err_short[:157] + "…"
            return GoalInterpretation(
                decision="pick" if top.score > 0 else "clarify",
                recipe_name=top.recipe.name if top.score > 0 else None,
                rationale=(
                    f"LLM call failed ({exc}); falling back to router. "
                    f"Top pick: {top.recipe.name} (score {top.score:+d})."
                ),
                rationale_key=(
                    "goal_interp.rationale.llm_failed_pick"
                    if top.score > 0
                    else "goal_interp.rationale.llm_failed_clarify"
                ),
                rationale_args={
                    "name": top.recipe.name,
                    "score": f"{top.score:+d}",
                    "err": err_short,
                },
                clarifying_questions=(
                    []
                    if top.score > 0
                    else [
                        "Which kind of deliverable do you want — knowledge pack, "
                        "data report, or project handoff?"
                    ]
                ),
                source="router",
                router_scores=scores_audit,
            )

        payload = response.payload
        try:
            interpretation = _coerce_llm_payload(payload, scores_audit)
        except ValidationError as exc:
            # LLM returned a malformed envelope — fall back.
            top = ranked[0]
            return GoalInterpretation(
                decision="pick" if top.score > 0 else "clarify",
                recipe_name=top.recipe.name if top.score > 0 else None,
                rationale=(
                    f"LLM returned an invalid envelope ({exc}); falling back to "
                    f"router top pick ({top.recipe.name})."
                ),
                rationale_key="goal_interp.rationale.llm_invalid_envelope",
                rationale_args={"name": top.recipe.name, "err": str(exc)[:160]},
                source="router",
                router_scores=scores_audit,
            )

        # Final safety net: if the LLM said "pick" but picked something
        # we don't have, refuse and degrade.
        if interpretation.decision == "pick" and interpretation.recipe_name not in recipe_names:
            top = ranked[0]
            return GoalInterpretation(
                decision="pick",
                recipe_name=top.recipe.name,
                rationale=(
                    f"LLM picked unknown recipe {interpretation.recipe_name!r}; "
                    f"router fallback to {top.recipe.name}."
                ),
                rationale_key="goal_interp.rationale.llm_unknown_recipe",
                rationale_args={
                    "ghost": str(interpretation.recipe_name),
                    "name": top.recipe.name,
                },
                source="router",
                router_scores=scores_audit,
            )

        return interpretation


def _coerce_llm_payload(
    payload: dict[str, Any], scores_audit: list[dict[str, Any]]
) -> GoalInterpretation:
    """Translate the strict-schema tool call payload into a
    :class:`GoalInterpretation`. ``router_scores`` is attached server-
    side (the LLM doesn't see it on the input + can't fabricate it on
    output)."""
    body = dict(payload)
    body["source"] = "llm"
    body["router_scores"] = scores_audit
    # Normalise None recipe_name when clarifying.
    if body.get("decision") == "clarify":
        body["recipe_name"] = None
        # Pydantic accepts missing field as default; we just need to
        # ensure clarifying_questions is at least 1 entry.
        if not body.get("clarifying_questions"):
            body["clarifying_questions"] = [
                "Could you say more about what kind of deliverable you want?"
            ]
    return GoalInterpretation.model_validate(body)
