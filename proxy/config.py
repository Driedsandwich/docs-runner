import ipaddress
import re
from pathlib import Path

DEFAULT_DOMAINS = (
    "pypi.org", "files.pythonhosted.org", "registry.npmjs.org", "github.com",
    "codeload.github.com", "objects.githubusercontent.com",
)


def valid_domain(domain: str) -> str:
    domain = domain.encode("idna").decode("ascii").lower().rstrip(".")
    if len(domain) > 253 or not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}", domain):
        raise ValueError("Invalid document domain")
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        return domain
    raise ValueError("IP literals cannot be added to the proxy allowlist")


def render(domain: str = "", controller_ips=()) -> str:
    domains = list(DEFAULT_DOMAINS)
    if domain:
        domains.append(valid_domain(domain))
    template = Path(__file__).with_name("squid.conf.template").read_text()
    blocked = [str(ipaddress.ip_address(ip)) for ip in controller_ips] or ["127.0.0.1"]
    return template.replace("@@DOMAINS@@", " ".join(sorted(set(domains)))).replace("@@CONTROL_IPS@@", " ".join(blocked))


def denied_hosts(log: str) -> list[str]:
    # Log only domain, never URL path, query, credentials or request headers.
    hosts = set()
    for line in log.splitlines():
        parts = line.split()
        if len(parts) == 4 and parts[0] == "DENIED" and parts[1].isdigit() and parts[2].startswith("TCP_DENIED"):
            host = parts[3]
            if len(host) <= 253 and re.fullmatch(r"[A-Za-z0-9.:[\]-]+", host):
                hosts.add(host)
    return sorted(hosts)


def complete_audit(log: str) -> bool:
    # Without an initial sequence number, rotation/omission cannot be ruled out.
    lines = log.splitlines()
    if not lines or len(lines) >= 5000 or len(log.encode()) >= 900_000:
        return False
    first = lines[0].split()
    return len(first) == 4 and first[0] == "DENIED" and first[1] in {"0", "1"}

