"""Check the authored synthetic corpus against real parsers, without executing fixtures.

Production and the evaluation CLI always use Docker. This local test deliberately
runs the installed analyzers against only this repository's fixed test fixtures.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from evaluation import score
from workflow import normalize_analyzer

CASES = json.loads((Path(__file__).parents[1] / "evals/fixtures.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_authored_static_labels(case, tmp_path):
    file = tmp_path / "app.py"
    file.write_text(case["code"], encoding="utf-8")
    findings = []
    for source, args in [
        ("bandit", ["-f", "json", "-r"]),
        (
            "ruff",
            [
                "check",
                "--isolated",
                "--select",
                "E4,E7,E9,F",
                "--no-cache",
                "--output-format",
                "json",
            ],
        ),
    ]:
        completed = subprocess.run(
            [sys.executable, "-m", source, *args, str(file)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert completed.returncode in (0, 1), completed.stderr
        result = json.loads(completed.stdout)
        rows = result["results"] if source == "bandit" else result
        for row in rows:
            assert Path(row["filename"]).resolve() == file.resolve()
            row["filename"] = "/work/app.py"
        findings.extend(
            normalize_analyzer(
                source, result, [{"filename": "app.py", "raw_content": case["code"]}]
            )
        )
    expected = [{"file": "app.py", **f} for f in case["expected"] if f["source"] != "logic"]
    metrics = score(expected, findings)
    assert metrics["fp"] == 0 and metrics["fn"] == 0
