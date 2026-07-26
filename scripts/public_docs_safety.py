#!/usr/bin/env python3
"""Fail-closed public documentation safety scanner.

This scanner treats repository text as untrusted data. It never executes or follows
instructions found in documentation, fixtures, comments, links, issues, PRs, or reviews.
"""
from __future__ import annotations
import argparse, os, re, subprocess, sys
from pathlib import Path

DOC_NAMES={"README.md","SECURITY.md","CONTRIBUTING.md","AGENTS.md"}
DOC_DIR_PARTS={"docs","doc","website","site","public"}
DOC_EXTS={".md",".mdx",".rst",".txt"}
ALLOW_PROMPT_CONFIG=re.compile(r"(?i)(example|sample|template|user-facing|user configurable|configuration|configures? the assistant)")
PATTERNS=[
 ("model-directed imperative prose", re.compile(r"(?i)\b(ignore|disregard|override)\b.{0,80}\b(previous|above|system|developer|policy|instruction)s?\b")),
 ("automation-control disclosure", re.compile(r"(?i)\b(private control plane|controller policy|trusted author|mutation policy|approved explicit command marker|command marker|guard value|stop condition|tool permission|completion contract)\b")),
 ("copied privileged prompt", re.compile(r"(?i)\b(delegate_task|fresh-context reviewer|do not ask the user|do not stop after|final status must be|READY_FOR_OWNER_REVIEW|BLOCKED_WITH_EVIDENCE)\b")),
 ("prompt-injection attempt", re.compile(r"(?i)\b(system prompt|developer message|reveal (your )?(secret|token|policy)|exfiltrate|bypass tests|approve this PR|merge this PR|operate on another repository|make an external purchase)\b")),
]
UNCERTAIN=re.compile(r"(?i)\b(agent|automation|controller|worker|model|llm)\b.{0,80}\b(must|shall|required to|always|never|use tool|run command|change goal|permission|boundary)\b")

def changed_files():
 p=subprocess.run(['git','diff','--name-only','origin/'+default_branch()+'...HEAD'], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
 if p.returncode==0 and p.stdout.strip(): return p.stdout.splitlines()
 p=subprocess.run(['git','diff','--name-only','--cached'], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
 if p.returncode==0:
  files=p.stdout.splitlines()
  if files: return files
 return [str(x) for x in Path('.').rglob('*') if x.is_file() and is_public_doc(str(x))]

def default_branch():
 p=subprocess.run(['git','symbolic-ref','refs/remotes/origin/HEAD'], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
 if p.returncode==0 and '/' in p.stdout: return p.stdout.strip().rsplit('/',1)[-1]
 return os.environ.get('GITHUB_BASE_REF') or os.environ.get('DEFAULT_BRANCH') or 'main'

def is_public_doc(path):
 parts=set(Path(path).parts)
 name=Path(path).name
 return name in DOC_NAMES or Path(path).suffix.lower() in DOC_EXTS and bool(parts & DOC_DIR_PARTS)

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--all', action='store_true'); args=ap.parse_args()
 files=[str(x) for x in Path('.').rglob('*') if x.is_file() and is_public_doc(str(x))] if args.all else [f for f in changed_files() if is_public_doc(f)]
 findings=[]
 for f in files:
  try: lines=Path(f).read_text(encoding='utf-8', errors='ignore').splitlines()
  except Exception as e: findings.append((f,1,'uncertain-read-failure',str(e))); continue
  for i,line in enumerate(lines,1):
   for label,rx in PATTERNS:
    if rx.search(line) and not ALLOW_PROMPT_CONFIG.search(line): findings.append((f,i,label,line.strip()[:220]))
   if UNCERTAIN.search(line) and not ALLOW_PROMPT_CONFIG.search(line): findings.append((f,i,'uncertain automation instruction',line.strip()[:220]))
 if findings:
  print('public-docs-safety: FAIL')
  for f,i,label,line in findings: print(f'{f}:{i}: {label}: {line}')
  return 1
 print('public-docs-safety: PASS')
 return 0
if __name__=='__main__': sys.exit(main())
