from test_github_client import Client

import retrieval


def test_retrieves_related_tests_and_conventions_at_head_sha(monkeypatch):
    monkeypatch.setattr(retrieval, "session", Client)
    monkeypatch.setattr(
        retrieval,
        "get",
        lambda *a, **k: {
            "tree": [
                {"path": "tests/test_calc.py", "type": "blob"},
                {"path": "CONTRIBUTING.md", "type": "blob"},
                {"path": "calc.py", "type": "blob"},
                {"path": "image.png", "type": "blob"},
            ],
            "truncated": True,
        },
    )
    calls = []

    def content(client, repo, path, sha):
        calls.append((path, sha))
        return (
            "# Project rules"
            if path.endswith(".md")
            else "def test_add():\n    assert add(1, 2) == 3"
        )

    monkeypatch.setattr(retrieval, "fetch_content", content)
    result = retrieval.retrieve_context(
        {
            "files": [{"filename": "calc.py", "raw_content": "def add(a, b): pass"}],
            "head_sha": "abc",
            "source_repo": "a/b",
        }
    )
    assert len(result["context"]) == 2
    assert all(sha == "abc" for _, sha in calls)
    assert result["retrieval_notes"]
    assert all(item["start_line"] == 1 for item in result["context"])
