"""Bounded GitHub reads pinned to the PR head; publication is opt-in."""

import base64
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from core.config import settings
from paths import safe_relative_path

API = "https://api.github.com"


def session() -> requests.Session:
    client = requests.Session()
    client.headers.update(
        {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    )
    if settings.github_token:
        client.headers["Authorization"] = "Bearer " + settings.github_token.get_secret_value()
    client.mount(
        "https://",
        HTTPAdapter(
            max_retries=Retry(
                total=2,
                backoff_factor=0.5,
                status_forcelist=[502, 503, 504],
                allowed_methods=["GET"],
                respect_retry_after_header=False,
            )
        ),
    )
    return client


def get(client, path: str, **params):
    response = client.get(f"{API}{path}", params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def fetch_content(client, repo: str, filename: str, sha: str) -> str:
    safe_relative_path(filename)
    data = get(client, f"/repos/{repo}/contents/{quote(filename, safe='/')}", ref=sha)
    if (
        not isinstance(data, dict)
        or data.get("type") != "file"
        or data.get("encoding") != "base64"
        or data.get("size", settings.max_file_bytes + 1) > settings.max_file_bytes
    ):
        raise ValueError("Unsupported or oversized file")
    content = base64.b64decode(data.get("content", "").replace("\n", ""), validate=True)
    if len(content) > settings.max_file_bytes:
        raise ValueError("Oversized file")
    return content.decode("utf-8")


def fetch_pr(request: dict) -> dict:
    repo, number = request["repo"], request["pr_number"]
    with session() as client:
        pull = get(client, f"/repos/{repo}/pulls/{number}")
        sha = pull["head"]["sha"]
        source_repo = (pull["head"].get("repo") or {}).get("full_name") or repo
        results, skipped = [], []
        total_python = 0
        used_chars = 0
        seen = set()
        # GitHub exposes at most 3,000 changed files.
        for page in range(1, 31):
            rows = get(client, f"/repos/{repo}/pulls/{number}/files", per_page=100, page=page)
            if not isinstance(rows, list):
                raise RuntimeError("Invalid GitHub file list")
            for row in rows:
                name = row["filename"]
                if not name.lower().endswith(".py"):
                    continue
                total_python += 1
                reason = None
                if row.get("status") == "removed":
                    reason = "deleted"
                elif not row.get("patch"):
                    reason = "missing_patch"
                elif len(results) >= settings.max_files:
                    reason = "file_limit"
                if reason:
                    skipped.append({"file": name, "reason": reason})
                    continue
                try:
                    safe_relative_path(name)
                    if name.casefold() in seen:
                        raise ValueError("Case-colliding path")
                    content = fetch_content(client, source_repo, name, sha)
                    size = len(content) + len(row["patch"])
                    if used_chars + size > settings.max_prompt_chars // 2:
                        raise ValueError("Review source budget exceeded")
                except (ValueError, UnicodeError) as exc:
                    skipped.append({"file": name, "reason": str(exc)})
                    continue
                seen.add(name.casefold())
                used_chars += size
                results.append({"filename": name, "patch": row["patch"], "raw_content": content})
            if len(rows) < 100:
                break
        # Do not combine a moving PR diff with a different commit's source.
        if get(client, f"/repos/{repo}/pulls/{number}")["head"]["sha"] != sha:
            raise RuntimeError("PR changed during fetch; start a new review")
    limited = pull.get("changed_files", 0) > 3000
    return {
        "files": results,
        "head_sha": sha,
        "source_repo": source_repo,
        "coverage": {
            "changed_files": pull.get("changed_files"),
            "python_files_seen": total_python,
            "reviewed_files": len(results),
            "skipped": skipped,
            "github_file_limit": limited,
            "partial": bool(skipped or limited),
        },
    }


def publish_comment(state: dict) -> str:
    """Reconcile an ambiguous prior POST by marker before attempting another."""
    if not settings.enable_github_posting or not settings.github_token:
        raise RuntimeError("GitHub posting is not configured")
    request = state["request"]
    repo, number = request["repo"], request["pr_number"]
    marker = f"<!-- code-reviewer:{state['review_id']} -->"
    with session() as client:
        actor = get(client, "/user")["id"]
        for page in range(1, 31):
            comments = get(
                client, f"/repos/{repo}/issues/{number}/comments", per_page=100, page=page
            )
            for comment in comments:
                if marker in comment.get("body", "") and comment.get("user", {}).get("id") == actor:
                    return comment["html_url"]
            if len(comments) < 100:
                break
        else:
            raise RuntimeError("Cannot safely reconcile more than 3000 comments")
        if get(client, f"/repos/{repo}/pulls/{number}")["head"]["sha"] != state["head_sha"]:
            raise RuntimeError("PR head changed since review; create a new review")
        # Escape source/model text to avoid mentions, HTML, or misleading Markdown.
        import html

        def escape(value):
            import re

            value = html.escape(str(value)).replace("@", "&#64;").replace("\n", " ")
            return re.sub(r"([\\`*_[\]#!|~])", r"\\\1", value)

        body = (
            f"{marker}\n## Code review\nCommit: {state['head_sha']}\n\n{escape(state['summary'])}\n"
        )
        for finding in state.get("findings", []):
            body += (
                f"\n- **{finding['severity']}** <code>{escape(finding['file'])}:"
                f"{finding['line']}</code> ({finding['source']}/{escape(finding['rule_id'])}): "
                f"{escape(finding['explanation'])} Fix: {escape(finding['fix'])}\n"
            )
        if len(body) > 60_000:
            raise RuntimeError("Review is too large for a single GitHub comment")
        response = client.post(
            f"{API}/repos/{repo}/issues/{number}/comments", json={"body": body}, timeout=30
        )
        response.raise_for_status()
        return response.json()["html_url"]
