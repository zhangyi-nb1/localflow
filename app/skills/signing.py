"""v0.16.0 — HMAC-SHA256 signing for external skills.

The external skill loader (Phase 4.1) treats every loaded plug-in as
trusted Python code. Two opt-in safety knobs already exist:

  * ``LOCALFLOW_ENABLE_EXTERNAL_SKILLS=1`` — explicit enable.
  * ``LOCALFLOW_DISABLE_EXTERNAL_SKILLS=1`` — kill switch.

v0.16 adds a third: **signature verification**. When
``LOCALFLOW_REQUIRE_SIGNED_SKILLS=1`` is set, the loader refuses to
register any external skill whose ``signature.txt`` is missing,
malformed, or doesn't match the HMAC-SHA256 of its
``skill.py + skill.yaml`` bytes under the shared signing key.

This isn't proper code signing (no PKI, no revocation, no audit
trail). It's a **tampering-detection** mechanism: once you've audited
a skill and signed it with your secret, the loader will refuse to
load a modified version of the same skill until you re-sign. Useful
for ops scenarios where you ship a curated set of internal skills
and want CI to detect drift.

Key sources (in precedence order):
  1. ``LOCALFLOW_SKILL_SIGNING_KEY`` env var (hex-encoded bytes).
  2. ``~/.localflow/memory/skill_signing_key`` file (raw bytes).

When BOTH are absent and ``LOCALFLOW_REQUIRE_SIGNED_SKILLS=1`` is
set, the loader treats this as a configuration error and refuses to
load any external skill (fail-closed).
"""

from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path

REQUIRE_ENV = "LOCALFLOW_REQUIRE_SIGNED_SKILLS"
KEY_ENV = "LOCALFLOW_SKILL_SIGNING_KEY"
SIGNATURE_FILENAME = "signature.txt"
SIGNED_FILES = ("skill.py", "skill.yaml")
"""The set of files signed. Anything else in the skill dir is NOT
covered by the signature — change set is deliberately small so the
signed payload is stable across reformatting / renames of helper
modules. If a skill needs to ship helpers, the helpers' integrity is
the skill author's responsibility."""


def signing_required() -> bool:
    raw = os.environ.get(REQUIRE_ENV, "")
    return raw.strip().lower() in ("1", "true", "yes", "on")


def load_signing_key(home: Path | None = None) -> bytes | None:
    """Resolve the signing key from env or memory file. Returns None
    when no key is configured anywhere."""
    env_val = os.environ.get(KEY_ENV, "").strip()
    if env_val:
        try:
            return bytes.fromhex(env_val)
        except ValueError:
            # Allow ASCII passphrases as a fallback — convert to bytes.
            return env_val.encode("utf-8")
    if home is None:
        env_home = os.environ.get("LOCALFLOW_HOME")
        home_path = Path(env_home) if env_home else (Path.home() / ".localflow")
    else:
        home_path = home
    key_path = home_path / "memory" / "skill_signing_key"
    if key_path.exists() and key_path.is_file():
        try:
            return key_path.read_bytes().strip()
        except OSError:
            return None
    return None


def compute_signature(skill_dir: Path, key: bytes) -> str:
    """HMAC-SHA256(key, concat(skill.py bytes, skill.yaml bytes)) as
    lowercase hex. Missing files in SIGNED_FILES contribute empty
    bytes — a skill without skill.yaml still gets a stable signature
    (only skill.py contents)."""
    mac = hmac.new(key, digestmod=hashlib.sha256)
    for name in SIGNED_FILES:
        path = skill_dir / name
        if path.exists() and path.is_file():
            mac.update(path.read_bytes())
    return mac.hexdigest()


def write_signature(skill_dir: Path, key: bytes) -> str:
    """Compute + persist the signature for ``skill_dir``. Returns the
    written digest."""
    digest = compute_signature(skill_dir, key)
    (skill_dir / SIGNATURE_FILENAME).write_text(digest + "\n", encoding="utf-8")
    return digest


def read_signature(skill_dir: Path) -> str | None:
    sig_path = skill_dir / SIGNATURE_FILENAME
    if not sig_path.exists() or not sig_path.is_file():
        return None
    try:
        return sig_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def verify_signature(skill_dir: Path, key: bytes) -> bool:
    """True iff the on-disk signature matches the freshly-computed one.
    Uses constant-time comparison to avoid trivial timing attacks."""
    expected = read_signature(skill_dir)
    if not expected:
        return False
    actual = compute_signature(skill_dir, key)
    return hmac.compare_digest(expected, actual)
