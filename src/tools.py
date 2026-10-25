"""Deterministic analyzers. Their mount path is never supplied by the model."""

import json
import subprocess
import uuid
from pathlib import Path

from core.config import settings


def run_analyzer(analyzer: str, review_path: Path) -> dict | list:
    commands = {
        "bandit": ["bandit", "-f", "json", "-r", "/work"],
        "ruff": [
            "ruff",
            "check",
            "--isolated",
            "--select",
            "E4,E7,E9,F",
            "--no-cache",
            "--output-format",
            "json",
            "/work",
        ],
    }
    if analyzer not in commands:
        raise ValueError("Unknown analyzer")
    review_path = review_path.resolve(strict=True)
    name = f"code-review-{uuid.uuid4().hex}"
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--network",
        "none",
        "--memory",
        "512m",
        "--cpus",
        "1",
        "--pids-limit",
        "64",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",
        "-v",
        f"{review_path}:/work:ro",
        settings.code_review_tools_image,
        *commands[analyzer],
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=settings.analyzer_timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        try:
            subprocess.run(
                ["docker", "rm", "-f", name], capture_output=True, timeout=10, check=False
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        raise RuntimeError(f"{analyzer} timed out") from exc
    except OSError as exc:
        raise RuntimeError("Could not start Docker") from exc
    if result.returncode not in (0, 1):
        raise RuntimeError(f"{analyzer} failed with exit code {result.returncode}")
    try:
        output = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise RuntimeError(f"{analyzer} returned invalid JSON") from exc
    if analyzer == "bandit":
        if not isinstance(output, dict) or not isinstance(output.get("results"), list):
            raise RuntimeError("Invalid Bandit result shape")
        if output.get("errors"):
            raise RuntimeError("Bandit could not analyze all files")
    elif not isinstance(output, list):
        raise RuntimeError("Invalid Ruff result shape")
    return output
