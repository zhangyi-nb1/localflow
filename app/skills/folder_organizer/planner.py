from __future__ import annotations

import uuid
from collections import defaultdict
from pathlib import PurePosixPath

from app.memory import NamingStyle, apply_naming_style
from app.schemas import ActionPlan, TaskSpec, WorkspaceSnapshot
from app.schemas.action import Action, ActionType, RiskLevel


# Phase 0 keeps the category map hard-coded here (and mirrored in skill.yaml).
# A later phase can load it from the manifest at runtime.
CATEGORY_TARGETS: dict[str, str] = {
    "pdf": "papers",
    "word": "documents",
    "excel": "spreadsheets",
    "tabular": "data",
    "structured": "data",
    "text": "notes",
    "image": "images",
    "audio": "audio",
    "video": "video",
    "archive": "archives",
    "code": "code",
    "other": "misc",
}

DUPLICATE_REPORT_NAME = "duplicates_report.md"


def _target_dir(file_type: str) -> str:
    return CATEGORY_TARGETS.get(file_type, "misc")


def _already_in_target(rel_path: str, target_dir: str) -> bool:
    """True if the file already lives at the top of its target directory."""
    parts = PurePosixPath(rel_path).parts
    return len(parts) >= 2 and parts[0] == target_dir


def plan_organization(
    task: TaskSpec,
    snapshot: WorkspaceSnapshot,
) -> ActionPlan:
    """Generate a deterministic, rule-based ActionPlan for the snapshot.

    The planner produces:
      * one mkdir per category directory that is needed and missing,
      * one move per file that isn't already in its category dir,
      * one index.md per non-empty category (listing the files we expect to
        live there after the moves),
      * one duplicates_report.md if any sha256 collisions exist (no delete).
    """
    plan_id = f"plan-{uuid.uuid4().hex[:8]}"
    actions: list[Action] = []
    expected_outputs: list[str] = []
    counter = 0

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"a-{counter:03d}"

    # 1. Bucket files by destination directory.
    by_target: dict[str, list] = defaultdict(list)
    for f in snapshot.files:
        target_dir = _target_dir(f.file_type)
        if _already_in_target(f.path, target_dir):
            continue
        by_target[target_dir].append(f)

    # 2. mkdir for each needed target dir.
    for target_dir in sorted(by_target):
        actions.append(
            Action(
                action_id=next_id(),
                action_type=ActionType.MKDIR,
                target_path=target_dir,
                reason=f"Create category directory for {target_dir}",
                risk_level=RiskLevel.LOW,
                reversible=True,
                requires_approval=True,
            )
        )

    # 3. One move per file.
    naming_style = task.preferences.get("naming_style", NamingStyle.ORIGINAL.value)
    for target_dir in sorted(by_target):
        for f in sorted(by_target[target_dir], key=lambda x: x.path):
            original_filename = PurePosixPath(f.path).name
            filename = apply_naming_style(original_filename, naming_style)
            target_path = f"{target_dir}/{filename}"
            reason = f"Categorize {f.file_type} file into {target_dir}/"
            if filename != original_filename:
                reason += f" (naming style: {naming_style})"
            actions.append(
                Action(
                    action_id=next_id(),
                    action_type=ActionType.MOVE,
                    source_path=f.path,
                    target_path=target_path,
                    reason=reason,
                    risk_level=RiskLevel.MEDIUM,
                    reversible=True,
                    requires_approval=True,
                )
            )

    # 4. Index per non-empty category (predicted membership).
    membership_predicted: dict[str, list[str]] = defaultdict(list)
    for f in snapshot.files:
        td = _target_dir(f.file_type)
        if _already_in_target(f.path, td):
            membership_predicted[td].append(PurePosixPath(f.path).name)
        else:
            membership_predicted[td].append(PurePosixPath(f.path).name)
    for target_dir in sorted(membership_predicted):
        members = sorted(set(membership_predicted[target_dir]))
        if not members:
            continue
        content = _render_index_md(target_dir, members)
        index_rel = f"{target_dir}/index.md"
        actions.append(
            Action(
                action_id=next_id(),
                action_type=ActionType.INDEX,
                target_path=index_rel,
                reason=f"Generate index for {target_dir}/",
                risk_level=RiskLevel.LOW,
                reversible=True,
                requires_approval=False,
                metadata={"content": content, "overwrite_existing": True},
            )
        )
        expected_outputs.append(index_rel)

    # 5. Duplicate detection — report only, no deletion (Rule 4).
    dup_groups: dict[str, list[str]] = defaultdict(list)
    for f in snapshot.files:
        if f.sha256:
            dup_groups[f.sha256].append(f.path)
    duplicates = {h: paths for h, paths in dup_groups.items() if len(paths) > 1}
    if duplicates:
        content = _render_duplicates_md(duplicates)
        actions.append(
            Action(
                action_id=next_id(),
                action_type=ActionType.INDEX,
                target_path=DUPLICATE_REPORT_NAME,
                reason="Report duplicate file candidates (no deletion).",
                risk_level=RiskLevel.LOW,
                reversible=True,
                requires_approval=False,
                metadata={"content": content, "overwrite_existing": True},
            )
        )
        expected_outputs.append(DUPLICATE_REPORT_NAME)

    file_count = sum(1 for a in actions if a.action_type == ActionType.MOVE)
    dir_count = sum(1 for a in actions if a.action_type == ActionType.MKDIR)
    summary = (
        f"Categorize {file_count} file(s) into {dir_count} directory(ies); "
        f"generate {len(expected_outputs)} index/report file(s)."
    )
    risk_summary = (
        "Medium risk: moves are reversible via rollback manifest. "
        "No deletes. Existing index files are auto-suffixed instead of overwritten."
    )
    return ActionPlan(
        plan_id=plan_id,
        task_id=task.task_id,
        summary=summary,
        actions=actions,
        expected_outputs=expected_outputs,
        risk_summary=risk_summary,
    )


def _render_index_md(target_dir: str, files: list[str]) -> str:
    lines = [f"# {target_dir}/", "", f"_{len(files)} file(s)_", ""]
    for name in files:
        lines.append(f"- `{name}`")
    lines.append("")
    return "\n".join(lines)


def _render_duplicates_md(groups: dict[str, list[str]]) -> str:
    lines = ["# Duplicate file candidates", "", "_Files sharing the same SHA-256 hash._", ""]
    lines.append("> Phase 0 policy: duplicates are reported, never deleted.")
    lines.append("")
    for digest, paths in sorted(groups.items()):
        lines.append(f"## `{digest[:16]}…`")
        for p in sorted(paths):
            lines.append(f"- `{p}`")
        lines.append("")
    return "\n".join(lines)
