#!/usr/bin/env python3
"""
Post-call summary extractor for Gemini Live Discord bridge transcripts.

Reads a JSONL notes file from ~/.hermes/voice-live-notes/ and extracts:
  - clean turn-based transcript
  - task list (heuristic on verbs like "todo", "action", "remember to")
  - decisions (heuristic on "decided", "agreed", "we'll", "going to")
  - questions (sentences ending in '?')
  - follow-ups (sentences containing "follow up", "next time", "later")

Usage:
  python3 post_call_summary.py --file PATH
  python3 post_call_summary.py --file PATH --summary --tasks
  python3 post_call_summary.py --latest   # pick the most recent file
  python3 post_call_summary.py --latest --json > summary.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

DEFAULT_NOTES_DIR = Path.home() / ".hermes" / "voice-live-notes"

TASK_RE = re.compile(
    r"\b(todo|to-do|action item|remember to|need to|must|should|"
    r"i'?ll|i will|we'?ll|we will|let'?s|let's|please|could you|"
    r"can you|don'?t forget|make sure|follow[- ]up)\b",
    re.IGNORECASE,
)
DECISION_RE = re.compile(
    r"\b(decided|agreed|we'?ll|we will|going to|going with|"
    r"settled on|picked|chose|chosen|the plan is|the answer is)\b",
    re.IGNORECASE,
)
QUESTION_RE = re.compile(r"\?\s*$")
FOLLOWUP_RE = re.compile(
    r"\b(follow[- ]up|next time|later|eventually|"
    r"i'?ll send|i'?ll check|let me check|coming soon|tbd|tba)\b",
    re.IGNORECASE,
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def latest_notes_file(notes_dir: Path) -> Path:
    files = sorted(notes_dir.glob("voice-live-*.jsonl"), reverse=True)
    if not files:
        raise SystemExit(f"no notes files in {notes_dir}")
    return files[0]


def build_transcript(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge word-level events into clean turns."""
    turns: list[dict[str, Any]] = []
    current_speaker: str | None = None
    current_words: list[str] = []
    current_t0: float | None = None

    def flush() -> None:
        nonlocal current_speaker, current_words, current_t0
        if current_speaker and current_words:
            turns.append({
                "speaker": current_speaker,
                "t0": current_t0,
                "text": " ".join(current_words).strip(),
            })
        current_speaker = None
        current_words = []
        current_t0 = None

    for ev in events:
        kind = ev.get("type") or ev.get("kind")
        if kind in {"word", "speech", "asr_word"}:
            speaker = ev.get("speaker") or ev.get("role") or "user"
            word = ev.get("word") or ev.get("text") or ""
            if not word:
                continue
            if speaker != current_speaker:
                flush()
                current_speaker = speaker
                current_t0 = ev.get("t0") or ev.get("ts")
            current_words.append(word)
        elif kind in {"turn", "turn_end", "utterance_end"}:
            flush()
        elif kind in {"transcript", "final_transcript"}:
            flush()
            turns.append({
                "speaker": ev.get("speaker", "user"),
                "t0": ev.get("t0"),
                "text": (ev.get("text") or "").strip(),
            })
    flush()
    return turns


