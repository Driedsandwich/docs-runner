import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

USER_AGENT = "DocsRunner/0.1"
MAX_BYTES = 256 * 1024
DEMO_DIR = Path(__file__).resolve().parent.parent / "demo_docs"


class FetchError(Exception):
    pass


def validate_url(url: str) -> str:
    if len(url) > 2048 or any(ord(c) < 33 for c in url) or "\\" in url:
        raise FetchError("Invalid URL")
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None or parts.password is not None:
            raise ValueError()
        if parts.port not in {None, 80, 443} or parts.query or parts.fragment:
            raise ValueError()
        host = parts.hostname.encode("idna").decode("ascii").lower().rstrip(".")
        iphost = f"[{host}]" if ":" in host else host
        netloc = iphost + (f":{parts.port}" if parts.port else "")
        return urlunsplit((parts.scheme, netloc, parts.path or "/", "", ""))
    except (ValueError, UnicodeError):
        raise FetchError("Use a public HTTP(S) URL without credentials, query, fragment or nonstandard port") from None


async def public_ips(host: str) -> list[str]:
    try:
        entries = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM), 5)
    except (OSError, TimeoutError):
        raise FetchError("DNS lookup failed") from None
    addresses = list(dict.fromkeys(entry[4][0] for entry in entries))
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise FetchError("Private, loopback, link-local and reserved destinations are forbidden")
    return addresses


@dataclass
class Document:
    url: str
    domain: str
    raw: str
    body: str
    builtin: bool = False


def extract_body(raw: str, content_type: str) -> str:
    if "html" not in content_type:
        return raw
    soup = BeautifulSoup(raw, "html.parser")
    for node in list(soup.find_all(["script", "style", "nav", "footer", "header"])):
        node.decompose()
    for node in list(soup.select('[hidden], [aria-hidden="true"]')):
        node.decompose()
    for node in list(soup.find_all(style=True)):
        if any(x in node.get("style", "").replace(" ", "").lower() for x in ("display:none", "visibility:hidden")):
            node.decompose()
    return (soup.find("main") or soup.find("article") or soup).get_text("\n", strip=True)


class Fetcher:
    def __init__(self, public_origin: str):
        self.public_origin = public_origin.rstrip("/")

    async def request(self, url: str):
        """Pin the validated IP for the actual socket; retain HTTP Host and TLS SNI."""
        parts = urlsplit(url)
        ips = await public_ips(parts.hostname)
        ip = ips[0]
        host = f"[{ip}]" if ":" in ip else ip
        pinned = urlunsplit((parts.scheme, host + (f":{parts.port}" if parts.port else ""), parts.path, "", ""))
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=10) as client:
            async with client.stream("GET", pinned, headers={"Host": parts.netloc, "User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
                                     extensions={"sni_hostname": parts.hostname}) as response:
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise FetchError("Compressed responses are unsupported")
                data = bytearray()
                async for chunk in response.aiter_bytes(65536):
                    data.extend(chunk)
                    if len(data) > MAX_BYTES:
                        raise FetchError("Page exceeds 256 KiB")
                return response.status_code, dict(response.headers), bytes(data).decode("utf-8", "replace")

    async def robots(self, url: str):
        parts = urlsplit(url)
        origin = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
        target = origin
        for _ in range(4):
            status, headers, raw = await self.request(target)
            if status in {301, 302, 303, 307, 308}:
                target = validate_url(urljoin(target, headers.get("location", "")))
                continue
            if status == 404:
                return
            if status != 200:
                raise FetchError("robots.txt unavailable or access denied")
            robot = RobotFileParser(origin)
            robot.parse(raw.splitlines())
            if not robot.can_fetch(USER_AGENT, url):
                raise FetchError("robots.txt disallows DocsRunner")
            return
        raise FetchError("Too many robots.txt redirects")

    async def fetch(self, url: str) -> Document:
        # Exact bundled fixtures only: never make an exception for arbitrary local HTTP.
        for name in ("good", "stale", "hostile"):
            if url == f"{self.public_origin}/demo-docs/{name}":
                raw = (DEMO_DIR / f"{name}.html").read_text()
                return Document(url, "", raw, extract_body(raw, "text/html"), True)
        current = validate_url(url)
        async with asyncio.timeout(40):
            for _ in range(5):
                await self.robots(current)
                status, headers, raw = await self.request(current)
                if status in {301, 302, 303, 307, 308}:
                    current = validate_url(urljoin(current, headers.get("location", "")))
                    continue
                if status != 200:
                    raise FetchError(f"Public page unavailable (HTTP {status})")
                kind = headers.get("content-type", "").lower()
                if not any(t in kind for t in ("text/html", "text/plain", "text/markdown")):
                    raise FetchError("Only HTML, plain text and Markdown pages are supported")
                if "html" in kind:
                    soup = BeautifulSoup(raw, "html.parser")
                    if soup.select_one('input[type="password"]'):
                        raise FetchError("Login pages are unsupported")
                return Document(current, urlsplit(current).hostname, raw, extract_body(raw, kind))
        raise FetchError("Too many redirects")
