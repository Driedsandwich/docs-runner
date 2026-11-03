"""Disposable Docker execution. No document command ever runs in a host shell."""
import asyncio
import base64
import json
import socket
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.config import Settings
from app.models import Evidence, Step
from proxy.config import complete_audit, denied_hosts, render

TAIL_BYTES = 4096


class SandboxError(Exception):
    pass


async def controller_addresses(origin: str):
    host = urlsplit(origin).hostname
    if not host:
        raise SandboxError("Public origin must be configured")
    addresses = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM), 5)
    return sorted({entry[4][0] for entry in addresses})


class Capture:
    def __init__(self, needles=()):
        self.tail = b""
        self.needles = [n.encode() for n in needles]
        self.matches = [False] * len(self.needles)
        self.window = b""

    def feed(self, chunk: bytes):
        data = self.window + chunk
        for i, needle in enumerate(self.needles):
            self.matches[i] |= needle in data
        keep = max((len(n) for n in self.needles), default=1) - 1
        self.window = data[-keep:] if keep else b""
        self.tail = (self.tail + chunk)[-TAIL_BYTES:]

    def text(self):
        # Ignore partial UTF-8 at the tail boundary; encoded output stays <=4 KiB.
        return self.tail.decode("utf-8", "ignore")


async def drain(stream, capture):
    while chunk := await stream.read(16384):
        capture.feed(chunk)


async def docker(*args, timeout=15, stdin: bytes | None = None):
    process = await asyncio.create_subprocess_exec(
        "docker", *args, stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        # Do not pass the controller's inference key to Docker CLI subprocesses.
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "DOCKER_HOST": "unix:///var/run/docker.sock"},
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(stdin), timeout)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode:
        # Docker errors can echo args. Keep them out of API and application logs.
        raise SandboxError("Docker operation failed")
    return stdout


@dataclass
class Cleanup:
    sandbox_removed: bool = False
    proxy_removed: bool = False
    network_removed: bool = False
    denied_destinations: list[str] | None = None
    audit_complete: bool = False


