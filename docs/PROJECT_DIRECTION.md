# LocalFlow Project Direction

This document records the working preference for LocalFlow's next phase.
Use it as the default decision guide when planning research, code changes,
UI changes, demos, and evaluation work.

## Core Positioning

LocalFlow should be treated first as a **local-first Agent Execution
Harness**, not as a desktop file organizer.

The important product promise is:

> An LLM agent can work on a real local workspace through typed plans,
> preview, approval, controlled execution, trace, independent verification,
> repair, and rollback.

Deliverable packs remain useful as demos and application-level workflows.
They should not obscure the deeper value: LocalFlow is the execution
harness that makes those workflows safe, reviewable, recoverable, and
measurable.

## Action Rules

- Prioritize harness capability over adding more narrow skills.
- Before accepting a feature, ask whether it improves safety,
  controllability, recoverability, verifiability, task success rate, or
  trace-based improvement.
- Do not treat "a template task runs" as agent intelligence. The target is
  more open-ended long-running work where failures can be located, repaired,
  retried, or rolled back.
- When studying reference projects, inspect architecture, task flow, state
  management, tool boundaries, permission model, evals, and failure recovery.
  Do not rely only on README claims or star counts.
- Keep deliverable packs as the demo layer, but keep the public narrative
  harness-first.
- UI changes should explain the harness lifecycle: plan, risk, preview,
  approval, execution trace, verification, repair, and rollback.

## Tracking Goal

Use this as the current Codex tracking goal for the project:

> Continuously research mature Agent Harness projects, extract their
> planning, tool-boundary, safe-execution, persistence, evaluation, and
> failure-recovery patterns, and use that evidence to reposition and evolve
> LocalFlow from a limited template-style local agent into a local,
> rollback-safe, verifiable, and continuously improving Agent Harness.

The goal is intentionally adjustable. Do not freeze the final product
direction before the research and eval evidence justify it.

## Current Roadmap Bias

> **2026-05-24 更新**：OpenHands 调研已完成
> ([docs/research/OPENHANDS_HARNESS_STUDY.md](research/OPENHANDS_HARNESS_STUDY.md))。
> Phase 24+ 锁定 **路线 B：阶段式 + 阶段内 react loop**。详见下方"架构路线"段。

### 架构路线（Phase 24+）

LocalFlow 当前是 **plan-once-execute-batch** 模型——`plan → dry-run → approve →
execute(顺序跑全部 action) → verify`。复杂任务一旦 plan 不够细就卡死。

Phase 24+ 走 **路线 B**：
- **保留**阶段骨架（plan / dry-run / approval / verify / rollback）—— 这是 LocalFlow
  对比 OpenHands 的核心差异化
- **改造** execute 阶段：每个 action 执行后，把结构化 observation 反馈给 LLM，
  LLM 决定下一个 action（在已批准的 plan 范围内 +/- N 步漂移）
- **不走**路线 A（全面 LLM-loop）—— 那会让 LocalFlow 变成 OpenHands 的弱复制品

### 必须坚守的差异化（不能丢）

OpenHands 没有但 LocalFlow 有的设计，这些是 LocalFlow 之所以是 LocalFlow 的原因：

1. 独立 Dry-run 阶段 + `dry_run.md` 确定性预览（OpenHands 把预览推给 LLM 自填，会幻觉）
2. RollbackManifest + hash-drift 检测（OpenHands 把撤销推给 git/user，不适合普通用户文件）
3. §10.7 ledger 纪律 / 内核加法不修改
4. 规则化 PolicyGuard 程序化路径越界检查（OpenHands 用 LLM analyzer 评估，可被注入误导）
5. 独立 Verifier 结构化 + 语义两层（OpenHands 只有 Critic 评分不阻塞）
6. Skill 是 plan 生成器，不是 executor（OpenHands Tool 是 plan+execute 揉合）

### Roadmap 步骤

1. ~~Compare 2-4 mature Agent Harness projects~~ — OpenHands ✅ 已完成；goose / Aider /
   SWE-agent 留待后续阶段需要时再做（用户偏好快速推进而非穷举调研）。
2. 清理 Phase 23 未提交工作（按 23.0 schema → 23.0 runtime → 23.1 UX 切 commit），
   完成 v0.23.0 发布。
3. **Phase 24 = C1 ActionEvent 重构** — 把 trace.jsonl / execution_log.jsonl /
   audit.jsonl 三流合一为单一 ActionEvent 流。是后续 react-loop 改造的基础。
4. Phase 25 = C4 Orphaned-Action 修复 — failed action 的结构化反馈喂回 LLM 做 REPAIR。
5. Phase 26 = 阶段内 react loop 落地 —— execute 阶段从 batch 改为 step-by-step。
6. Phase 27+ = Workspace 抽象（C3）+ ConfirmationPolicy 多档（C2）+ Harness 内核拆包
   （`localflow-harness` / `localflow-pack` 分离）。

每个 Phase 完成后回头审视本文件，按真实证据调整后续顺序。**不在 Phase 26 落地前提
Phase 27 细节计划**。

## Evidence Standard

Every important change should come with evidence from one of these surfaces:

- `trace.jsonl` or equivalent run history showing what happened.
- Independent verifier output showing whether the task met its criteria.
- Eval results showing task-level success, regression, or improvement.
- Rollback or repair evidence for failure-mode work.

For UI changes, the acceptance standard is that a user can tell what the
system plans to do, why it is allowed, what risk exists, whether the result
passed checks, and how to undo it.

## Boundaries

LocalFlow is not currently trying to become:

- a general OS-control agent;
- a low-code automation platform;
- a universal personal assistant;
- a system that exposes arbitrary shell execution as the default path.

These boundaries can change later, but only if trace, eval, and safety
evidence support the change.
