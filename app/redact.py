"""密钥脱敏：任何写入事件流、日志、PR 说明的文本都先经过这里。"""
from __future__ import annotations

import re

_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"rlwy_[A-Za-z0-9_-]{10,}"),
    re.compile(r"hf_[A-Za-z0-9]{20,}"),
    re.compile(r"(postgres(?:ql)?(?:\+psycopg)?://[^:\s/]+:)([^@\s]+)(@)"),
    re.compile(r"(?i)(authorization:\s*bearer\s+)\S+"),
    re.compile(r"(?i)(api[_-]?key[\"'\s:=]+)[A-Za-z0-9_\-]{16,}"),
]

MASK = "***REDACTED***"


def redact(text: str) -> str:
    if not text:
        return ""
    result = text
    for pattern in _PATTERNS:
        if pattern.groups >= 2:
            result = pattern.sub(lambda m: m.group(1) + MASK + m.group(3), result)
        else:
            result = pattern.sub(MASK, result)
    return result


def contains_secret(text: str) -> bool:
    if not text:
        return False
    return any(pattern.search(text) for pattern in _PATTERNS)
