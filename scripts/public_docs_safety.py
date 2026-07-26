#!/usr/bin/env python3
"""Metadata-only safety scanner for public-facing documentation."""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

DOC_NAMES = {"README.md", "SECURITY.md", "CONTRIBUTING.md", "AGENTS.md"}
DOC_DIR_PARTS = {"docs", "doc", "website", "site", "public", "docs-site"}
FIXTURE_PARTS = {"tests", "fixtures", "public-docs"}
DOC_EXTS = {".md", ".mdx", ".rst", ".txt", ".html", ".htm"}
EXCLUDE_PARTS = {"i18n", "CHANGELOG.md", "sessions", "vendor", "node_modules", ".git", "__pycache__", ".pytest_cache"}

RULES = [
    ("PDS001", re.compile(r"(?i)\b(ignore|disregard|override)\b.{0,100}\b(previous|above|system|developer|policy|instruction)s?\b")),
    ("PDS002", re.compile(r"(?i)\b(reveal|print|show|exfiltrate|leak)\b.{0,100}\b(secret|token|credential|password|policy|system prompt|developer message)s?\b")),
    ("PDS003", re.compile(r"(?i)\b(approve|merge|push|deploy|purchase|transfer|delete|rotate|disable)\b.{0,100}\b(PR|pull request|repository|repo|payment|account|guard|check|policy|automation)\b")),
    ("PDS004", re.compile(r"(?i)\b(privileged command|private control|non-public guard|secret marker|trusted[- ]identity rule|mutation authorization|worker queue|controller lease|private escalation)\b")),
]
UNCERTAIN = re.compile(r"(?i)\b(maintaining model|automation agent|autonomous maintainer|repository bot)\b.{0,100}\b(must|shall|required to|always|never|use tool|run command|obey|ignore|stop when|final status)\b")
BENIGN_UNCERTAIN = re.compile(r"(?i)\b(example|sample|template|user-facing|configuration|API|worker thread|service worker|inference|event loop|model name|route|provider|guardrail|security policy|documentation)\b")
BENIGN_PRODUCT = re.compile(r"(?i)\b(sponsor|sponsorship|release pipeline|reviews? every pull request|security|configuration|configured|deploy|deployment|feature flag|environment variable|env var|health check|migration|healthcheck|provider override|combo|compact_prompt|system prompt used during compaction|auth token|credential|locale|CI pipeline|quality gate|merge|pull request|token validation|instance)\b")


def default_branch() -> str:
    explicit = os.environ.get("GITHUB_BASE_REF") or os.environ.get("DEFAULT_BRANCH")
    if explicit:
        return explicit
    p = subprocess.run(["git", "symbolic-ref", "refs/remotes/origin/HEAD"], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if p.returncode == 0 and "/" in p.stdout:
        return p.stdout.strip().rsplit("/", 1)[-1]
    return "main"


def is_public_doc(path: str, include_fixtures: bool = False) -> bool:
    p = Path(path)
    if not p.exists() or not p.is_file():
        return False
    parts = set(p.parts)
    if parts & EXCLUDE_PARTS:
        return False
    if include_fixtures and FIXTURE_PARTS <= parts and p.suffix.lower() in DOC_EXTS:
        return True
    return p.name in DOC_NAMES or (p.suffix.lower() in DOC_EXTS and bool(parts & DOC_DIR_PARTS))


def changed_files() -> list[str]:
    base = default_branch()
    p = subprocess.run(["git", "diff", "--name-only", "--diff-filter=ACMRT", f"origin/{base}...HEAD"], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if p.returncode == 0:
        return p.stdout.splitlines()
    p = subprocess.run(["git", "diff", "--name-only", "--diff-filter=ACMRT", "--cached"], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if p.returncode == 0:
        return p.stdout.splitlines()
    return []


def changed_added_lines(files: list[str]) -> dict[str, set[int]] | None:
    if not files:
        return {}
    base = default_branch()
    p = subprocess.run(["git", "diff", "--unified=0", "--diff-filter=ACMRT", f"origin/{base}...HEAD", "--", *files], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if p.returncode != 0:
        return None
    out: dict[str, set[int]] = {}
    cur = None
    new_line = None
    for line in p.stdout.splitlines():
        if line.startswith("+++ b/"):
            cur = line[6:]
            out.setdefault(cur, set())
        elif line.startswith("@@") and cur:
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if m:
                new_line = int(m.group(1))
        elif cur and new_line is not None:
            if line.startswith("+") and not line.startswith("+++"):
                out.setdefault(cur, set()).add(new_line)
                new_line += 1
            elif not line.startswith("-"):
                new_line += 1
    return out


def scan_file(path: str, line_numbers) -> list[tuple[str, int, str]]:
    findings = []
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return [(path, 1, "PDS_READ_ERROR")]
    for i in line_numbers:
        if i < 1 or i > len(lines):
            continue
        line = lines[i - 1]
        quoted_example = bool(re.search(r'"[^"]*(ignore|disregard|override|reveal|show me your system prompt)[^"]*"', line, re.I))
        human_guidance = bool(re.search(r"(?i)(contributor'?s? (PR|pull request)|merge via (github|the )|so they get credit|always merge|never close a contributor)", line))
        for rule_id, rx in RULES:
            if not rx.search(line):
                continue
            if rule_id in {"PDS001", "PDS002"} and quoted_example:
                continue
            if rule_id == "PDS003" and (human_guidance or BENIGN_PRODUCT.search(line)):
                continue
            findings.append((path, i, rule_id))
        if UNCERTAIN.search(line) and not BENIGN_UNCERTAIN.search(line) and not human_guidance:
            findings.append((path, i, "PDS005"))
    return findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--include-test-fixtures", action="store_true")
    args = ap.parse_args()
    include_fixtures = args.include_test_fixtures or args.all
    candidates = [str(x) for x in Path(".").rglob("*") if x.is_file()] if args.all else changed_files()
    files = [f for f in candidates if is_public_doc(f, include_fixtures)]
    added = None if args.all else changed_added_lines(files)
    findings = []
    for f in files:
        if added is None:
            try:
                line_numbers = range(1, len(Path(f).read_text(encoding="utf-8", errors="ignore").splitlines()) + 1)
            except Exception:
                line_numbers = [1]
        else:
            line_numbers = sorted(added.get(f, set()))
        findings.extend(scan_file(f, line_numbers))
    if findings:
        print("public-docs-safety: FAIL")
        for f, i, rule_id in findings:
            print(f"{f}:{i}: {rule_id}")
        return 1
    print("public-docs-safety: PASS")
    return 0

if __name__ == "__main__":
    sys.exit(main())
