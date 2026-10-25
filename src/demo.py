"""Offline graph demonstration with explicitly simulated GitHub/model/tool responses."""

import argparse
import json
import uuid
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from workflow import build_graph


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("examples/demo-review.json"))
    parser.add_argument("--database", type=Path, default=Path(".review-data/demo.sqlite"))
    args = parser.parse_args()
    args.database.parent.mkdir(parents=True, exist_ok=True)
    review_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": review_id}}
    files = [
        {
            "filename": "app.py",
            "raw_content": "def last(items):\n    return items[len(items)]\n",
            "patch": "@@ -0,0 +1,2 @@\n+def last(items):\n+    return items[len(items)]",
        }
    ]
    finding = {
        "file": "app.py",
        "line": 2,
        "severity": "MEDIUM",
        "source": "logic",
        "rule_id": "LOGIC",
        "explanation": "The index equals the list length and is out of range.",
        "evidence": "    return items[len(items)]",
        "fix": "Use items[-1] and handle empty input.",
    }

    def make_graph(saver):
        return build_graph(
            saver,
            fetcher=lambda _: {
                "files": files,
                "head_sha": "demo-commit",
                "source_repo": "demo/repo",
                "coverage": {"reviewed_files": 1, "skipped": [], "partial": False},
            },
            analyzer=lambda source, _: {"results": [], "errors": []} if source == "bandit" else [],
            logic_reviewer=lambda *_: {"findings": [finding], "usage": {}},
            retriever=lambda _: {"context": []},
        )

    with SqliteSaver.from_conn_string(str(args.database)) as saver:
        paused = make_graph(saver).invoke(
            {
                "review_id": review_id,
                "request": {"repo": "demo/repo", "pr_number": 1, "logic": True},
                "candidates": [],
                "errors": [],
                "metrics": {},
            },
            config,
        )
        print("Paused:", paused["status"])
    # A new connection and graph demonstrate restart recovery. Rejection is safe
    # even if the developer's environment has live GitHub publication enabled.
    with SqliteSaver.from_conn_string(str(args.database)) as saver:
        result = make_graph(saver).invoke(Command(resume={"approved": False}), config)
    artifact = {
        "demo": "SIMULATED integrations; real LangGraph and SQLite checkpoint/resume",
        "before_restart": paused["status"],
        "after_restart": result["status"],
        "summary": result["summary"],
        "findings": result["findings"],
        "coverage": result["coverage"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    print("Resumed:", result["status"], "- artifact:", args.output)


if __name__ == "__main__":
    main()
