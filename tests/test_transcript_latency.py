"""End-to-end transcript-mining test for voice-bridge interrupt latency.

Scans the most recent voice-live notes JSONL files in
~/.hermes/voice-live-notes/ and reports the historical distribution of
"interrupt latency" — the gap between the last model output event and
the first user input event in an interruption burst.

Run with:
    ~/.hermes/hermes-agent/venv/bin/python -m unittest tests.test_transcript_latency -v

Or directly:
    ~/.hermes/hermes-agent/venv/bin/python tests/test_transcript_latency.py

This test is post-hoc and READ-ONLY. It does not modify any files.
"""

import json
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path


NOTES_DIR = Path.home() / ".hermes" / "voice-live-notes"


def _analyze_files(n_files: int = 5):
    """Return a list of (gap_ms, burst_len, last_output_text, filename) tuples.

    The list is empty if NOTES_DIR is missing or no interruptions are found.
    """
    if not NOTES_DIR.exists():
        return []
    files = sorted(
        NOTES_DIR.glob("voice-live-*.jsonl"),
        key=os.path.getmtime,
        reverse=True,
    )[:n_files]
    gaps = []
    for fp in files:
        try:
            events = [json.loads(line) for line in open(fp) if line.strip()]
        except Exception:
            continue
        burst_len = 0
        last_out_ts = None
        last_out_text = ""
        for e in events:
            direction = e.get("direction")
            ts = e.get("ts")
            if not ts:
                continue
            if direction == "output":
                burst_len += 1
                last_out_ts = ts
                last_out_text = e.get("text", "")
            elif direction == "input" and last_out_ts is not None:
                try:
                    a = datetime.fromisoformat(last_out_ts.replace("Z", "+00:00"))
                    b = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    gap_ms = (b - a).total_seconds() * 1000.0
                    gaps.append((gap_ms, burst_len, last_out_text[:50], fp.name))
                except Exception:
                    pass
                burst_len = 0
                last_out_ts = None
    return gaps


def _percentile(values, p):
    if not values:
        return 0.0
    sorted_v = sorted(values)
    idx = min(int(len(sorted_v) * p), len(sorted_v) - 1)
    return sorted_v[idx]


def _print_distribution(gaps):
    if not gaps:
        print("  No interruptions found.")
        return
    gap_values = [g[0] for g in gaps]
    n = len(gap_values)
    print(f"  Total interruptions: {n}")
    print(f"  Median gap:     {_percentile(gap_values, 0.50):7.0f} ms")
    print(f"  p75 gap:        {_percentile(gap_values, 0.75):7.0f} ms")
    print(f"  p95 gap:        {_percentile(gap_values, 0.95):7.0f} ms")
    print(f"  Max gap:        {max(gap_values):7.0f} ms")
    print(f"  Min gap:        {min(gap_values):7.0f} ms")
    print()
    print("  5 fastest (best-case latency):")
    for g, b, t, fn in sorted(gaps)[:5]:
        print(f"    {g:7.0f} ms  burst={b:3d} outs  {fn}: \"{t}\"")
    print("  5 slowest (worst-case latency):")
    for g, b, t, fn in sorted(gaps)[-5:]:
        print(f"    {g:7.0f} ms  burst={b:3d} outs  {fn}: \"{t}\"")


class TestTranscriptLatency(unittest.TestCase):
    def test_distribution_report_runs(self):
        """Mine the last 5 voice-live notes files and print the gap distribution.

        This is an observational test — it does not enforce a numeric
        threshold, because historical data was captured before the
        local-interrupt fix landed. Use the output to compare pre-fix vs
        post-fix latency after restarting the bridge with the new code.
        """
        print()
        print(f"  Notes dir: {NOTES_DIR}")
        gaps = _analyze_files(n_files=5)
        _print_distribution(gaps)
        # Sanity: the parser must work (gaps are floats, not garbage).
        if gaps:
            for g, _b, _t, _fn in gaps:
                self.assertIsInstance(g, float)
                self.assertGreaterEqual(g, 0.0)
                # Sanity ceiling: a gap larger than 60s is almost certainly
                # a parse bug, not a real latency event.
                self.assertLess(g, 60_000.0, "gap exceeds 60s sanity ceiling")


if __name__ == "__main__":
    if NOTES_DIR.exists():
        files = sorted(
            NOTES_DIR.glob("voice-live-*.jsonl"),
            key=os.path.getmtime,
            reverse=True,
        )[:5]
        print()
        print(f"Notes dir: {NOTES_DIR}")
        print(f"Files scanned: {len(files)}")
        for f in files:
            print(f"  - {f.name}  ({os.path.getsize(f)} bytes)")
        print()
        gaps = _analyze_files(n_files=5)
        _print_distribution(gaps)
    else:
        print(f"Notes dir does not exist: {NOTES_DIR}")
        sys.exit(1)
