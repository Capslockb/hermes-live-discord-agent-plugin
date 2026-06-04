#!/usr/bin/env python3
"""
Voice Notes Analyzer
====================
Reads Discord Voice Live .jsonl note files and extracts:
- Clean transcript
- Tasks / action items
- Decisions
- Questions asked
- Follow-up suggestions

Usage:
    python3 voice_notes_analyzer.py --file path/to/notes.jsonl [--summary] [--tasks] [--decisions] [--questions] [--followups] [--markdown]

If no flags are given, outputs everything.
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def load_events(path: Path) -> List[Dict[str, Any]]:
    events = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def compile_transcript(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge word-level events into turn-based transcript entries."""
    transcript: List[Dict[str, Any]] = []
    for event in events:
        direction = str(event.get("direction") or "").strip()
        text = str(event.get("text") or "").strip()
        ts = str(event.get("ts") or "").strip()
        if not direction or not text:
            continue
        if transcript and transcript[-1]["direction"] == direction:
            sep = "" if text in {",", ".", "?", "!", ":", ";"} else " "
            transcript[-1]["text"] = (transcript[-1]["text"] + sep + text).strip()
            transcript[-1]["ts"] = ts or transcript[-1]["ts"]
        else:
            transcript.append({"ts": ts, "direction": direction, "text": text})
    return transcript


