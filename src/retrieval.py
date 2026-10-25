"""Bounded lexical retrieval at the reviewed SHA (no vector database required)."""

import re
from pathlib import PurePosixPath

from github_client import fetch_content, get, session


def retrieve_context(state: dict) -> dict:
    files, sha, repo = state["files"], state["head_sha"], state["source_repo"]
    stems = {PurePosixPath(f["filename"]).stem for f in files}
    symbols = set(
        re.findall(r"\b(?:def|class)\s+(\w+)", "\n".join(f["raw_content"] for f in files))
    )
    changed = {f["filename"] for f in files}
    with session() as client:
        tree = get(client, f"/repos/{repo}/git/trees/{sha}", recursive=1)
        candidates = []
        for row in tree.get("tree", []):
            path = row["path"]
            if row.get("type") != "blob" or path in changed:
                continue
            convention = PurePosixPath(path).name.lower() in {"contributing.md", "pyproject.toml"}
            if not (path.endswith(".py") or convention):
                continue
            rank = (10 if convention else 0) + sum(3 for stem in stems if stem in path)
            candidates.append((rank, path))
        candidates.sort(key=lambda item: (-item[0], item[1]))
        matches = []
        errors = []
        for rank, path in candidates[:20]:
            try:
                content = fetch_content(client, repo, path, sha)
            except ValueError:
                continue
            lines = content.splitlines()
            hits = [
                i
                for i, line in enumerate(lines)
                if any(re.search(rf"\b{re.escape(symbol)}\b", line) for symbol in symbols)
            ]
            if not rank and not hits:
                continue
            start = max(0, (hits[0] if hits else 0) - 5)
            excerpt = "\n".join(lines[start : start + 60])[:6000]
            matches.append(
                {
                    "file": path,
                    "start_line": start + 1,
                    "content": excerpt,
                    "head_sha": sha,
                    "score": rank + len(hits),
                }
            )
        if tree.get("truncated"):
            errors.append("Repository tree was truncated; retrieval coverage is limited")
    matches.sort(key=lambda item: (-item["score"], item["file"]))
    return {
        "context": matches[:4],
        "retrieval_notes": errors,
        "retrieval_candidates_scanned": min(20, len(candidates)),
    }
