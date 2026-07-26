import subprocess
import sys
import tomllib
from pathlib import Path

import plating


def _pyproject_version() -> str:
    root = Path(__file__).resolve().parents[1]
    with (root / "pyproject.toml").open("rb") as fh:
        data = tomllib.load(fh)
    return data["project"]["version"]


def test_module_version_matches_pyproject():
    assert plating.__version__ == _pyproject_version()


def test_cli_version_matches_pyproject():
    script_name = "plating.exe" if sys.platform == "win32" else "plating"
    cli = Path(sys.executable).with_name(script_name)
    assert cli.is_file(), "test environment must install the plating console script"
    result = subprocess.run(
        [cli, "--version"],
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == f"plating {_pyproject_version()}"
