"""Deterministic overload, abuse-control, and recovery-hysteresis policy."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque, Dict, Optional, Tuple


@dataclass(frozen=True)
class OverloadDecision:
    allowed: bool
    dimension: str = ""
    retry_after: int = 0


class SlidingWindowLimiter:
    def __init__(self, limit: int, window: float):
        self.limit = max(0, int(limit))
        self.window = max(0.1, float(window))
        self.events: Dict[str, Deque[float]] = defaultdict(deque)

    def allow(self, key: str, now: float) -> bool:
        if self.limit <= 0:
            return True
        values = self.events[key]
        while values and values[0] <= now - self.window:
            values.popleft()
        if len(values) >= self.limit:
            return False
        values.append(now)
        return True


class OverloadController:
    def __init__(self, config: Optional[dict] = None):
        cfg = dict(config or {})
        self.enabled = bool(cfg.get("enabled", False))
        self.retry_after = max(1, int(cfg.get("retry_after", 1)))
        self.source = SlidingWindowLimiter(int(cfg.get("per_source_rps", 0)), 1.0)
        self.methods = {str(k).upper(): SlidingWindowLimiter(int(v), 1.0) for k, v in dict(cfg.get("method_rps", {})).items()}
        self.registration = SlidingWindowLimiter(int(cfg.get("registration_rps", 0)), 1.0)
        self.invite = SlidingWindowLimiter(int(cfg.get("invite_cps", 0)), 1.0)
        self.realms = SlidingWindowLimiter(int(cfg.get("per_realm_rps", 0)), 1.0)
        self.subscribers = SlidingWindowLimiter(int(cfg.get("per_subscriber_rps", 0)), 1.0)
        self.trunks = SlidingWindowLimiter(int(cfg.get("per_trunk_rps", 0)), 1.0)
        self.max_inflight = max(0, int(cfg.get("max_inflight_transactions", 0)))
        self.inflight = 0
        self.rejects: Dict[str, int] = defaultdict(int)
        self.allowed = 0
        self.shed_until = 0.0
        self.recovery_seconds = max(0.0, float(cfg.get("recovery_seconds", 2.0)))

    def decide(self, source: str, method: str, *, realm: str = "", subscriber: str = "",
               trunk: str = "", now: Optional[float] = None, priority: bool = False) -> OverloadDecision:
        timestamp = time.monotonic() if now is None else now
        method = method.upper()
        if not self.enabled or priority:
            self.allowed += 1
            return OverloadDecision(True)
        if timestamp < self.shed_until:
            return self._reject("hysteresis")
        if self.max_inflight and self.inflight >= self.max_inflight:
            self.shed_until = max(self.shed_until, timestamp + self.recovery_seconds)
            return self._reject("transactions")
        checks = [("source", self.source, source)]
        if realm:
            checks.append(("realm", self.realms, realm))
        if subscriber:
            checks.append(("subscriber", self.subscribers, subscriber))
        if trunk:
            checks.append(("trunk", self.trunks, trunk))
        if method in self.methods:
            checks.append((f"method:{method}", self.methods[method], source))
        if method == "REGISTER":
            checks.append(("registration", self.registration, source))
        if method == "INVITE":
            checks.append(("invite", self.invite, source))
        for dimension, limiter, key in checks:
            if not limiter.allow(key, timestamp):
                self.shed_until = max(self.shed_until, timestamp + self.recovery_seconds)
                return self._reject(dimension)
        self.allowed += 1
        self.inflight += 1
        return OverloadDecision(True)

    def release(self) -> None:
        self.inflight = max(0, self.inflight - 1)

    def _reject(self, dimension: str) -> OverloadDecision:
        self.rejects[dimension] += 1
        return OverloadDecision(False, dimension, self.retry_after)
