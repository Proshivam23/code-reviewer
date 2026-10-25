import os
import stat
import subprocess

import pytest

import tools
from paths import safe_relative_path, write_sources


@pytest.mark.parametrize(
    "path",
    [
        "../x.py",
        "/x.py",
        "C:/x.py",
        "a\\x.py",
        "a/../x.py",
        "a//x.py",
        "NUL.py",
        "a./x.py",
        "x.py:secret",
    ],
)
def test_unsafe_paths_rejected(path):
    with pytest.raises(ValueError):
        safe_relative_path(path)


def test_case_collision_rejected(tmp_path):
    with pytest.raises(ValueError):
        write_sources(
            tmp_path, [{"filename": name, "raw_content": ""} for name in ["a.py", "A.py"]]
        )


@pytest.mark.skipif(os.name == "nt", reason="POSIX container mount permissions")
def test_private_parent_and_readable_mount(tmp_path):
    tmp_path.chmod(0o700)
    mount = tmp_path / "source"
    write_sources(mount, [{"filename": "nested/app.py", "raw_content": "x = 1\n"}])
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    assert stat.S_IMODE(mount.stat().st_mode) == 0o755
    assert stat.S_IMODE((mount / "nested").stat().st_mode) == 0o755
    assert stat.S_IMODE((mount / "nested/app.py").stat().st_mode) == 0o644


def test_container_is_restricted(monkeypatch, tmp_path):
    seen = []

    def run(command, **kwargs):
        seen.append((command, kwargs))
        return subprocess.CompletedProcess(command, 1, '{"results": [], "errors": []}', "")

    monkeypatch.setattr(tools.subprocess, "run", run)
    assert tools.run_analyzer("bandit", tmp_path) == {"results": [], "errors": []}
    command, options = seen[0]
    assert "--read-only" in command
    assert command[command.index("--network") + 1] == "none"
    assert "--pids-limit" in command and "--cap-drop" in command
    assert f"{tmp_path.resolve()}:/work:ro" in command
    assert options["timeout"] > 0


@pytest.mark.parametrize(
    "code,output", [(2, "[]"), (0, "invalid"), (0, '{"errors":["bad"],"results":[]}')]
)
def test_failure_not_treated_as_no_findings(monkeypatch, tmp_path, code, output):
    monkeypatch.setattr(
        tools.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, code, output, "")
    )
    with pytest.raises(RuntimeError):
        tools.run_analyzer("bandit", tmp_path)


def test_timeout_removes_container(monkeypatch, tmp_path):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(command, 1)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(tools.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="timed out"):
        tools.run_analyzer("ruff", tmp_path)
    assert calls[1][:3] == ["docker", "rm", "-f"]
    assert calls[1][3] == calls[0][calls[0].index("--name") + 1]