def extract_tasks(turns: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for t in turns:
        for sentence in re.split(r"(?<=[.!?])\s+", t["text"]):
            sentence = sentence.strip()
            if not sentence:
                continue
            if TASK_RE.search(sentence):
                out.append(sentence)
    return out


def extract_decisions(turns: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for t in turns:
        for sentence in re.split(r"(?<=[.!?])\s+", t["text"]):
            sentence = sentence.strip()
            if not sentence:
                continue
            if DECISION_RE.search(sentence):
                out.append(sentence)
    return out


def extract_questions(turns: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for t in turns:
        for sentence in re.split(r"(?<=[.!?])\s+", t["text"]):
            sentence = sentence.strip()
            if not sentence:
                continue
            if QUESTION_RE.search(sentence):
                out.append(sentence)
    return out


def extract_followups(turns: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for t in turns:
        for sentence in re.split(r"(?<=[.!?])\s+", t["text"]):
            sentence = sentence.strip()
            if not sentence:
                continue
            if FOLLOWUP_RE.search(sentence):
                out.append(sentence)
    return out


def render_markdown(turns: list[dict[str, Any]], tasks: list[str],
                    decisions: list[str], questions: list[str],
                    followups: list[str], source: Path) -> str:
    out: list[str] = []
    out.append(f"# Voice Call Summary — {source.stem}\n")
    out.append(f"Source: `{source}`\n")
    out.append(f"Turns: {len(turns)}")
    out.append(f"Tasks: {len(tasks)}")
    out.append(f"Decisions: {len(decisions)}")
    out.append(f"Questions: {len(questions)}")
    out.append(f"Follow-ups: {len(followups)}\n")

    if tasks:
        out.append("## Tasks")
        for t in tasks:
            out.append(f"- [ ] {t}")
        out.append("")
    if decisions:
        out.append("## Decisions")
        for d in decisions:
            out.append(f"- {d}")
        out.append("")
    if questions:
        out.append("## Questions")
        for q in questions:
            out.append(f"- \"{q}\"")
        out.append("")
    if followups:
        out.append("## Follow-ups")
        for f in followups:
            out.append(f"- {f}")
        out.append("")
    out.append("## Transcript")
    for t in turns:
        speaker = t["speaker"]
        text = t["text"]
        out.append(f"- **{speaker}**: {text}")
    out.append("")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file", type=Path, help="path to a .jsonl notes file")
    ap.add_argument("--latest", action="store_true", help="use the most recent notes file")
    ap.add_argument("--notes-dir", type=Path, default=DEFAULT_NOTES_DIR)
    ap.add_argument("--summary", action="store_true", help="print summary section")
    ap.add_argument("--tasks", action="store_true", help="print tasks section")
    ap.add_argument("--decisions", action="store_true", help="print decisions section")
    ap.add_argument("--questions", action="store_true", help="print questions section")
    ap.add_argument("--followups", action="store_true", help="print follow-ups section")
    ap.add_argument("--transcript", action="store_true", help="print full transcript")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    args = ap.parse_args()

    if args.latest:
        path = latest_notes_file(args.notes_dir)
    elif args.file:
        path = args.file
    else:
        ap.error("specify --file PATH or --latest")
    if not path.exists():
        ap.error(f"file does not exist: {path}")

    events = load_jsonl(path)
    turns = build_transcript(events)
    tasks = extract_tasks(turns)
    decisions = extract_decisions(turns)
    questions = extract_questions(turns)
    followups = extract_followups(turns)

    if args.json:
        print(json.dumps({
            "source": str(path),
            "turns": turns,
            "tasks": tasks,
            "decisions": decisions,
            "questions": questions,
            "followups": followups,
        }, indent=2, ensure_ascii=False))
        return 0

    any_flag = any([args.summary, args.tasks, args.decisions,
                    args.questions, args.followups, args.transcript])
    if not any_flag:
        args.summary = args.tasks = args.decisions = args.questions = args.followups = args.transcript = True

    md = render_markdown(turns, tasks, decisions, questions, followups, path)
    if args.summary or args.tasks or args.decisions or args.questions or args.followups:
        # When no specific sections are requested, print full doc; otherwise filter
        if any_flag:
            sections = ["# Voice Call Summary"]
            if args.summary:
                sections.append(f"Tasks: {len(tasks)} | Decisions: {len(decisions)} | "
                                f"Questions: {len(questions)} | Follow-ups: {len(followups)}")
            if args.tasks and tasks:
                sections.append("## Tasks\n" + "\n".join(f"- [ ] {t}" for t in tasks))
            if args.decisions and decisions:
                sections.append("## Decisions\n" + "\n".join(f"- {d}" for d in decisions))
            if args.questions and questions:
                sections.append("## Questions\n" + "\n".join(f"- \"{q}\"" for q in questions))
            if args.followups and followups:
                sections.append("## Follow-ups\n" + "\n".join(f"- {f}" for f in followups))
            if args.transcript:
                sections.append("## Transcript\n" + "\n".join(
                    f"- **{t['speaker']}**: {t['text']}" for t in turns
                ))
            print("\n\n".join(s for s in sections if s))
            return 0
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
