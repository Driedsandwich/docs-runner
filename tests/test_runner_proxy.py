import json

import pytest

from proxy.config import DEFAULT_DOMAINS, complete_audit, denied_hosts, render
from sandbox.runner import Capture, DockerRunner, SandboxError


def test_output_tail_is_bounded_and_matches_across_chunks():
    capture = Capture(["hello world"])
    capture.feed(b"hello ")
    capture.feed(b"world" + b"x" * 10000)
    assert capture.matches == [True]
    assert len(capture.tail) == 4096
    assert "hello" not in capture.text()


def test_multibyte_tail_is_within_byte_limit():
    capture = Capture()
    capture.feed(("語" * 5000).encode())
    assert len(capture.text().encode()) <= 4096


def test_proxy_domains_are_exact_and_deny_private_first():
    config = render("docs.example.org")
    assert all(host in config for host in DEFAULT_DOMAINS)
    assert "dstdomain -n" in config and "docs.example.org" in config
    assert "http_access deny nonpublic" in config
    assert config.index("http_access deny nonpublic") < config.index("http_access allow allowed")
    assert "169.254.0.0/16" in config and "fc00::/7" in config


@pytest.mark.parametrize("domain", ["example.org\nhttp_access allow all", "*.example.org", "127.0.0.1", "localhost", "example.org:443"])
def test_proxy_configuration_injection_is_rejected(domain):
    with pytest.raises(ValueError):
        render(domain)


def test_denials_expose_only_destinations():
    log = "DENIED 1 TCP_DENIED blocked.invalid\nDENIED 2 TCP_DENIED blocked.invalid\nDENIED 3 TCP_DENIED https://bad.invalid/?token=secret\n"
    assert denied_hosts(log) == ["blocked.invalid"]


def test_rotated_denial_logs_do_not_claim_complete_audit():
    assert complete_audit("DENIED 1 TCP_DENIED blocked.invalid\n")
    assert not complete_audit("DENIED 50000 TCP_DENIED blocked.invalid\n")
    assert not complete_audit("")


def test_proxy_also_denies_controller_public_ip():
    config = render("docs.example.org", ["93.184.216.34"])
    assert "acl controller_dst dst 93.184.216.34" in config
    assert config.index("http_access deny controller_dst") < config.index("http_access allow allowed")


@pytest.mark.parametrize("runtimes,expected", [({"runc": {}}, "runc"), ({"runc": {}, "runsc": {}}, "runsc")])
async def test_container_contract(monkeypatch, settings, runtimes, expected):
    calls = []
    async def fake_docker(*args, **kwargs):
        calls.append((args, kwargs))
        if args[0] == "version":
            return b'{"Version":"28.0.1"}'
        if args[0] == "info":
            return json.dumps(runtimes).encode()
        return b""
    monkeypatch.setattr("sandbox.runner.docker", fake_docker)
    runner = DockerRunner("test", settings)
    await runner.start("example.org")
    create = next(args for args, _ in calls if args[:3] == ("create", "--name", runner.name))
    for option in ("--read-only", "--cap-drop", "--security-opt", "--memory", "--memory-swap", "--pids-limit", "--cpus", "--tmpfs", "--user", "--dns"):
        assert option in create
    assert "512m" in create and "128" in create and "65532:65532" in create
    assert "--env" not in create and "--volume" not in create and "--privileged" not in create
    assert "--publish" not in create and "--network=host" not in create
    assert runner.runtime == expected
    net = calls[2][0]
    assert "--internal" in net and "com.docker.network.bridge.gateway_mode_ipv4=isolated" in net
    assert "--network-alias" in next(args for args, _ in calls if args[:3] == ("create", "--name", runner.proxy))
    cleanup = await runner.cleanup()
    assert cleanup.sandbox_removed and cleanup.proxy_removed and cleanup.network_removed


async def test_old_docker_fails_closed(monkeypatch, settings):
    async def fake(*args, **kwargs):
        return b'{"Version":"27.5.0"}'
    monkeypatch.setattr("sandbox.runner.docker", fake)
    with pytest.raises(SandboxError):
        await DockerRunner("test", settings).start("")


async def test_daemon_failure_does_not_claim_cleanup(monkeypatch, settings):
    async def failed(*args, **kwargs):
        raise SandboxError("offline")
    monkeypatch.setattr("sandbox.runner.docker", failed)
    result = await DockerRunner("test", settings).cleanup()
    assert not result.sandbox_removed and not result.proxy_removed and not result.network_removed

