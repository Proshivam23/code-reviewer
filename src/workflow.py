"""Explicit fan-out/fan-in graph with durable, human-approved publication."""

import operator
import re
import tempfile
import time
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from agents import review_logic
from core.config import settings
from github_client import fetch_pr, publish_comment
from paths import safe_relative_path, write_sources
from retrieval import retrieve_context
from schemas import Finding
from tools import run_analyzer


def merge(left: dict, right: dict) -> dict:
    return {**left, **right}


class ReviewState(TypedDict, total=False):
    review_id: str
    request: dict
    head_sha: str
    source_repo: str
    files: list[dict]
    coverage: dict
    context: list[dict]
    retrieval_notes: list[str]
    retrieval_candidates_scanned: int
    candidates: Annotated[list[dict], operator.add]
    errors: Annotated[list[str], operator.add]
    metrics: Annotated[dict, merge]
    findings: list[dict]
    summary: str
    status: str
    approved: bool
    comment_url: str


def changed_lines(patch: str) -> set[int]:
    changed = set()
    line = None
    for row in patch.splitlines():
        match = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", row)
        if match:
            line = int(match.group(1))
        elif line is not None:
            if row.startswith("+"):
                changed.add(line)
                line += 1
            elif row.startswith(" "):
                line += 1
    return changed


def normalize_analyzer(source: str, output: dict | list, files: list[dict]) -> list[dict]:
    sources = {f["filename"]: f["raw_content"].splitlines() for f in files}
    findings = []
    rows = output["results"] if source == "bandit" else output
    for row in rows:
        name = row["filename"].removeprefix("/work/")
        line = row["line_number"] if source == "bandit" else row["location"]["row"]
        if name not in sources or not 1 <= line <= len(sources[name]):
            raise ValueError("Analyzer returned an invalid source location")
        rule = row["test_id"] if source == "bandit" else (row.get("code") or "SYNTAX")
        message = row["issue_text"] if source == "bandit" else row["message"]
        fixes = {
            "B307": "Replace eval with an explicit parser for the expected input.",
            "B602": "Pass an argument list and disable shell=True.",
            "B105": "Load the credential from a secret provider or environment.",
            "B506": "Use yaml.safe_load for untrusted YAML.",
            "F401": "Remove the unused import.",
            "F821": "Define or import this name before use.",
            "F841": "Remove the unused assignment or use its value.",
        }
        findings.append(
            Finding(
                file=name,
                line=line,
                severity=row["issue_severity"] if source == "bandit" else "LOW",
                source=source,
                rule_id=rule,
                explanation=message,
                evidence=sources[name][line - 1],
                fix=fixes.get(rule, f"Resolve {rule}: {message}"),
            ).model_dump()
        )
    return findings


def validate_findings(candidates: list[dict], files: list[dict], context: list[dict]):
    sources = {f["filename"]: f for f in files}
    allowed_refs = {c["file"] for c in context}
    result, errors, seen = [], [], set()
    for raw in candidates:
        try:
            finding = Finding.model_validate(raw)
            safe_relative_path(finding.file)
            source = sources[finding.file]
            lines = source["raw_content"].splitlines()
            if finding.line > len(lines):
                raise ValueError("Invalid line")
            if finding.evidence.strip() != lines[finding.line - 1].strip():
                raise ValueError("Evidence is not the cited source line")
            if not set(finding.context_refs) <= allowed_refs:
                raise ValueError("Unknown context citation")
            finding.in_changed_lines = finding.line in changed_lines(source["patch"])
            key = (finding.file, finding.line, finding.source, finding.rule_id)
            if key not in seen:
                seen.add(key)
                result.append(finding.model_dump())
        except (ValueError, KeyError, TypeError):
            errors.append("Rejected a finding with invalid schema, location, evidence, or citation")
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    result.sort(key=lambda f: (order[f["severity"]], f["file"], f["line"], f["rule_id"]))
    return result, errors


