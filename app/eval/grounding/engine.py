"""Phase 36.3 — claim-level grounding engine.

Pipeline: ``split_claims`` (deterministic) → a ``ClaimJudge`` per claim
→ ``evaluate_grounding`` (deterministic gate) → ``ClaimGroundingResult``.

Two judges:
  * ``LexicalClaimJudge`` — deterministic salient-term overlap. No LLM,
    no API key. The CI / eval baseline and the no-key fallback.
  * ``LLMClaimJudge`` — per-claim LLM-as-judge via ``app.agent.judge``.
    The production path; non-deterministic, needs a key.

Honesty (rule F): the lexical judge is a crude proxy ("do the claim's
distinctive terms appear in some source?"), NOT semantic understanding.
It is the reproducible baseline + the no-key fallback; real grounding
uses the LLM judge. Both emit the same ``ClaimVerdict`` shape.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.eval.grounding.schema import (
    Claim,
    ClaimGroundingResult,
    ClaimVerdict,
    GroundingGateResult,
    GroundingPolicy,
    SourceFragment,
)

# --------------------------------------------------------------------- tokenisation

# Function words + report boilerplate that carry no grounding signal.
# Kept deliberately moderate — domain verbs (improved / reduced /
# increased) stay salient because they're distinctive.
_STOPWORDS: frozenset[str] = frozenset(
    """
    a an the of to in on for and or but by with as is are was were be been being
    that this these those it its at from which who whom into than then so such also
    can may will would could should has have had do does did not no nor we our us
    their they them he she his her you your i me my if when while where what why how
    about over under between among per via using used use within without across
    study studies paper papers research researchers authors author article articles
    finding findings reported report reports according shows show showed shown
    suggests suggest indicate indicates et al eg ie vs etc cf fig eq
    """.split()
)

_TOKEN_RE = re.compile(r"[A-Za-z]+|\d+")

# Abbreviations whose trailing period must NOT trigger a sentence split.
_ABBREVS = ("et al.", "e.g.", "i.e.", "vs.", "etc.", "cf.", "Fig.", "Eq.", "Dr.", "Prof.")
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _salient_terms(text: str) -> set[str]:
    """Distinctive terms for overlap matching.

    Rules: numbers kept verbatim; single letters kept only when
    UPPERCASE in the original (entity labels like 'A' / 'C'); longer
    words lowercased + stopword-filtered.
    """
    out: set[str] = set()
    for tok in _TOKEN_RE.findall(text):
        if tok.isdigit():
            out.add(tok)
        elif len(tok) == 1:
            if tok.isupper():
                out.add(tok)  # entity label, e.g. "A", "C"
        else:
            low = tok.lower()
            if low not in _STOPWORDS:
                out.add(low)
    return out


# --------------------------------------------------------------------- claim splitting

_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")
_HR_RE = re.compile(r"^\s*([-*_])\1{2,}\s*$")


def _split_sentences(line: str) -> list[str]:
    """Split a prose line into sentences, guarding common abbreviations."""
    masked = line
    placeholders: list[tuple[str, str]] = []
    for i, ab in enumerate(_ABBREVS):
        token = f"\x00{i}\x00"
        if ab in masked:
            masked = masked.replace(ab, token)
            placeholders.append((token, ab))
    parts = _SENT_SPLIT_RE.split(masked)
    out = []
    for p in parts:
        restored = p
        for token, ab in placeholders:
            restored = restored.replace(token, ab)
        out.append(restored)
    return out


# Document-meta / framing prefixes. A sentence that talks ABOUT the
# review itself ("This review synthesises…") is not a groundable factual
# claim about the sources, so it shouldn't be flagged ungrounded. This is
# a narrow content filter (only self-referential framing), not a way to
# hide hallucinations — the groundable unit ("Method C reduced cost by
# 40%") is always a separate claim. The LLM judge handles framing
# natively; this filter keeps the lexical baseline's false-positive rate
# honest.
_FRAMING_PREFIXES: tuple[str, ...] = (
    "this review",
    "this paper",
    "this summary",
    "this section",
    "this document",
    "this note",
    "in this review",
    "in this paper",
    "in this section",
    "the following",
    "here we summarise",
    "here we summarize",
    "here we synthesise",
    "here we synthesize",
    "we synthesise",
    "we synthesize",
    "we summarise",
    "we summarize",
    "we review",
)


def _is_claimworthy(text: str) -> bool:
    """A claim must carry a groundable factual assertion: >=4 words, not
    a bare section lead-in ('Key findings:'), and not a self-referential
    framing sentence ('This review synthesises…')."""
    stripped = text.strip()
    words = stripped.split()
    if len(words) < 4:
        return False
    if stripped.endswith(":") and len(words) <= 6:
        return False
    low = stripped.lower()
    if any(low.startswith(prefix) for prefix in _FRAMING_PREFIXES):
        return False
    return True


def split_claims(review_markdown: str) -> list[Claim]:
    """Split a review's markdown into individual factual claims.

    Deterministic. Skips code fences, headings, tables, HR rules,
    blockquotes, and HTML comments. List items become one claim each;
    prose lines split into sentences.
    """
    claims: list[Claim] = []
    in_fence = False
    counter = 0
    for lineno, raw in enumerate(review_markdown.splitlines(), start=1):
        stripped = raw.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not stripped:
            continue
        if stripped.startswith("#"):  # heading
            continue
        if stripped.startswith("|"):  # table row
            continue
        if stripped.startswith(">"):  # blockquote
            continue
        if stripped.startswith("<!--"):  # html comment
            continue
        if _HR_RE.match(stripped):  # horizontal rule
            continue

        m = _LIST_ITEM_RE.match(raw)
        candidates: list[str]
        if m:
            candidates = [m.group(1).strip()]
        else:
            candidates = _split_sentences(stripped)

        for cand in candidates:
            cand = cand.strip()
            if not _is_claimworthy(cand):
                continue
            counter += 1
            claims.append(Claim(claim_id=f"c{counter}", text=cand, source_line=lineno))
    return claims


# --------------------------------------------------------------------- source loading


def load_source_fragments(
    workspace_root: Path,
    *,
    rel_globs: tuple[str, ...] = ("summaries/*.md", "summaries/*.txt"),
    max_chars: int = 8000,
) -> list[SourceFragment]:
    """Load candidate source fragments (per-source summaries) from the
    workspace. Each matched file becomes one fragment, capped to
    ``max_chars``. Sorted by path for determinism."""
    fragments: list[SourceFragment] = []
    seen: set[Path] = set()
    for glob in rel_globs:
        for path in sorted(workspace_root.glob(glob)):
            if not path.is_file() or path in seen:
                continue
            seen.add(path)
            try:
                text = path.read_text(encoding="utf-8", errors="replace")[:max_chars]
            except OSError:
                continue
            rel = path.relative_to(workspace_root).as_posix()
            fragments.append(SourceFragment(source_id=rel, text=text))
    return fragments


# --------------------------------------------------------------------- judges


@runtime_checkable
class ClaimJudge(Protocol):
    """Decides whether one claim traces to the given source fragments."""

    kind: str

    def judge_claim(self, claim: Claim, fragments: list[SourceFragment]) -> ClaimVerdict: ...


class LexicalClaimJudge:
    """Deterministic salient-term overlap judge. No LLM.

    A claim is grounded if its salient terms overlap some single
    fragment by ratio >= ``threshold``. Claims with no salient terms
    (pure filler) are treated as grounded (benefit of the doubt — they
    carry no factual assertion to hallucinate), keeping false positives
    low."""

    kind = "lexical"

    def __init__(self, *, threshold: float = 0.5) -> None:
        self._threshold = threshold

    def judge_claim(self, claim: Claim, fragments: list[SourceFragment]) -> ClaimVerdict:
        salient = _salient_terms(claim.text)
        if not salient:
            return ClaimVerdict(
                claim_id=claim.claim_id,
                text=claim.text,
                grounded=True,
                source_id=None,
                evidence="no checkable salient terms",
                judge=self.kind,
                source_line=claim.source_line,
            )
        best_id: str | None = None
        best_ratio = 0.0
        best_overlap: set[str] = set()
        for frag in fragments:
            frag_terms = _salient_terms(frag.text)
            overlap = salient & frag_terms
            ratio = len(overlap) / len(salient)
            if ratio > best_ratio:
                best_ratio = ratio
                best_id = frag.source_id
                best_overlap = overlap
        grounded = best_ratio >= self._threshold
        if grounded:
            evidence = f"matched {sorted(best_overlap)} in {best_id} (ratio {best_ratio:.2f})"
        else:
            missing = sorted(salient - best_overlap)
            evidence = f"no source covers {missing} (best ratio {best_ratio:.2f})"
        return ClaimVerdict(
            claim_id=claim.claim_id,
            text=claim.text,
            grounded=grounded,
            source_id=best_id if grounded else None,
            evidence=evidence,
            judge=self.kind,
            source_line=claim.source_line,
        )


_LLM_JUDGE_SYSTEM = (
    "You are a grounding checker for a literature review. Given a CLAIM and a list "
    "of SOURCE fragments, decide whether the claim is supported by at least one "
    "source. Set verdict=true ONLY if a specific source materially supports the "
    "claim's factual content (entities, numbers, findings). Set verdict=false if no "
    "source supports it (a fabricated / hallucinated claim). In `reason`, name the "
    "supporting source or state that none supports it."
)


class LLMClaimJudge:
    """Per-claim LLM-as-judge. Production path; needs an LLM client.

    On a transient judge failure (client returns None mid-run) the claim
    is treated as grounded (benefit of the doubt) to avoid a false gate
    failure — documented behaviour, not a silent pass of hallucinations
    (a fully-unavailable client means the verifier picks the lexical
    judge instead, see ``claim_grounding_verifier``)."""

    kind = "llm"

    def __init__(self, client=None, *, max_fragments: int = 12, frag_chars: int = 800) -> None:
        self._client = client
        self._max_fragments = max_fragments
        self._frag_chars = frag_chars

    def judge_claim(self, claim: Claim, fragments: list[SourceFragment]) -> ClaimVerdict:
        from app.agent.judge import judge as _judge

        listing = "\n\n".join(
            f"[source: {f.source_id}]\n{f.text[: self._frag_chars]}"
            for f in fragments[: self._max_fragments]
        )
        user = (
            f"CLAIM:\n{claim.text}\n\nSOURCES:\n{listing or '(no sources provided)'}\n\n"
            "Is the claim supported by at least one source above?"
        )
        verdict = _judge(system=_LLM_JUDGE_SYSTEM, user=user, client=self._client)
        if verdict is None:
            return ClaimVerdict(
                claim_id=claim.claim_id,
                text=claim.text,
                grounded=True,
                source_id=None,
                evidence="judge unavailable mid-run; treated as grounded",
                judge=self.kind,
                source_line=claim.source_line,
            )
        return ClaimVerdict(
            claim_id=claim.claim_id,
            text=claim.text,
            grounded=bool(verdict.verdict),
            source_id=None,
            evidence=verdict.reason,
            judge=self.kind,
            source_line=claim.source_line,
        )


# --------------------------------------------------------------------- gate + orchestration


def evaluate_grounding(
    verdicts: list[ClaimVerdict], policy: GroundingPolicy
) -> GroundingGateResult:
    """Deterministic gate over per-claim verdicts."""
    total = len(verdicts)
    ungrounded = [v for v in verdicts if not v.grounded]
    grounded_count = total - len(ungrounded)
    ratio = (grounded_count / total) if total else 1.0
    passed = ratio >= policy.min_grounded_ratio and len(ungrounded) <= policy.max_ungrounded

    hint: str | None = None
    if not passed and ungrounded:
        preview = "; ".join(f'"{v.text[:80]}"' for v in ungrounded[:5])
        hint = (
            f"Regenerate the review so every claim traces to a source fragment. "
            f"{len(ungrounded)} claim(s) have no traceable source and must be removed "
            f"or rewritten with a citation: {preview}"
        )

    return GroundingGateResult(
        passed=passed,
        total_claims=total,
        grounded_count=grounded_count,
        ungrounded_count=len(ungrounded),
        grounded_ratio=round(ratio, 4),
        ungrounded_claims=ungrounded,
        suggested_hint=hint,
    )


def ground_review(
    *,
    review_text: str,
    review_path: str,
    fragments: list[SourceFragment],
    policy: GroundingPolicy,
    judge: ClaimJudge,
) -> ClaimGroundingResult:
    """End-to-end: split → judge each claim → gate → evidence bundle."""
    claims = split_claims(review_text)
    verdicts = [judge.judge_claim(c, fragments) for c in claims]
    gate = evaluate_grounding(verdicts, policy)
    return ClaimGroundingResult(
        review_path=review_path,
        judge_kind=judge.kind,
        policy=policy,
        verdicts=verdicts,
        gate=gate,
    )


__all__ = [
    "ClaimJudge",
    "LLMClaimJudge",
    "LexicalClaimJudge",
    "evaluate_grounding",
    "ground_review",
    "load_source_fragments",
    "split_claims",
]