class DockerRunner:
    def __init__(self, job_id: str, settings: Settings):
        self.settings = settings
        self.name = f"docsrunner-job-{job_id}"
        self.proxy = f"docsrunner-proxy-{job_id}"
        self.network = f"docsrunner-net-{job_id}"
        self.runtime = "runc"
        self.alive = False
        self.created = False

    async def start(self, domain: str):
        # Docker 28+ required for isolated bridge mode (no host-side bridge address).
        version = json.loads(await docker("version", "--format", "{{json .Server}}"))
        if int(version["Version"].split(".")[0]) < 28:
            raise SandboxError("Docker Engine 28 or newer is required")
        info = json.loads(await docker("info", "--format", "{{json .Runtimes}}"))
        requested = self.settings.docsrunner_runtime
        self.runtime = ("runsc" if "runsc" in info else "runc") if requested == "auto" else requested
        if self.runtime not in info:
            raise SandboxError("Requested sandbox runtime is not registered")
        await docker("network", "create", "--internal", "--driver", "bridge",
                     "--opt", "com.docker.network.bridge.gateway_mode_ipv4=isolated",
                     "--label", "docsrunner.managed=true", self.network)
        config = base64.b64encode(render(domain, await controller_addresses(self.settings.docsrunner_public_origin)).encode()).decode()
        await docker("create", "--name", self.proxy, "--label", "docsrunner.managed=true",
                     "--network", self.network, "--network-alias", "egress",
                     "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                     "--memory", "128m", "--memory-swap", "128m", "--pids-limit", "32", "--cpus", "0.25",
                     "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777",
                     "--sysctl", "net.ipv4.ip_forward=0", "--sysctl", "net.ipv6.conf.all.disable_ipv6=1",
                     "--log-driver", "local", "--log-opt", "max-size=1m", "--log-opt", "max-file=1", "--log-opt", "compress=false",
                     "--entrypoint", "/bin/sh", self.settings.docsrunner_proxy_image,
                     "-c", 'printf %s "$1" | base64 -d > /tmp/squid.conf; exec /usr/sbin/squid -N -f /tmp/squid.conf', "--", config)
        await docker("network", "connect", self.settings.docsrunner_egress_network, self.proxy)
        await docker("start", self.proxy)
        # gVisor does not see Docker's embedded DNS, so pin the proxy name to its address on the internal network.
        proxy_ip = (await docker("inspect", "--format",
                                 "{{(index .NetworkSettings.Networks \"" + self.network + "\").IPAddress}}",
                                 self.proxy)).decode().strip()
        if not proxy_ip:
            raise SandboxError("Egress proxy has no internal address")
        await docker("create", "--name", self.name, "--label", "docsrunner.managed=true",
                     "--runtime", self.runtime, "--network", self.network, "--dns", "127.0.0.1", "--add-host", f"egress:{proxy_ip}",
                     "--read-only", "--user", "65532:65532", "--cap-drop", "ALL",
                     "--security-opt", "no-new-privileges", "--memory", "512m", "--memory-swap", "512m",
                     "--pids-limit", "128", "--cpus", "1", "--ulimit", "nofile=256:256",
                     "--sysctl", "net.ipv6.conf.all.disable_ipv6=1",
                     "--tmpfs", "/work:rw,nosuid,nodev,size=256m,uid=65532,gid=65532,mode=0700",
                     "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m,uid=65532,gid=65532,mode=0700",
                     "--shm-size", "16m", "--workdir", "/work", "--log-driver", "none",
                     self.settings.docsrunner_sandbox_image)
        self.created = True
        await docker("start", self.name)
        self.alive = True
        # Proxy startup must succeed; fail closed before executing any document command.
        for _ in range(20):
            try:
                await docker("exec", self.name, "/usr/local/bin/python3", "-I", "-S", "-c",
                             "import socket; socket.create_connection(('egress',3128),1).close()", timeout=2)
                return
            except SandboxError:
                await asyncio.sleep(0.1)
        raise SandboxError("Egress proxy did not become ready")

    async def probe(self, files=(), sweep=False):
        return json.loads(await docker("exec", "-i", self.name, "/usr/local/bin/python3", "-I", "-S", "/opt/probe.py",
                                       stdin=json.dumps({"files": list(files), "sweep": sweep}).encode(), timeout=3))

    async def stop(self):
        self.alive = False
        try:
            await docker("kill", self.name, timeout=5)
        except (SandboxError, TimeoutError):
            pass

    async def process_limit_reached(self):
        # runsc's host tasks and guest processes are different populations.
        # A Docker stats gauge is not evidence of a guest PID limit event, and
        # a low/zero value after sandbox death cannot rule out an earlier hit.
        if self.runtime == "runsc":
            return False
        try:
            # Host-side Docker stats still work when an exhausted cgroup cannot exec a probe.
            count = await docker("stats", "--no-stream", "--format", "{{.PIDs}}", self.name, timeout=3)
            return int(count.strip()) >= 128
        except (SandboxError, TimeoutError, ValueError):
            return False

    async def run(self, step: Step, timeout: float) -> Evidence:
        if not self.alive:
            raise SandboxError("Sandbox is no longer running")
        started = time.monotonic()
        before = await self.probe()
        out_indices = [i for i, e in enumerate(step.expectations) if e.kind == "stdout"]
        err_indices = [i for i, e in enumerate(step.expectations) if e.kind == "stderr"]
        out = Capture([step.expectations[i].value for i in out_indices])
        err = Capture([step.expectations[i].value for i in err_indices])
        process = await asyncio.create_subprocess_exec(
            "docker", "exec", "--workdir", "/work", self.name,
            "/usr/bin/env", "-i", "/bin/bash", "--noprofile", "--norc", "-c", step.command,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "DOCKER_HOST": "unix:///var/run/docker.sock"},
        )
        readers = [asyncio.create_task(drain(process.stdout, out)), asyncio.create_task(drain(process.stderr, err))]
        limits = []
        observation_failed = False
        after = {"counters": {}, "files": []}
        try:
            async with asyncio.timeout(timeout):
                await process.wait()
                await asyncio.gather(*readers)
                after = await self.probe([e.value for e in step.expectations if e.kind == "file"], sweep=True)
        except TimeoutError:
            limits.append("timeout")
            if await self.process_limit_reached():
                limits.append("pids")
            await self.stop()  # Kill the WHOLE cgroup, including forked/background descendants.
        except asyncio.CancelledError:
            await self.stop()
            raise
        except (SandboxError, json.JSONDecodeError):
            observation_failed = True
            if await self.process_limit_reached():
                limits.append("pids")
            await self.stop()
        finally:
            if process.returncode is None:
                process.kill()
            await process.wait()
            for task in readers:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*readers, return_exceptions=True)
        try:
            state = json.loads(await docker("inspect", "--format", "{{json .State}}", self.name, timeout=3))
            if state.get("OOMKilled"):
                limits.append("oom")
            self.alive = self.alive and state.get("Running", False)
        except (SandboxError, TimeoutError, json.JSONDecodeError):
            self.alive = False
        metric_pairs = (("memory.events", "oom_kill", "oom"), ("pids.events", "max", "pids"))
        metrics_available = True
        for source, key, name in metric_pairs:
            if self.runtime == "runsc" and name == "pids":
                # The guest cgroup is not the host cgroup enforcing Docker's
                # limit. Do not promote guest counters into host evidence.
                metrics_available = False
                continue
            a, b = before["counters"].get(source), after["counters"].get(source)
            if not a or not b:
                metrics_available = False
            elif int(b.get(key, 0)) > int(a.get(key, 0)) and name not in limits:
                limits.append(name)
        if limits:
            await self.stop()
        if self.runtime == "runsc":
            # This deployment exposes the Docker socket, not host cgroups or
            # runsc state. No reliable PID event source is available through
            # the current adapter. Record that even on successful steps;
            # missing telemetry alone must neither fail nor stop a good job.
            limits.append("pids_unmetered")
        matched = [False] * len(step.expectations)
        for indices, capture in ((out_indices, out), (err_indices, err)):
            for i, value in zip(indices, capture.matches):
                matched[i] = value
        file_matches = iter(after["files"])
        for i, expected in enumerate(step.expectations):
            if expected.kind == "file":
                matched[i] = next(file_matches, False)
        return Evidence(command=step.command, exit_code=None if observation_failed else process.returncode, stdout=out.text(), stderr=err.text(),
                        duration_seconds=round(time.monotonic() - started, 3), limits=limits,
                        matched=matched, metrics_available=metrics_available)

    async def cleanup(self) -> Cleanup:
        result = Cleanup(denied_destinations=[])
        self.alive = False
        # Stop sandbox before collecting proxy logs; let connections settle.
        if self.created:
            try:
                await docker("kill", self.name, timeout=5)
            except (SandboxError, TimeoutError):
                pass
        try:
            await docker("stop", "--time", "2", self.proxy, timeout=5)
            logs = await docker("logs", "--tail", "5000", self.proxy, timeout=5)
            decoded = logs.decode("utf-8", "replace")
            result.denied_destinations = denied_hosts(decoded)
            # Bounded logging can truncate a flood. Be explicit about this in the report.
            result.audit_complete = complete_audit(decoded)
        except (SandboxError, TimeoutError, FileNotFoundError):
            pass
        for resource, attr, kind in ((self.name, "sandbox_removed", "container"),
                                     (self.proxy, "proxy_removed", "container"),
                                     (self.network, "network_removed", "network")):
            try:
                if kind == "container":
                    await docker("rm", "--force", resource, timeout=10)
                else:
                    await docker("network", "rm", resource, timeout=10)
            except (SandboxError, TimeoutError, FileNotFoundError):
                pass
            try:
                # Query existence without conflating daemon-unavailable with NotFound.
                ids = await docker(kind, "ls", *( ["--all"] if kind == "container" else [] ),
                                   "--filter", f"name=^{resource}$", "--format", "{{.ID}}", timeout=5)
                setattr(result, attr, not ids.strip())
            except (SandboxError, TimeoutError, FileNotFoundError):
                pass
        return result
