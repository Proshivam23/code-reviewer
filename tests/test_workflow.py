import threading

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from core.config import settings
from workflow import build_graph, changed_lines, validate_findings

FILES = [
    {
        "filename": "app.py",
        "raw_content": "value = items[0]\n",
        "patch": "@@ -0,0 +1 @@\n+value = items[0]",
    }
]
FINDING = {
    "file": "app.py",
    "line": 1,
    "severity": "MEDIUM",
    "source": "logic",
    "rule_id": "LOGIC",
    "explanation": "Empty input raises IndexError",
    "evidence": "value = items[0]",
    "fix": "Check items[0] is accessible",
}


def fetch(_):
    return {
        "files": FILES,
        "head_sha": "abc",
        "source_repo": "owner/repo",
        "coverage": {"partial": False, "reviewed_files": 1, "skipped": []},
    }


def empty_analyzer(source, path):
    assert (path / "app.py").read_text() == FILES[0]["raw_content"]
    return {"results": [], "errors": []} if source == "bandit" else []


def graph(saver=None, **kwargs):
    return build_graph(
        saver or InMemorySaver(),
        fetcher=kwargs.pop("fetcher", fetch),
        analyzer=kwargs.pop("analyzer", empty_analyzer),
        logic_reviewer=kwargs.pop("logic_reviewer", lambda *_: {"findings": [FINDING]}),
        retriever=lambda _: {"context": []},
        **kwargs,
    )


def initial():
    return {
        "review_id": "demo",
        "request": {"repo": "owner/repo", "pr_number": 1, "logic": True},
        "candidates": [],
        "errors": [],
        "metrics": {},
    }


CONFIG = {"configurable": {"thread_id": "demo"}}


def test_fanout_is_parallel_and_joins_once():
    barrier = threading.Barrier(3, timeout=5)

    def analyzer(source, path):
        barrier.wait()
        return empty_analyzer(source, path)

    def logic(*_):
        barrier.wait()
        return {"findings": [FINDING]}

    result = graph(analyzer=analyzer, logic_reviewer=logic).invoke(initial(), CONFIG)
    assert result["status"] == "awaiting_approval"
    assert len(result["findings"]) == 1
    assert result["findings"][0]["in_changed_lines"]
    assert not result["errors"]
    assert set(result["metrics"]) == {"bandit", "ruff", "logic"}


def test_sqlite_resume_after_restart_does_not_rerun_analyzers(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "enable_github_posting", False)
    database = str(tmp_path / "checkpoints.sqlite")
    with SqliteSaver.from_conn_string(database) as saver:
        graph(saver).invoke(initial(), CONFIG)
    with SqliteSaver.from_conn_string(database) as saver:

        def forbidden(*_):
            raise AssertionError("Completed analyzer was rerun")

        resumed = graph(saver, analyzer=forbidden).invoke(
            Command(resume={"approved": True}), CONFIG
        )
        assert resumed["status"] == "approved"
        assert resumed["files"] == FILES
        assert len(resumed["findings"]) == 1
        assert not resumed["errors"]


def test_failure_is_partial_not_clean():
    def broken(*_):
        raise RuntimeError("Docker unavailable")

    result = graph(analyzer=broken).invoke(initial(), CONFIG)
    assert result["coverage"]["partial"]
    assert len(result["errors"]) == 2
    assert result["summary"].startswith("Partial review")


def test_no_files_skips_review_and_approval():
    result = graph(fetcher=lambda _: {**fetch({}), "files": []}).invoke(initial(), CONFIG)
    assert result["status"] == "completed"
    assert "__interrupt__" not in result
    assert result["metrics"] == {}


def test_rejection_never_publishes(monkeypatch):
    monkeypatch.setattr(settings, "enable_github_posting", True)

    def forbidden(_):
        raise AssertionError("Rejected review published")

    workflow = graph(publisher=forbidden)
    workflow.invoke(initial(), CONFIG)
    result = workflow.invoke(Command(resume={"approved": False}), CONFIG)
    assert result["status"] == "rejected"


def test_failed_publication_can_resume_without_second_approval(monkeypatch):
    monkeypatch.setattr(settings, "enable_github_posting", True)
    calls = []

    def publish(_):
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("connection dropped")
        return "https://example.com/comment"

    workflow = graph(publisher=publish)
    workflow.invoke(initial(), CONFIG)
    import pytest

    with pytest.raises(RuntimeError):
        workflow.invoke(Command(resume={"approved": True}), CONFIG)
    result = workflow.invoke(None, CONFIG)
    assert result["status"] == "published"
    assert len(result["findings"]) == 1


def test_evidence_validation_dedup_and_brackets():
    candidates = [
        FINDING,
        FINDING,
        {**FINDING, "line": 99},
        {**FINDING, "file": "../secret.py"},
        {**FINDING, "evidence": "invented"},
        {**FINDING, "context_refs": ["invented.py"]},
    ]
    findings, errors = validate_findings(candidates, FILES, [])
    assert len(findings) == 1
    assert "items[0]" in findings[0]["fix"]
    assert len(errors) == 4


def test_diff_line_mapping():
    assert changed_lines("@@ -1,2 +1,3 @@\n same\n-old\n+new\n+extra") == {2, 3}