def _extract_tasks(transcript: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Heuristic extraction of action items from user transcript turns only."""
    # Strong action verbs / request patterns
    strong_signals = [
        "need to", "needs to", "should", "must", "have to",
        "remind me", "set up", "configure", "install", "build",
        "research", "check", "verify", "test", "deploy", "fix",
        "update", "write", "create", "make", "add", "remove",
        "delete", "enable", "disable", "schedule", "plan",
        "organize", "extract", "analyze", "review",
    ]
    # Weak signals only count if combined with an explicit request marker
    weak_signals = ["can you", "could you", "please", "do", "help me"]
    request_markers = ["can you", "could you", "please", "would you", "will you"]
    # Exclude purely informational queries
    info_query_starts = [
        "what", "what's", "whats", "how", "when", "where", "who",
        "why", "is there", "are there", "tell me", "give me",
    ]
    owner_patterns = [
        (r"\b(I|me|my)\b", "User"),
        (r"\b(you|your)\b", "Assistant"),
        (r"\b(we|us|our)\b", "Shared"),
        (r"\b(assistant|hermes|sora)\b", "Assistant"),
        (r"\b(B|caps|capslockb)\b", "User"),
    ]
    tasks = []
    for turn in transcript:
        if turn["direction"] != "input":
            continue
        text_lower = turn["text"].lower()

        # Skip pure info queries unless they also contain strong action words
        is_info_query = any(text_lower.startswith(s) for s in info_query_starts)
        has_strong = any(re.search(r'\b' + re.escape(s) + r'\b', text_lower) for s in strong_signals)
        has_weak = any(re.search(r'\b' + re.escape(s) + r'\b', text_lower) for s in weak_signals)
        has_request = any(re.search(r'\b' + re.escape(s) + r'\b', text_lower) for s in request_markers)

        if is_info_query and not has_strong:
            continue
        if not has_strong and not (has_weak and has_request):
            continue

        owner = None
        for pat, label in owner_patterns:
            m = re.search(pat, text_lower)
            if m:
                owner = label
                break
        tasks.append({
            "text": turn["text"],
            "direction": turn["direction"],
            "ts": turn["ts"],
            "owner": owner,
        })
    return tasks


def _extract_decisions(transcript: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    decision_markers = [
        "decided", "decision", "agreed", "agreement", "settled",
        "we will", "we'll", "i will", "i'll", "going with",
        "choose", "chose", "chosen", "opt for", "opted for",
        "confirmed", "approved", "green light", "go ahead",
        "let's use", "lets use", "using", "switch to", "migrated to",
    ]
    decisions = []
    for turn in transcript:
        text_lower = turn["text"].lower()
        if any(m in text_lower for m in decision_markers):
            decisions.append({
                "text": turn["text"],
                "direction": turn["direction"],
                "ts": turn["ts"],
            })
    return decisions


def _extract_questions(transcript: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract sentences ending in ? from output turns."""
    questions = []
    for turn in transcript:
        if turn["direction"] != "output":
            continue
        text = turn["text"]
        # Split by sentence terminators, keep those with ?
        for sentence in re.split(r'(?<=[.?!])\s+', text):
            sentence = sentence.strip()
            if sentence.endswith("?"):
                questions.append({
                    "text": sentence,
                    "ts": turn["ts"],
                })
    return questions


def _extract_followups(transcript: List[Dict[str, Any]], tasks: List[Dict], decisions: List[Dict], questions: List[Dict]) -> List[str]:
    """Generate follow-up suggestions based on extracted content."""
    followups = []
    if tasks:
        followups.append("Review extracted tasks and assign owners/deadlines.")
    if decisions:
        followups.append("Document decisions in knowledge base or project notes.")
    if questions:
        followups.append("Answer unresolved questions from the call.")
    followups.append("Check voice bridge health and confirm no issues.")
    # Heuristic: if user said "research" or "check", suggest follow-up
    for turn in transcript:
        t = turn["text"].lower()
        if "research" in t:
            followups.append("Schedule time for research items discussed.")
            break
    return followups


def _format_timestamp(ts_str: str) -> str:
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return ts_str


def _render_markdown(
    transcript: List[Dict],
    tasks: List[Dict],
    decisions: List[Dict],
    questions: List[Dict],
    followups: List[str],
    source_path: Path,
) -> str:
    lines = []
    # Header
    first_ts = transcript[0]["ts"] if transcript else ""
    header_ts = _format_timestamp(first_ts) if first_ts else "Unknown time"
    lines.append(f"# Voice Call Summary — {header_ts}")
    lines.append(f"**Source:** `{source_path}`")
    lines.append(f"**Turns:** {len(transcript)}")
    lines.append("")

    # Tasks
    if tasks:
        lines.append("## Tasks")
        for t in tasks:
            owner = f" *(inferred: {t['owner']})*" if t.get("owner") else ""
            lines.append(f"- [ ] {t['text']}{owner}")
        lines.append("")

    # Decisions
    if decisions:
        lines.append("## Decisions")
        for d in decisions:
            lines.append(f"- {d['text']}")
        lines.append("")

    # Questions
    if questions:
        lines.append("## Questions")
        for q in questions:
            lines.append(f'- "{q["text"]}"')
        lines.append("")

    # Follow-ups
    if followups:
        lines.append("## Follow-ups")
        for fu in followups:
            lines.append(f"- {fu}")
        lines.append("")

    # Full transcript (collapsed feel — just the turns)
    lines.append("## Transcript")
    for turn in transcript:
        speaker = "**User**" if turn["direction"] == "input" else "**Assistant**"
        lines.append(f"[{turn['ts']}] {speaker}: {turn['text']}")
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze Discord Voice Live .jsonl notes")
    parser.add_argument("--file", required=True, type=Path, help="Path to .jsonl notes file")
    parser.add_argument("--summary", action="store_true", help="Output summary section")
    parser.add_argument("--tasks", action="store_true", help="Output tasks section")
    parser.add_argument("--decisions", action="store_true", help="Output decisions section")
    parser.add_argument("--questions", action="store_true", help="Output questions section")
    parser.add_argument("--followups", action="store_true", help="Output follow-ups section")
    parser.add_argument("--transcript", action="store_true", help="Output full transcript")
    parser.add_argument("--markdown", action="store_true", help="Output as Markdown")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--since", type=str, help="Only process events after this ISO timestamp")
    parser.add_argument("--until", type=str, help="Only process events before this ISO timestamp")
    args = parser.parse_args()

    if not args.file.exists():
        print(f"ERROR: File not found: {args.file}", file=sys.stderr)
        return 1

    events = load_events(args.file)

    # Time filtering
    if args.since or args.until:
        filtered = []
        for ev in events:
            ts = ev.get("ts", "")
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if args.since:
                    since_dt = datetime.fromisoformat(args.since.replace("Z", "+00:00"))
                    if dt < since_dt:
                        continue
                if args.until:
                    until_dt = datetime.fromisoformat(args.until.replace("Z", "+00:00"))
                    if dt > until_dt:
                        continue
                filtered.append(ev)
            except Exception:
                filtered.append(ev)
        events = filtered

    transcript = compile_transcript(events)
    tasks = _extract_tasks(transcript)
    decisions = _extract_decisions(transcript)
    questions = _extract_questions(transcript)
    followups = _extract_followups(transcript, tasks, decisions, questions)

    # Default: show everything if no section flags
    all_flags = [args.summary, args.tasks, args.decisions, args.questions, args.followups, args.transcript]
    if not any(all_flags):
        args.summary = args.tasks = args.decisions = args.questions = args.followups = args.transcript = True

    if args.json:
        out = {
            "meta": {
                "source": str(args.file),
                "total_events": len(events),
                "transcript_turns": len(transcript),
            },
            "transcript": transcript,
            "tasks": tasks,
            "decisions": decisions,
            "questions": questions,
            "followups": followups,
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    if args.markdown:
        print(_render_markdown(transcript, tasks, decisions, questions, followups, args.file))
        return 0

    # Plain text sectioned output
    if args.summary:
        print(f"=== SUMMARY ({len(transcript)} turns, {len(events)} events) ===")
        print(f"Source: {args.file}")
        print()

    if args.transcript:
        print("=== TRANSCRIPT ===")
        for turn in transcript:
            speaker = "USER" if turn["direction"] == "input" else "ASSISTANT"
            print(f"[{turn['ts']}] {speaker}: {turn['text']}")
        print()

    if args.tasks:
        print("=== TASKS ===")
        for t in tasks:
            owner = f" (owner: {t['owner']})" if t.get("owner") else ""
            print(f"- {t['text']}{owner}")
        if not tasks:
            print("(none detected)")
        print()

    if args.decisions:
        print("=== DECISIONS ===")
        for d in decisions:
            print(f"- {d['text']}")
        if not decisions:
            print("(none detected)")
        print()

    if args.questions:
        print("=== QUESTIONS ===")
        for q in questions:
            print(f'- "{q["text"]}"')
        if not questions:
            print("(none detected)")
        print()

    if args.followups:
        print("=== FOLLOW-UPS ===")
        for fu in followups:
            print(f"- {fu}")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
