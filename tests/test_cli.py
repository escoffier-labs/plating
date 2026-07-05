import json

from plating.cli import main


def _write_spec(path, output):
    path.write_text(json.dumps({
        "title": "demo",
        "steps": [
            {
                "command": "printf 'hello\\n'",
                "output": output,
            }
        ],
    }))


def test_verify_passes_when_recorded_output_matches_live_output(tmp_path, capsys):
    spec = tmp_path / "demo.json"
    _write_spec(spec, "hello\n")

    assert main(["verify", str(spec)]) == 0

    captured = capsys.readouterr()
    assert "verify clean" in captured.out


def test_verify_fails_when_recorded_output_drifts(tmp_path, capsys):
    spec = tmp_path / "demo.json"
    _write_spec(spec, "stale\n")

    assert main(["verify", str(spec)]) == 1

    captured = capsys.readouterr()
    assert "demo drift found" in captured.err
    assert "-stale" in captured.err
    assert "+hello" in captured.err


def test_verify_applies_content_guard_policy(tmp_path, capsys):
    spec = tmp_path / "demo.json"
    spec.write_text(json.dumps({
        "title": "demo",
        "steps": [
            {
                "command": "printf 'internal-host\\n'",
                "output": "internal-host\n",
            }
        ],
    }))
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"known_hosts": ["internal-host"]}))

    assert main(["verify", str(spec), "--scan-policy", str(policy)]) == 2

    captured = capsys.readouterr()
    assert "content-guard:known-host" in captured.err
