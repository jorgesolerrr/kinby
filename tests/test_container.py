import json
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parents[1]


def _docker(
    *args: str,
    check: bool = True,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
    )


def _docker_is_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        result = _docker("info", "--format", "{{.ServerVersion}}", check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


@pytest.mark.skipif(not _docker_is_available(), reason="Docker daemon is not available")
def test_image_runs_a_mounted_instance_with_the_container_contract() -> None:
    image = f"kinby-container-test-{uuid.uuid4().hex}"
    instance = PROJECT_ROOT / "examples" / "instances" / "minimal"

    try:
        _docker("build", "--quiet", "--tag", image, ".")
        inspection = _docker("image", "inspect", image)
        config = json.loads(inspection.stdout)[0]["Config"]

        assert "KINBY_INSTANCE=/instance" in config["Env"]
        assert config["Volumes"] == {"/instance": {}}
        assert config["Entrypoint"] == ["kinby"]
        assert config["Cmd"] == ["run"]

        result = _docker(
            "run",
            "--rm",
            "--mount",
            f"type=bind,src={instance},dst=/instance,readonly",
            image,
            "instance",
            "show",
            check=False,
        )

        assert result.returncode == 0
        assert "id: minimal" in result.stdout
        assert "path: /instance" in result.stdout
        assert "matching rule: KINBY_INSTANCE" in result.stdout
        assert result.stderr == ""
    finally:
        _docker("image", "rm", "--force", image, check=False)
