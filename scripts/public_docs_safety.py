#!/usr/bin/env python3
"""Metadata-only safety scanner for public-facing documentation."""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Iterable

SUPPORTED_EXTS = {".md", ".mdx", ".rst", ".txt", ".adoc", ".asciidoc", ".html", ".htm"}
ROOT_DOC_NAMES = {
    "security.md",
    "contributing.md",
    "code_of_conduct.md",
    "agents.md",
    "support.md",
    "governance.md",
    "pull_request_template.md",
}
DOC_DIRS = {"docs", "doc", "docs-site", "website", "site", "public"}
EXCLUDED_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    "vendor",
    "sessions",
    "i18n",
}
FIXTURE_PREFIX = ("tests", "fixtures", "public-docs")

RULES = (
    (
        "PDS001",
        "POSSIBLE_PROMPT_INJECTION",
        re.compile(
            r"(?is)\b(ignore|disregard|override)\b.{0,180}"
            r"\b(previous|above|system|developer|policy|instructions?)\b"
        ),
    ),
    (
        "PDS002",
        "POSSIBLE_PROMPT_INJECTION",
        re.compile(
            r"(?is)\b(reveal|print|show|exfiltrate|leak)\b.{0,180}"
            r"\b(secret|token|credential|password|policy|system prompt|developer message)\b"
        ),
    ),
    (
        "PDS003",
        "MODEL_DIRECTED_INSTRUCTION",
        re.compile(
            r"(?is)\b(approve|merge|push|deploy|purchase|transfer|delete|rotate|disable|bypass)\b"
            r".{0,180}\b(this|current|the|all|tests?|checks?|PR|pull request|repository|repo|"
            r"payment|account|guard|policy|automation)\b"
        ),
    ),
    (
        "PDS004",
        "INTERNAL_AUTOMATION_POLICY",
        re.compile(
            r"(?is)\b(privileged command|private control|non-public guard|secret marker|"
            r"trusted[- ]identity rule|mutation authorization|worker queue|controller lease|"
            r"private escalation|completion contract)\b"
        ),
    ),
    (
        "PDS005",
        "MODEL_DIRECTED_INSTRUCTION",
        re.compile(
            r"(?is)\b(maintaining model|automation agent|autonomous maintainer|repository bot|"
            r"AI agent)\b.{0,180}\b(must|shall|required to|always|never|use tool|run command|"
            r"obey|ignore|stop when|final status)\b"
        ),
    ),
    (
        "PDS006",
        "INTERNAL_AUTOMATION_POLICY",
        re.compile(
            r"(?im)(?:^|\s)/(goal|subgoal|done_when|notification|authority)\b|"
            r"\bdelegate_task\b"
        ),
    ),
)

ERROR_FINDINGS = {
    "compare": ("<comparison>", 1, "SCANNER_ERROR", "PDS900"),
    "read": ("<unreadable>", 1, "SCANNER_ERROR", "PDS901"),
    "internal": ("<scanner>", 1, "SCANNER_ERROR", "PDS902"),
}


class ComparisonError(RuntimeError):
    pass


def _parts(path: str) -> tuple[str, ...]:
    return tuple(part.casefold() for part in PurePosixPath(path.replace("\\", "/")).parts)


def is_public_doc_path(path: str, include_fixtures: bool = False) -> bool:
    """Classify a repository path without requiring it to exist."""
    p = PurePosixPath(path.replace("\\", "/"))
    parts = _parts(path)
    if not parts or any(part in EXCLUDED_DIRS for part in parts):
        return False

    suffix = p.suffix.casefold()
    name = p.name.casefold()

    if include_fixtures and parts[:3] == FIXTURE_PREFIX and suffix in SUPPORTED_EXTS:
        return True

    if len(parts) == 1:
        if name in ROOT_DOC_NAMES:
            return True
        if p.stem.casefold() == "readme" and suffix in SUPPORTED_EXTS:
            return True

    if parts[0] == "readme" and suffix in SUPPORTED_EXTS:
        return True

    if name == "agents.md":
        return True

    if parts[:2] == (".github", "codeowners"):
        return True

    if parts and parts[0] == ".github":
        if len(parts) == 2 and name in {
            "pull_request_template.md",
            "issue_template.md",
            "security.md",
            "contributing.md",
            "code_of_conduct.md",
            "support.md",
            "governance.md",
        }:
            return True
        if len(parts) >= 3 and parts[1] in {"pull_request_template", "issue_template"}:
            # Issue-form YAML remains excluded until a bounded metadata parser is accepted.
            return suffix in SUPPORTED_EXTS

    if parts[0] in DOC_DIRS and suffix in SUPPORTED_EXTS:
        return True

    return False


