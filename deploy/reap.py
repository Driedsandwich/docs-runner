"""Run from a host systemd timer to recover after controller/VM crashes.

Only removes Docs Runner resources older than ten minutes. No shell, no secrets.
"""
import json
import subprocess
from datetime import datetime, timezone


def docker(*args):
    return subprocess.check_output(["docker", *args], timeout=20, stderr=subprocess.DEVNULL).decode()


def main():
    now = datetime.now(timezone.utc)
    for kind in ("container", "network"):
        ids = docker(kind, "ls", *(["--all"] if kind == "container" else []), "--filter", "label=docsrunner.managed=true", "-q").split()
        for resource in ids:
            try:
                info = json.loads(docker(kind, "inspect", resource))[0]
                created = datetime.fromisoformat(info["Created"].replace("Z", "+00:00"))
                if (now - created).total_seconds() <= 600:
                    continue
                docker(kind, "rm", *(["--force"] if kind == "container" else []), resource)
            except (subprocess.SubprocessError, ValueError, KeyError):
                # Next timer tick retries; never claim successful deletion on error.
                print("Docs Runner resource cleanup failed")


if __name__ == "__main__":
    main()

