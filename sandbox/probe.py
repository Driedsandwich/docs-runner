"""Read-only helper: clean leftover processes and inspect relative artifacts/cgroups."""
import json
import os
import signal
import sys
from pathlib import Path


def counters():
    result = {}
    for name in ("memory.events", "pids.events"):
        try:
            result[name] = dict(line.split() for line in Path("/sys/fs/cgroup", name).read_text().splitlines())
        except (OSError, ValueError):
            result[name] = None
    return result


def artifact(path):
    # O_NOFOLLOW on each component prevents symlink escapes and FIFO hangs.
    parts = path.split("/")
    if any(p in ("", ".", "..") for p in parts) or path.startswith("/"):
        return False
    fd = os.open("/work", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        import stat
        return stat.S_ISREG(os.stat(parts[-1], dir_fd=fd, follow_symlinks=False).st_mode)
    except OSError:
        return False
    finally:
        os.close(fd)


if __name__ == "__main__":
    request = json.loads(sys.stdin.read(32768) or "{}")
    if request.get("sweep"):
        for entry in Path("/proc").iterdir():
            if entry.name.isdigit() and int(entry.name) not in (1, os.getpid()):
                try:
                    os.kill(int(entry.name), signal.SIGKILL)
                except ProcessLookupError:
                    pass
    print(json.dumps({"counters": counters(), "files": [artifact(p) for p in request.get("files", [])]}))
