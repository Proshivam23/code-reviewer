import base64

import pytest

import github_client as github
from core.config import settings


class Client:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


def test_fetch_reports_skips_and_pins_fork_content(monkeypatch):
    calls = []
    monkeypatch.setattr(github, "session", Client)
    monkeypatch.setattr(settings, "max_files", 1)

    def get(client, path, **params):
        calls.append((path, params))
        if path.endswith("/pulls/1"):
            return {"head": {"sha": "abc", "repo": {"full_name": "fork/repo"}}, "changed_files": 5}
        if path.endswith("/files"):
            return [
                {"filename": "old.py", "status": "removed", "patch": "@@"},
                {"filename": "missing.py", "patch": None},
                {"filename": "a.py", "patch": "@@"},
                {"filename": "b.py", "patch": "@@"},
            ]
        assert path == "/repos/fork/repo/contents/a.py"
        assert params["ref"] == "abc"
        return {
            "type": "file",
            "encoding": "base64",
            "size": 4,
            "content": base64.b64encode(b"x=1\n").decode(),
        }

    monkeypatch.setattr(github, "get", get)
    result = github.fetch_pr({"repo": "owner/repo", "pr_number": 1})
    assert len(result["files"]) == 1
    assert result["coverage"]["partial"]
    assert [r["reason"] for r in result["coverage"]["skipped"]] == [
        "deleted",
        "missing_patch",
        "file_limit",
    ]


def test_publication_reconciles_existing_comment_without_post(monkeypatch):
    monkeypatch.setattr(settings, "enable_github_posting", True)
    from pydantic import SecretStr

    monkeypatch.setattr(settings, "github_token", SecretStr("fake"))
    monkeypatch.setattr(github, "session", Client)

    def get(client, path, **params):
        if path == "/user":
            return {"id": 42}
        return [
            {
                "body": "<!-- code-reviewer:demo -->",
                "user": {"id": 42},
                "html_url": "https://example.com/comment",
            }
        ]

    monkeypatch.setattr(github, "get", get)
    assert (
        github.publish_comment({"request": {"repo": "a/b", "pr_number": 1}, "review_id": "demo"})
        == "https://example.com/comment"
    )


def test_stale_head_prevents_post(monkeypatch):
    monkeypatch.setattr(settings, "enable_github_posting", True)
    from pydantic import SecretStr

    monkeypatch.setattr(settings, "github_token", SecretStr("fake"))
    monkeypatch.setattr(github, "session", Client)
    monkeypatch.setattr(
        github,
        "get",
        lambda c, path, **p: (
            {"id": 42}
            if path == "/user"
            else []
            if path.endswith("comments")
            else {"head": {"sha": "new"}}
        ),
    )
    with pytest.raises(RuntimeError, match="head changed"):
        github.publish_comment(
            {"request": {"repo": "a/b", "pr_number": 1}, "review_id": "demo", "head_sha": "old"}
        )
