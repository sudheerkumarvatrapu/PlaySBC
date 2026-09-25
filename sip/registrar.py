"""Registrar, digest, identity, and routing-policy primitives."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, Optional, Tuple


SUPPORTED_DIGEST_ALGORITHMS = ("SHA-256", "MD5")
DEFAULT_DIGEST_ALGORITHMS = ("MD5", "SHA-256")


def digest_hex(algorithm: str, value: str) -> str:
    name = algorithm.upper().replace("-SESS", "")
    if name == "SHA-256":
        return hashlib.sha256(value.encode()).hexdigest()
    if name == "MD5":
        return hashlib.md5(value.encode()).hexdigest()
    raise ValueError(f"unsupported digest algorithm {algorithm}")


def digest_response(username: str, realm: str, password: str, method: str, uri: str,
                    nonce: str, *, algorithm: str = "SHA-256", nc: str = "",
                    cnonce: str = "", qop: str = "") -> str:
    ha1 = digest_hex(algorithm, f"{username}:{realm}:{password}")
    if algorithm.upper().endswith("-SESS"):
        ha1 = digest_hex(algorithm, f"{ha1}:{nonce}:{cnonce}")
    ha2 = digest_hex(algorithm, f"{method}:{uri}")
    return digest_hex(algorithm, f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}" if qop else f"{ha1}:{nonce}:{ha2}")


@dataclass
class NonceRecord:
    issued_at: float
    highest_nc: Dict[str, int] = field(default_factory=dict)


class DigestNonceStore:
    def __init__(self, lifetime: float = 300.0):
        self.lifetime = max(1.0, float(lifetime))
        self.records: Dict[str, NonceRecord] = {}

    def issue(self, now: Optional[float] = None) -> str:
        nonce = secrets.token_hex(24)
        self.records[nonce] = NonceRecord(time.time() if now is None else now)
        return nonce

    def validate(self, nonce: str, username: str, nc: str, now: Optional[float] = None, *, commit: bool = True) -> str:
        record = self.records.get(nonce)
        timestamp = time.time() if now is None else now
        if not record or timestamp - record.issued_at > self.lifetime:
            self.records.pop(nonce, None)
            return "stale"
        if not re.fullmatch(r"[0-9A-Fa-f]{8}", nc or ""):
            return "invalid"
        value = int(nc, 16)
        if value <= record.highest_nc.get(username, 0):
            return "replay"
        if commit:
            record.highest_nc[username] = value
        return "ok"


@dataclass(frozen=True)
class ContactBinding:
    aor: str
    contact_uri: str
    source: Tuple[str, int]
    expires_at: float
    q: float = 1.0
    path: Tuple[str, ...] = ()
    instance_id: str = ""
    reg_id: str = ""
    flow_token: str = ""

    def expired(self, now: float) -> bool:
        return self.expires_at <= now


class LocationService:
    """Bounded multi-contact location service with deterministic q ordering."""

    def __init__(self, max_bindings_per_aor: int = 8):
        self.max_bindings_per_aor = max(1, int(max_bindings_per_aor))
        self._bindings: Dict[str, Dict[str, ContactBinding]] = {}

    def upsert(self, binding: ContactBinding) -> None:
        bucket = self._bindings.setdefault(binding.aor, {})
        if binding.contact_uri not in bucket and len(bucket) >= self.max_bindings_per_aor:
            raise ValueError("binding limit exceeded")
        bucket[binding.contact_uri] = binding

    def remove(self, aor: str, contact_uri: str = "") -> None:
        if not contact_uri:
            self._bindings.pop(aor, None)
            return
        bucket = self._bindings.get(aor, {})
        bucket.pop(contact_uri, None)
        if not bucket:
            self._bindings.pop(aor, None)

    def bindings(self, aor: str, now: Optional[float] = None) -> Tuple[ContactBinding, ...]:
        timestamp = time.time() if now is None else now
        bucket = self._bindings.get(aor, {})
        for uri, binding in tuple(bucket.items()):
            if binding.expired(timestamp):
                bucket.pop(uri, None)
        return tuple(sorted(bucket.values(), key=lambda item: (-item.q, item.contact_uri)))

    def best(self, aor: str, now: Optional[float] = None) -> Optional[ContactBinding]:
        values = self.bindings(aor, now)
        return values[0] if values else None


def parse_contact_bindings(value: str) -> Tuple[Tuple[str, Dict[str, str]], ...]:
    if value.strip() == "*":
        return (("*", {}),)
    entries = re.split(r',(?=(?:[^"<]*["<][^">]*[">])*[^">]*$)', value)
    result = []
    for entry in entries:
        match = re.search(r"<([^>]+)>|((?:sips?):[^;\s,]+)", entry.strip(), re.I)
        if not match:
            raise ValueError("invalid Contact")
        uri = match.group(1) or match.group(2)
        params = {key.lower(): (raw.strip('"') or "") for key, raw in re.findall(r";\s*([^=;\s]+)(?:=([^;,]+))?", entry)}
        result.append((uri, params))
    return tuple(result)


def privacy_filter(headers: Dict[str, str], trusted_source: bool) -> Dict[str, str]:
    output = dict(headers)
    privacy = {token.strip().lower() for token in output.get("privacy", "").split(",")}
    sensitive = {"p-asserted-identity", "p-preferred-identity", "remote-party-id"}
    if not trusted_source:
        for name in sensitive:
            output.pop(name, None)
    if privacy & {"id", "header", "user", "full"}:
        for name in sensitive | {"history-info", "diversion"}:
            output.pop(name, None)
    return output
