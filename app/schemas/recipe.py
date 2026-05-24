"""Phase 17 — Recipe / Pack System schema (v0.17.0).

A **Recipe** is the product-level abstraction above TaskGraph. While
TaskGraph asks "which skills run in what order", a Recipe answers
"what kind of deliverable pack are we building, and is this workspace
suitable for it?".

Recipes map onto the productisation guide's §5.1 / §6 / §12 Phase B:
the user picks a Pack (Research Pack / Data Report Pack / Project
Handoff Pack) without having to know skill names. The router (no LLM
in Phase 17 — keyword + workspace signal only) selects a recipe; the
:meth:`RecipeSpec.compile_to_taskgraph` method then emits the
underlying TaskGraph the existing runner already knows how to drive.

§10.7 invariant: zero kernel changes — Recipe layer compiles DOWN to
the v0.11 TaskGraph schema. The runner, executor, verifier, and
rollback paths are untouched.

Fields mirror the productisation guide's §12 Phase B exactly:
    name / description / input_expectation / stages /
    expected_outputs / verifiers / repair_policy
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.task import DEFAULT_LOCALE, Locale
from app.schemas.taskgraph import StageFailurePolicy, StageSpec, TaskGraph


class InputExpectation(BaseModel):
    """What kind of workspace this recipe is suitable for.

    Used by :class:`app.recipes.router.RecipeRouter` to score candidate
    recipes against a real workspace's :class:`WorkspaceSnapshot` —
    e.g. ``data_report_pack`` wants tabular files, ``research_pack``
    wants a mixed pile of PDFs + notes + data.

    All fields are advisory: the router uses them to rank candidates,
    but the user can always force any recipe via
    ``localflow pack run <name>`` regardless of fit.
    """

    file_kinds: list[str] = Field(
        default_factory=list,
        description=(
            "FileMeta.kind values this recipe benefits from "
            "(e.g. ['pdf', 'tabular', 'image', 'markdown', 'text']). "
            "Empty list means the recipe is kind-agnostic."
        ),
    )
    min_files: int = Field(
        default=1,
        ge=0,
        description="Recipe is only suitable if the workspace has at least this many files.",
    )
    require_any: list[str] = Field(
        default_factory=list,
        description=(
            "FileMeta.kind values where at least ONE must be present. "
            "Empty list disables this check."
        ),
    )
    keywords: list[str] = Field(
        default_factory=list,
        description=(
            "Lowercase substrings the router matches against the user's goal text. "
            "Each hit contributes to the recipe's score. Empty list means goal-agnostic."
        ),
    )


class RecipeStage(BaseModel):
    """A stage inside a recipe — the YAML-authoring layer that compiles
    down to :class:`StageSpec`.

    Identical to ``StageSpec`` in spirit, but kept separate so the
    Recipe schema can evolve (e.g. add `verifiers:` per stage) without
    bumping the TaskGraph schema. The :meth:`to_stage_spec` method
    handles the translation.
    """

    stage_id: str = Field(..., description="Unique within the parent recipe.")
    title: str
    skill: str
    planner: Literal["rule", "llm"] = "rule"
    expected_outputs: list[str] = Field(default_factory=list)
    allowed_actions: list[str] | None = None
    forbidden_actions: list[str] = Field(default_factory=list)
    failure_policy: StageFailurePolicy = StageFailurePolicy.ABORT
    max_retries: int = Field(default=1, ge=1)
    notes: str | None = None

    def to_stage_spec(self) -> StageSpec:
        """Translate a recipe stage to a runnable :class:`StageSpec`."""
        return StageSpec(
            stage_id=self.stage_id,
            title=self.title,
            skill=self.skill,
            planner=self.planner,
            expected_outputs=list(self.expected_outputs),
            allowed_actions=self.allowed_actions,
            forbidden_actions=list(self.forbidden_actions),
            failure_policy=self.failure_policy,
            max_retries=self.max_retries,
            notes=self.notes,
        )


class RepairPolicy(BaseModel):
    """Recipe-level repair settings.

    Phase 17 carries policy metadata only — the actual repair loop
    machinery (Phase 13 semantic verifier + retry) is unchanged. When
    ``enabled`` is true, every stage whose individual failure_policy is
    ABORT gets promoted to REPAIR at compile time. The user-facing
    contract: "auto-repair this pack if a stage fails verification."
    """

    enabled: bool = Field(
        default=False,
        description=(
            "Master switch. When false, stages keep their authored "
            "failure_policy. When true, ABORT stages are rewritten to "
            "REPAIR during compile_to_taskgraph()."
        ),
    )
    max_rounds: int = Field(
        default=1,
        ge=1,
        le=3,
        description=(
            "Maximum repair attempts per stage. Applied as StageSpec.max_retries "
            "for every promoted REPAIR stage."
        ),
    )


class RecipeSpec(BaseModel):
    """The product-level Pack definition.

    Field order matches the productisation guide §12 Phase B exactly:
        name / description / input_expectation / stages /
        expected_outputs / verifiers / repair_policy
    """

    name: str = Field(
        ...,
        description=(
            "Unique recipe identifier (e.g. 'research_pack'). Used as the CLI "
            "argument: 'localflow pack run research_pack'."
        ),
    )
    title: str = Field(
        ...,
        description="Human-readable name shown in `localflow pack list` and the UI.",
    )
    description: str = Field(
        ...,
        description=(
            "One-paragraph product pitch: what kind of deliverable pack this "
            "produces, who it's for. Surfaces in the UI as the pack's card subtitle."
        ),
    )
    input_expectation: InputExpectation = Field(default_factory=InputExpectation)
    stages: list[RecipeStage] = Field(..., min_length=1)
    expected_outputs: list[str] = Field(
        default_factory=list,
        description=(
            "Pack-level deliverables the user sees after a successful run "
            "(e.g. README.md, SOURCES.md). Sum of every stage's outputs plus "
            "any synthesised top-level files. Surfaced verbatim in the dry-run "
            "summary and in `localflow pack describe`."
        ),
    )
    verifiers: list[str] = Field(
        default_factory=list,
        description=(
            "Recipe-level grader names (registered in app.eval.graders) the "
            "verifier should run AFTER the pack finishes. Phase 17 records "
            "these as metadata; Phase 19 wires them into the harness."
        ),
    )
    repair_policy: RepairPolicy = Field(default_factory=RepairPolicy)
    repair_target_map: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Phase 21 — verifier_name → stage_id mapping that tells the "
            "recipe auto-repair loop which stage to replay when a "
            "deliverable verifier fails. Stages absent from this map (or "
            "verifiers not listed) default to the LAST LLM-planned "
            "stage of the recipe, which is the synthesis step in the "
            "shipped flagships (most verifier failures trace back to the "
            "agent's prose / index generation). Authors can override per "
            "verifier — e.g. `coverage_verifier: s1_organize` so failed "
            "coverage triggers a re-organize."
        ),
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Free-form tags ('research', 'data', 'handoff') for UI grouping.",
    )
    allow_compute_action: bool = Field(
        default=False,
        description=(
            "Phase 24 — capability-first escape hatch. When False (default), "
            "no stage of this recipe may declare ``python_compute`` in its "
            "``allowed_actions``; ``compile_to_taskgraph()`` raises ValueError "
            "if a stage tries to. When True, the recipe AUTHOR has explicitly "
            "opted into the third §10.7 kernel exception — every approval "
            "prompt for a PYTHON_COMPUTE action will still surface the script "
            "and warnings. This flag is the audit-trail anchor: it makes "
            "every recipe that touches the compute path grep-able."
        ),
    )
    enable_react_mode: bool = Field(
        default=False,
        description=(
            "Phase 26 — opt into the execute-stage react loop. When False "
            "(default), each stage runs as plan-once-execute-batch — exactly "
            "v0.23.x behaviour. When True, the runtime consults the LLM "
            "between actions and may apply REPLACE / INSERT / SKIP decisions "
            "within a bounded drift budget (default 3 steps per stage). "
            "Switching this on is the recipe AUTHOR's explicit acceptance "
            "of the §10.7 4th deliberate exception — see "
            "docs/PHASE_26_DESIGN.md and docs/REACT_LOOP.md. The flag is "
            "the audit-trail anchor: grepping ``enable_react_mode: true`` "
            "lists every recipe that may run mid-execute LLM decisions."
        ),
    )

    @model_validator(mode="after")
    def _unique_stage_ids(self) -> "RecipeSpec":
        seen: set[str] = set()
        for s in self.stages:
            if s.stage_id in seen:
                raise ValueError(f"duplicate stage_id in recipe: {s.stage_id!r}")
            seen.add(s.stage_id)
        return self

    @model_validator(mode="after")
    def _check_compute_capability_opt_in(self) -> "RecipeSpec":
        """Phase 24 — enforce the capability-first contract.

        When ``allow_compute_action`` is False (default), no stage may
        list ``python_compute`` in its ``allowed_actions``. This makes
        the third §10.7 kernel exception (PYTHON_COMPUTE) discoverable:
        every recipe that opts in flips one explicit bit, and grepping
        ``allow_compute_action: true`` lists the entire surface area.
        """
        if self.allow_compute_action:
            return self
        for stage in self.stages:
            allowed = stage.allowed_actions or []
            if "python_compute" in allowed:
                raise ValueError(
                    f"stage {stage.stage_id!r} declares 'python_compute' in "
                    f"allowed_actions but recipe {self.name!r} has "
                    f"allow_compute_action=False. Set allow_compute_action=True "
                    f"on the recipe to opt into the §10.7 ComputeAction "
                    f"capability (see docs/COMPUTE_ACTION.md)."
                )
        return self

    def resolve_repair_target(self, verifier_name: str) -> str | None:
        """Phase 21 — pick the stage_id to replay when ``verifier_name``
        fails.

        Lookup order:

          1. ``repair_target_map[verifier_name]`` if the author specified
             a mapping AND the target stage exists.
          2. The recipe's LAST LLM-planned stage (most flagships' synth
             step). This is the right default because deliverable
             verifiers usually catch synthesis-level issues (README
             wording / SOURCES citations / pack-level coverage).
          3. None if the recipe has zero LLM stages. The repair loop
             treats this as "can't repair this failure" and halts.
        """
        target = self.repair_target_map.get(verifier_name)
        if target and any(s.stage_id == target for s in self.stages):
            return target
        # Default: last LLM-planned stage.
        for stage in reversed(self.stages):
            if stage.planner == "llm":
                return stage.stage_id
        return None

    def compile_to_taskgraph(
        self,
        *,
        workspace_root: str,
        user_goal: str | None = None,
        forbidden_actions: list[str] | None = None,
        forbidden_paths: list[str] | None = None,
        preferences: dict[str, Any] | None = None,
        locale: Locale | str | None = None,
    ) -> TaskGraph:
        """Emit the underlying TaskGraph the existing runner consumes.

        Phase 17's whole point: a Recipe is a higher-level concept, but
        execution semantics still go through the v0.11 TaskGraph. This
        method is the bridge.

        When ``repair_policy.enabled`` is true, every stage with
        failure_policy=ABORT is promoted to REPAIR with
        max_retries=repair_policy.max_rounds. SKIP / CONTINUE stages
        keep their authored policy (they're intentional opt-outs).
        """
        stages = [s.to_stage_spec() for s in self.stages]

        if self.repair_policy.enabled:
            rounds = self.repair_policy.max_rounds
            for stage in stages:
                if stage.failure_policy is StageFailurePolicy.ABORT:
                    stage.failure_policy = StageFailurePolicy.REPAIR
                    stage.max_retries = rounds

        # Phase 20 — auto-propagate recipe intent into stage preferences:
        # a recipe that lists ``review_queue_verifier`` clearly WANTS
        # unclassifiable files routed to ``review/``, so the organizer
        # stage should respect that without the user having to set the
        # memory preference manually. User-supplied preferences win on
        # conflict (a CLI ``--preferences`` flag is the override path).
        merged_prefs: dict[str, Any] = {}
        if "review_queue_verifier" in self.verifiers:
            merged_prefs["route_low_confidence_to_review"] = True
        if preferences:
            merged_prefs.update(preferences)

        # Phase 24 — when the recipe didn't opt into ComputeAction, add
        # ``python_compute`` to the graph-level forbidden_actions so the
        # policy guard rejects any stage-level skill that tries to emit
        # one anyway (e.g. an LLM-planned agent stage hallucinates a
        # PYTHON_COMPUTE action). This is belt-and-braces: the schema
        # validator already rejected recipes that DECLARE python_compute
        # in allowed_actions; this catches accidental emission.
        if forbidden_actions is not None:
            graph_forbidden = list(forbidden_actions)
        else:
            graph_forbidden = ["delete", "overwrite", "shell"]
        if not self.allow_compute_action and "python_compute" not in graph_forbidden:
            graph_forbidden.append("python_compute")

        return TaskGraph(
            user_goal=user_goal or self.description,
            workspace_root=workspace_root,
            stages=stages,
            forbidden_actions=graph_forbidden,
            forbidden_paths=list(forbidden_paths) if forbidden_paths else [],
            preferences=merged_prefs,
            locale=locale if locale is not None else DEFAULT_LOCALE,  # type: ignore[arg-type]
        )