def _git(args: list[str]) -> str:
    p = subprocess.run(
        ["git", *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if p.returncode != 0:
        raise ComparisonError("git comparison failed")
    return p.stdout


def _valid_revision(value: str | None) -> bool:
    if not value:
        return False
    return bool(re.fullmatch(r"[0-9a-fA-F]{7,40}", value))


def _diff_revisions(base: str, head: str) -> list[tuple[str, str, str | None]]:
    """Return (status, path, old_path) without exposing file contents."""
    out = _git(["diff", "--name-status", "--find-renames", base, head])
    records: list[tuple[str, str, str | None]] = []
    for raw in out.splitlines():
        fields = raw.split("\t")
        if not fields:
            continue
        status = fields[0]
        code = status[0]
        if code in {"R", "C"}:
            if len(fields) != 3:
                raise ComparisonError("malformed rename comparison")
            records.append((code, fields[2], fields[1]))
        else:
            if len(fields) != 2:
                raise ComparisonError("malformed comparison")
            records.append((code, fields[1], None))
    return records


def _added_lines(base: str, head: str, files: list[str]) -> dict[str, set[int]]:
    if not files:
        return {}
    out = _git(
        [
            "diff",
            "--unified=0",
            "--diff-filter=ACMRT",
            base,
            head,
            "--",
            *files,
        ]
    )
    result: dict[str, set[int]] = {path: set() for path in files}
    current: str | None = None
    new_line: int | None = None
    for line in out.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
            result.setdefault(current, set())
            new_line = None
        elif line.startswith("@@") and current:
            match = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if not match:
                raise ComparisonError("malformed hunk comparison")
            new_line = int(match.group(1))
        elif current and new_line is not None:
            if line.startswith("+") and not line.startswith("+++"):
                result.setdefault(current, set()).add(new_line)
                new_line += 1
            elif not line.startswith("-"):
                new_line += 1
    return result


def _all_public_docs(include_fixtures: bool) -> list[str]:
    result = []
    for path in Path(".").rglob("*"):
        if path.is_file():
            rel = path.as_posix()
            if is_public_doc_path(rel, include_fixtures=include_fixtures):
                result.append(rel)
    return sorted(result)


def _read_lines(path: str) -> list[str]:
    try:
        return Path(path).read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError) as exc:
        raise UnicodeError(path) from exc


def _line_windows(lines: list[str], targets: Iterable[int]) -> Iterable[tuple[list[int], str, list[int]]]:
    """Yield bounded windows of one to three contiguous nonblank source lines."""
    seen: set[tuple[int, int]] = set()
    count = len(lines)
    for target in sorted(set(targets)):
        if target < 1 or target > count:
            continue
        for start in range(max(1, target - 2), target + 1):
            for length in range(1, 4):
                end = start + length - 1
                if end > count or not (start <= target <= end):
                    continue
                key = (start, end)
                if key in seen:
                    continue
                chunk = lines[start - 1 : end]
                if any(not item.strip() for item in chunk):
                    continue
                seen.add(key)
                offsets: list[int] = []
                pieces: list[str] = []
                cursor = 0
                for item in chunk:
                    clean = item.strip()
                    offsets.append(cursor)
                    pieces.append(clean)
                    cursor += len(clean) + 1
                yield list(range(start, end + 1)), " ".join(pieces), offsets


def _match_line(line_numbers: list[int], offsets: list[int], match_start: int) -> int:
    chosen = line_numbers[0]
    for number, offset in zip(line_numbers, offsets):
        if offset <= match_start:
            chosen = number
        else:
            break
    return chosen


def scan_file(path: str, target_lines: Iterable[int]) -> list[tuple[str, int, str, str]]:
    lines = _read_lines(path)
    findings: set[tuple[str, int, str, str]] = set()
    for line_numbers, text, offsets in _line_windows(lines, target_lines):
        for rule_id, category, pattern in RULES:
            for match in pattern.finditer(text):
                line = _match_line(line_numbers, offsets, match.start())
                findings.add((path, line, category, rule_id))
    return sorted(findings)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--include-test-fixtures", action="store_true")
    parser.add_argument("--base")
    parser.add_argument("--head")
    args = parser.parse_args()

    findings: list[tuple[str, int, str, str]] = []
    try:
        include_fixtures = args.include_test_fixtures
        if args.all:
            files = _all_public_docs(include_fixtures)
            line_map = {
                path: range(1, len(_read_lines(path)) + 1)
                for path in files
            }
        else:
            base = args.base or os.environ.get("PUBLIC_DOCS_BASE_SHA")
            head = args.head or os.environ.get("PUBLIC_DOCS_HEAD_SHA")
            if not _valid_revision(head):
                raise ComparisonError("missing or invalid head revision")
            # A zero before-SHA denotes a newly created push ref. Scan all current docs.
            new_ref = bool(base and set(base) == {"0"})
            if not new_ref and not _valid_revision(base):
                raise ComparisonError("missing or invalid base revision")

            if new_ref:
                files = _all_public_docs(include_fixtures=False)
                line_map = {
                    path: range(1, len(_read_lines(path)) + 1)
                    for path in files
                }
            else:
                records = _diff_revisions(base, head)
                precedence_change = False
                changed: list[str] = []
                for status, path, old_path in records:
                    new_is_doc = is_public_doc_path(path)
                    old_is_doc = bool(old_path and is_public_doc_path(old_path))
                    if status == "D" and is_public_doc_path(path):
                        precedence_change = True
                    elif status == "R" and old_is_doc and not new_is_doc:
                        precedence_change = True
                    if status != "D" and new_is_doc:
                        changed.append(path)

                if precedence_change:
                    files = _all_public_docs(include_fixtures=False)
                    line_map = {
                        path: range(1, len(_read_lines(path)) + 1)
                        for path in files
                    }
                else:
                    files = sorted(set(changed))
                    line_map = _added_lines(base, head, files)

        for path in files:
            findings.extend(scan_file(path, line_map.get(path, [])))
    except ComparisonError:
        findings.append(ERROR_FINDINGS["compare"])
    except UnicodeError as exc:
        path = str(exc) if str(exc) else ERROR_FINDINGS["read"][0]
        findings.append((path, 1, "SCANNER_ERROR", "PDS901"))
    except Exception:
        findings.append(ERROR_FINDINGS["internal"])

    findings = sorted(set(findings))
    if findings:
        print("public-docs-safety: FAIL")
        for path, line, category, rule_id in findings:
            print(f"{path}:{line}:{category}:{rule_id}")
        return 1

    print("public-docs-safety: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
