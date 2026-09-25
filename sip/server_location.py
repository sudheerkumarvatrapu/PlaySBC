"""RFC 3263-oriented SIP server discovery with bounded caching and failover."""

from __future__ import annotations

import ipaddress
import random
import socket
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

try:  # Installed in the product image; optional for dependency-free unit tests.
    import dns.resolver  # type: ignore
except ImportError:  # pragma: no cover - exercised by production image integration.
    dns = None  # type: ignore


@dataclass(frozen=True)
class ServerTarget:
    host: str
    port: int
    transport: str
    priority: int = 0
    weight: int = 0


def _weighted_order(records: Iterable[ServerTarget], rng: random.Random) -> list[ServerTarget]:
    pending = list(records)
    ordered: list[ServerTarget] = []
    while pending:
        lowest = min(item.priority for item in pending)
        group = [item for item in pending if item.priority == lowest]
        while group:
            total = sum(max(0, item.weight) for item in group)
            if total:
                pick = rng.randint(1, total)
                running = 0
                chosen = group[-1]
                for item in group:
                    running += max(0, item.weight)
                    if pick <= running:
                        chosen = item
                        break
            else:
                chosen = group[rng.randrange(len(group))]
            ordered.append(chosen)
            group.remove(chosen)
            pending.remove(chosen)
    return ordered


class ServerLocator:
    """Resolve NAPTR/SRV/A/AAAA candidates and retain them for a bounded TTL."""

    def __init__(
        self,
        config: Optional[dict[str, Any]] = None,
        *,
        query: Optional[Callable[[str, str], list[Any]]] = None,
        address_lookup: Optional[Callable[[str, int], list[tuple[str, int]]]] = None,
        clock: Callable[[], float] = time.monotonic,
        rng: Optional[random.Random] = None,
    ):
        values = dict(config or {})
        self.enabled = bool(values.get("enabled", True))
        self.use_naptr = bool(values.get("naptr", True))
        self.use_srv = bool(values.get("srv", True))
        self.cache_ttl = float(values.get("cache_ttl", 60.0))
        self.negative_ttl = float(values.get("negative_ttl", 5.0))
        self.max_targets = int(values.get("max_targets", 8))
        self._query = query or self._system_query
        self._address_lookup = address_lookup or self._system_addresses
        self._clock = clock
        self._rng = rng or random.Random()
        self._cache: dict[tuple[str, int, str], tuple[float, list[ServerTarget]]] = {}

    def resolve(self, host: str, port: int, transport: str) -> list[ServerTarget]:
        normalized_host = host.rstrip(".")
        normalized_transport = transport.lower()
        try:
            ipaddress.ip_address(normalized_host.strip("[]"))
            return [ServerTarget(normalized_host.strip("[]"), port, normalized_transport)]
        except ValueError:
            pass
        key = (normalized_host.lower(), int(port), normalized_transport)
        cached = self._cache.get(key)
        now = self._clock()
        if cached and cached[0] > now:
            return list(cached[1])

        candidates: list[ServerTarget] = []
        service_names = self._naptr_services(normalized_host, normalized_transport)
        if not service_names:
            service_names = [self._srv_name(normalized_host, normalized_transport)]
        if self.enabled and self.use_srv:
            for service_name in service_names:
                candidates.extend(self._srv_targets(service_name, normalized_transport))
        if not candidates:
            candidates = [ServerTarget(normalized_host, port, normalized_transport)]

        expanded: list[ServerTarget] = []
        for candidate in _weighted_order(candidates, self._rng):
            addresses = self._address_lookup(candidate.host, candidate.port)
            for address, resolved_port in addresses:
                target = ServerTarget(
                    address, resolved_port, candidate.transport, candidate.priority, candidate.weight
                )
                if target not in expanded:
                    expanded.append(target)
                if len(expanded) >= self.max_targets:
                    break
            if len(expanded) >= self.max_targets:
                break
        ttl = self.cache_ttl if expanded else self.negative_ttl
        self._cache[key] = (now + ttl, expanded)
        return list(expanded)

    def _naptr_services(self, host: str, transport: str) -> list[str]:
        if not self.enabled or not self.use_naptr:
            return []
        desired = {"udp": "SIP+D2U", "tcp": "SIP+D2T", "tls": "SIPS+D2T"}[transport]
        records = []
        for record in self._safe_query(host, "NAPTR"):
            service = str(getattr(record, "service", "")).strip('"').upper()
            replacement = str(getattr(record, "replacement", "")).rstrip(".")
            if service == desired and replacement:
                records.append((int(record.order), int(record.preference), replacement))
        return [item[2] for item in sorted(records)]

    def _srv_targets(self, name: str, transport: str) -> list[ServerTarget]:
        targets = []
        for record in self._safe_query(name, "SRV"):
            host = str(record.target).rstrip(".")
            targets.append(
                ServerTarget(host, int(record.port), transport, int(record.priority), int(record.weight))
            )
        return targets

    def _safe_query(self, name: str, record_type: str) -> list[Any]:
        try:
            return list(self._query(name, record_type))
        except Exception:
            return []

    @staticmethod
    def _srv_name(host: str, transport: str) -> str:
        return f"_{'sips' if transport == 'tls' else 'sip'}._{'udp' if transport == 'udp' else 'tcp'}.{host}"

    @staticmethod
    def _system_query(name: str, record_type: str) -> list[Any]:
        if dns is None:
            return []
        return list(dns.resolver.resolve(name, record_type, lifetime=2.0))

    @staticmethod
    def _system_addresses(host: str, port: int) -> list[tuple[str, int]]:
        results: list[tuple[str, int]] = []
        for _family, _type, _proto, _canonname, sockaddr in socket.getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        ):
            candidate = (str(sockaddr[0]), int(sockaddr[1]))
            if candidate not in results:
                results.append(candidate)
        return results
