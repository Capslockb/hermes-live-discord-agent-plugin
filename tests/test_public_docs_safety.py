import subprocess, sys
from pathlib import Path

def test_public_docs_safety_flags_adversarial_fixture():
    script = Path('scripts/public_docs_safety.py')
    fixture = Path('tests/fixtures/public-docs/false-privileged-instructions.md')
    assert script.exists()
    assert fixture.exists()
    p = subprocess.run(
        [sys.executable, str(script), '--all', '--include-test-fixtures'],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert p.returncode != 0, p.stdout
    out = p.stdout.lower()
    assert str(fixture).lower() in out
    assert (
        'model-directed override' in out
        or 'secret-or-policy exfiltration' in out
        or 'unauthorized action request' in out
        or 'non-public automation disclosure' in out
    )
