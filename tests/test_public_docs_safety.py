import subprocess, sys
from pathlib import Path

def test_public_docs_safety_flags_adversarial_fixture():
    script = Path('scripts/public_docs_safety.py')
    assert script.exists()
    p = subprocess.run([sys.executable, str(script), '--all'], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert p.returncode != 0
    out = p.stdout.lower()
    assert 'tests/fixtures/public-docs/false-privileged-instructions.md' in out
    assert 'prompt-injection' in out or 'copied privileged' in out or 'automation-control' in out
