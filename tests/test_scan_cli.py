import subprocess
import sys
from pathlib import Path


def test_cli_scan_flags_secret_assignment_and_redacts(tmp_path: Path):
    target = tmp_path / "leak.txt"
    target.write_text("API_TOKEN=fake-example-value\n")
    result = subprocess.run(
        [sys.executable, "-m", "plating.cli", "scan", str(target)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2, result.stderr
    assert "secret-assignment" in result.stdout
    assert "API_TOKEN=<redacted>" in result.stdout
    assert "fake-example-value" not in result.stdout
    assert "fake-example-value" not in result.stderr
