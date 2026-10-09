"""Deployment wiring: container entrypoint applies migrations before serving.

Blitz (unlike Render) has no pre-deploy hook, so the image must run
``alembic upgrade head`` itself. These tests guard the Docker/entrypoint wiring
without requiring a Docker daemon.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_entrypoint_script_exists_with_lf_endings():
    script = ROOT / "docker-entrypoint.sh"
    assert script.is_file()
    # A CRLF shebang would break /bin/sh inside the Linux image.
    assert b"\r\n" not in script.read_bytes()


def test_entrypoint_script_has_valid_shell_syntax():
    shell = shutil.which("sh")
    if shell is None:
        pytest.skip("no POSIX shell available")
    script = ROOT / "docker-entrypoint.sh"
    result = subprocess.run([shell, "-n", str(script)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_entrypoint_falls_back_to_in_app_schema_when_scripts_missing():
    script = (ROOT / "docker-entrypoint.sh").read_text(encoding="utf-8")
    assert "migrations/" in script and "AUTO_CREATE_SCHEMA=true" in script
    assert "forcing" in script


def test_dockerfile_applies_migrations_before_start():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh" in dockerfile
    assert 'ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]' in dockerfile
    # The entrypoint delegates to the CMD, which must still honour $PORT.
    assert 'CMD ["sh", "-c", "uvicorn app.main:app' in dockerfile
    assert "${PORT:-8000}" in dockerfile