def build_graph(
    checkpointer=None,
    *,
    fetcher=fetch_pr,
    analyzer=run_analyzer,
    logic_reviewer=review_logic,
    retriever=retrieve_context,
    publisher=publish_comment,
):
    def fetch(state):
        return {**fetcher(state["request"]), "status": "reviewing"}

    def analyze(source):
        def node(state):
            started = time.monotonic()
            try:
                # Rehydrate a private mount from checkpointed source, never from an LLM path.
                with tempfile.TemporaryDirectory(prefix="code-review-") as directory:
                    mount = Path(directory) / "source"
                    write_sources(mount, state["files"])
                    output = analyzer(source, mount)
                findings = normalize_analyzer(source, output, state["files"])
                return {
                    "candidates": findings,
                    "metrics": {source: {"seconds": time.monotonic() - started, "status": "ok"}},
                }
            except Exception as exc:
                return {
                    "errors": [f"{source} analysis failed ({type(exc).__name__})"],
                    "metrics": {
                        source: {"seconds": time.monotonic() - started, "status": "failed"}
                    },
                }

        return node

    def logic(state):
        started = time.monotonic()
        if not state["request"].get("logic", True):
            return {"metrics": {"logic": {"status": "disabled", "seconds": 0}}}
        context, notes = [], []
        scanned = 0
        try:
            retrieved = retriever(state)
            context = retrieved["context"]
            notes = retrieved.get("retrieval_notes", [])
            scanned = retrieved.get("retrieval_candidates_scanned", 0)
        except Exception as exc:
            notes = [f"Repository context unavailable ({type(exc).__name__}); using changed files"]
        try:
            result = logic_reviewer(state["files"], context)
            # A model may not impersonate a deterministic analyzer.
            if any(f.get("source") != "logic" for f in result["findings"]):
                raise ValueError("Invalid logic finding provenance")
            return {
                "candidates": result["findings"],
                "context": context,
                "retrieval_notes": notes,
                "retrieval_candidates_scanned": scanned,
                "metrics": {
                    "logic": {
                        "status": "ok",
                        "seconds": time.monotonic() - started,
                        "usage": result.get("usage", {}),
                    }
                },
            }
        except Exception as exc:
            return {
                "context": context,
                "retrieval_notes": notes,
                "errors": [f"Logic review failed ({type(exc).__name__})"],
                "metrics": {"logic": {"status": "failed", "seconds": time.monotonic() - started}},
            }

    def aggregate(state):
        findings, errors = validate_findings(
            state.get("candidates", []), state["files"], state.get("context", [])
        )
        coverage = {
            **state["coverage"],
            "logic_enabled": state["request"].get("logic", True),
            "retrieval_notes": state.get("retrieval_notes", []),
            "retrieval_candidates_scanned": state.get("retrieval_candidates_scanned", 0),
        }
        coverage["partial"] = bool(coverage.get("partial") or errors or state.get("errors"))
        prefix = "Partial review" if coverage["partial"] else "Review complete"
        summary = (
            f"{prefix}: {len(findings)} findings across {len(state['files'])} Python files. "
            f"{sum(not f['in_changed_lines'] for f in findings)} findings outside added lines."
        )
        return {
            "findings": findings,
            "errors": errors,
            "coverage": coverage,
            "summary": summary,
            "status": "awaiting_approval" if state["files"] else "completed",
        }

    def approval(state):
        decision = interrupt(
            {
                "review_id": state["review_id"],
                "summary": state["summary"],
                "findings": state["findings"],
                "coverage": state["coverage"],
            }
        )
        if not isinstance(decision, dict) or type(decision.get("approved")) is not bool:
            raise ValueError("Approval must contain a boolean approved field")
        approved = decision["approved"]
        return {"approved": approved, "status": "approved" if approved else "rejected"}

    def publish(state):
        return {"comment_url": publisher(state), "status": "published"}

    graph = StateGraph(ReviewState)
    graph.add_node("fetch", fetch)
    graph.add_node("security", analyze("bandit"))
    graph.add_node("style", analyze("ruff"))
    graph.add_node("logic", logic)
    graph.add_node("aggregate", aggregate)
    graph.add_node("approval", approval)
    graph.add_node("publish", publish)
    graph.add_edge(START, "fetch")
    graph.add_conditional_edges(
        "fetch", lambda s: ["security", "style", "logic"] if s["files"] else "aggregate"
    )
    graph.add_edge(["security", "style", "logic"], "aggregate")
    graph.add_conditional_edges("aggregate", lambda s: "approval" if s["files"] else END)
    graph.add_conditional_edges(
        "approval", lambda s: "publish" if s["approved"] and settings.enable_github_posting else END
    )
    graph.add_edge("publish", END)
    return graph.compile(checkpointer=checkpointer)
