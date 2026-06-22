# SOURCES

## Research Papers & Studies

### 1. Grounding Study (2025)
**File**: `papers/grounding.txt`
**Finding**: Requiring every report claim to cite a source cut unsupported claims by 24% in generated reports. This establishes the importance of source grounding in AI-generated content.

### 2. HumanEval (2021)
**File**: `papers/humaneval.txt`
**Finding**: A hand-written code-synthesis benchmark where the evaluated code model reached 67% pass@1. This provides a baseline for code generation capabilities.

### 3. Task-Decomposition Study (2024)
**File**: `papers/planning.txt`
**Finding**: Plan-then-execute decomposition reduced step errors by 18% on WebArena. Demonstrates the value of structured planning in agent execution.

### 4. ReAct (2023)
**File**: `papers/react.txt`
**Finding**: Interleaving chain-of-thought reasoning with tool actions cut hallucinated actions by 30% on ALFWorld benchmark. Shows the benefit of reasoning-action integration.

### 5. Reflexion (2023)
**File**: `papers/reflexion.txt`
**Finding**: Agents reflecting on failures in natural language improved success rate by 11 points on HotpotQA. Highlights the value of self-reflection mechanisms.

### 6. Checkpoint-Rollback Study (2024)
**File**: `papers/rollback.txt`
**Finding**: Rollback on failed verification recovered 92% of corrupted runs. Demonstrates robust error recovery mechanisms.

### 7. Sandbox Isolation Study (2024)
**File**: `papers/sandbox.txt`
**Finding**: Container isolation of agent actions reduced host side-effects to zero across 1000 trials. Establishes the importance of safety through isolation.

### 8. SWE-bench-Verified (2024)
**File**: `papers/swe_bench.txt`
**Finding**: A baseline agent resolved 41% of issues on human-validated subset, establishing reference for software engineering tasks.

### 9. Toolformer (2023)
**File**: `papers/toolformer.txt`
**Finding**: Self-supervised API calls improved zero-shot accuracy by 9% on math tasks. Shows the value of tool learning capabilities.

### 10. Voyager (2023)
**File**: `papers/voyager.txt`
**Finding**: Skill-library reuse increased novel item discovery 3.3 times in Minecraft. Demonstrates the power of cumulative learning in open-ended environments.

## Evaluation Notes

### 1. Evaluation Metrics
**File**: `notes/note_eval_metrics.md`
**Key Point**: Agent evaluation should report recall and false-positive rate rather than model self-assessment. Emphasizes the need for objective evaluation metrics.

### 2. Failure Modes
**File**: `notes/note_failure_modes.md`
**Key Point**: Six common agent failure modes include goal drift, false completion, and context rot. Identifies critical challenges in agent reliability.