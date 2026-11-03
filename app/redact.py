import re


def redact(text: str, secret: str = "") -> str:
    if secret:
        text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)\b(?:sk-[\w-]{12,}|ghp_[\w]{12,}|github_pat_[\w]{12,})", "[REDACTED]", text)
    text = re.sub(r"(?i)(bearer\s+)[^\s\"'<>]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)((?:api[_-]?key|access[_-]?token|password|secret)\s*[=:]\s*)[^\s<>]+", r"\1[REDACTED]", text)
    return text

