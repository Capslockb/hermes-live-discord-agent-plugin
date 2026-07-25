#!/usr/bin/env python3
"""Detect stale repository-identity and license claims in public surfaces.

This check intentionally targets user-facing documentation, generated pages,
the installer clone source, and the docs generator. Runtime compatibility or
provenance references in Python modules are outside its scope and must be
classified before they are changed.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class ForbiddenClaim:
    pattern: re.Pattern[str]
    message: str


FORBIDDEN_CLAIMS = (
    ForbiddenClaim(
        re.compile(r"https://github\.com/Capslockb/gemini-live-discord-bridge(?:\.git)?"),
        "points users or generated documentation at the unrelated Gemini bridge repository",
    ),
    ForbiddenClaim(
        re.compile(r"\bMIT licensed\b", re.IGNORECASE),
        "claims an MIT license although this repository has no standalone LICENSE file",
    ),
    ForbiddenClaim(
        re.compile(r"\bOpen source,\s*self-hostable,\s*MIT\.?", re.IGNORECASE),
        "publishes an unsupported first-party MIT license claim",
    ),
    ForbiddenClaim(
        re.compile(r"\bMIT\.\s*See top of bridge\.py for full text\.?", re.IGNORECASE),
        "points to bridge.py as license text although it contains architecture documentation",
    ),
    ForbiddenClaim(
        re.compile(r"·\s*MIT\s*·", re.IGNORECASE),
        "publishes an unsupported MIT footer claim",
    ),
    ForbiddenClaim(
        re.compile(r"\bfree to fork\b", re.IGNORECASE),
        "implies redistribution rights that are not currently declared",
    ),
)


def iter_public_surfaces() -> list[Path]:
    """Return deterministic user-facing files covered by this guard."""
    paths = [
        ROOT / "README.md",
        ROOT / "CHANGELOG.md",
        ROOT / "install.sh",
        ROOT / "scripts" / "build_docs_site.py",
    ]
    paths.extend(sorted((ROOT / "docs").rglob("*.md")))
    paths.extend(sorted((ROOT / "docs-site").rglob("*.html")))
    return paths


def main() -> int:
    violations: list[str] = []

    for path in iter_public_surfaces():
        if not path.exists():
            violations.append(f"{path.relative_to(ROOT)}: missing expected public surface")
            continue

        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            for claim in FORBIDDEN_CLAIMS:
                if claim.pattern.search(line):
                    violations.append(
                        f"{path.relative_to(ROOT)}:{line_number}: {claim.message}"
                    )

    if violations:
        print("Public documentation identity check failed:", file=sys.stderr)
        for violation in violations:
            print(f"- {violation}", file=sys.stderr)
        return 1

    print("Public documentation identity check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
