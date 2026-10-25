"""Evaluate seeded PR fixtures without executing their Python code."""

import argparse
import json
import tempfile
import time
from pathlib import Path

from agents import review_logic
from paths import write_sources
from tools import run_analyzer
from workflow import normalize_analyzer, validate_findings


def score(expected: list[dict], predicted: list[dict]) -> dict:
    def key(item):
        return item["file"], item["line"], item["source"], item["rule_id"]

    truth, actual = {key(x) for x in expected}, {key(x) for x in predicted}
    tp, fp, fn = len(truth & actual), len(actual - truth), len(truth - actual)
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
    }


def evaluate(cases: list[dict], include_logic=False) -> dict:
    rows = []
    totals = {"baseline": {"tp": 0, "fp": 0, "fn": 0}, "combined": {"tp": 0, "fp": 0, "fn": 0}}
    for case in cases:
        started = time.monotonic()
        content = case["code"]
        files = [
            {
                "filename": "app.py",
                "raw_content": content,
                "patch": f"@@ -0,0 +1,{len(content.splitlines())} @@\n"
                + "\n".join("+" + line for line in content.splitlines()),
            }
        ]
        candidates, errors, usage = [], [], {}
        with tempfile.TemporaryDirectory(prefix="review-eval-") as directory:
            mount = Path(directory) / "source"
            write_sources(mount, files)
            for source in ("bandit", "ruff"):
                try:
                    candidates.extend(
                        normalize_analyzer(source, run_analyzer(source, mount), files)
                    )
                except Exception as exc:
                    errors.append(f"{source}: {type(exc).__name__}")
        baseline, invalid = validate_findings(candidates, files, [])
        errors.extend(invalid)
        combined = baseline
        if include_logic:
            try:
                result = review_logic(files, [])
                combined, invalid = validate_findings(candidates + result["findings"], files, [])
                errors.extend(invalid)
                usage = result["usage"]
            except Exception as exc:
                errors.append(f"logic: {type(exc).__name__}")
        expected = [{"file": "app.py", **f} for f in case["expected"]]
        metrics = {"baseline": score(expected, baseline)}
        if include_logic:
            metrics["combined"] = score(expected, combined)
        for mode, values in metrics.items():
            for name in ("tp", "fp", "fn"):
                totals[mode][name] += values[name]
        rows.append(
            {
                "id": case["id"],
                "category": case["category"],
                "scores": metrics,
                "seconds": time.monotonic() - started,
                "usage": usage,
                "errors": errors,
                "findings": combined,
            }
        )
    aggregate = {}
    for mode in ["baseline", "combined"] if include_logic else ["baseline"]:
        counts = totals[mode]
        tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
        aggregate[mode] = {
            **counts,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
        }
    return {
        "fixture_count": len(cases),
        "valid": not any(r["errors"] for r in rows),
        "aggregate": aggregate,
        "cases": rows,
        "method": "Exact file/line/source/rule matching; synthetic fixtures, not a production benchmark.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=Path("evals/fixtures.json"))
    parser.add_argument("--output", type=Path, default=Path("evaluation-results/baseline.json"))
    parser.add_argument("--with-logic", action="store_true", help="Makes paid Gemini model calls")
    args = parser.parse_args()
    result = evaluate(json.loads(args.fixtures.read_text(encoding="utf-8")), args.with_logic)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"valid": result["valid"], "aggregate": result["aggregate"]}, indent=2))
    if not result["valid"]:
        raise SystemExit("Evaluation incomplete: inspect errors in output")


if __name__ == "__main__":
    main()
