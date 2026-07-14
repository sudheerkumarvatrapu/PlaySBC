from __future__ import annotations

import asyncio
import secrets
import socket
from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence, Tuple
from urllib.parse import urlparse


class RtpengineError(RuntimeError):
    """Raised when RTPengine control command handling fails."""


@dataclass(frozen=True)
class RtpengineEndpoint:
    host: str
    port: int


def parse_rtpengine_url(url: str) -> RtpengineEndpoint:
    parsed = urlparse(url)
    if parsed.scheme != "udp":
        raise ValueError("rtpengine_url must use udp://host:port")
    if not parsed.hostname or not parsed.port:
        raise ValueError("rtpengine_url must include host and port")
    return RtpengineEndpoint(parsed.hostname, parsed.port)


class RtpengineClient:
    """Small RTPengine NG protocol client for offer/answer/delete commands."""

    def __init__(self, url: str = "udp://127.0.0.1:2223", timeout: float = 3.0):
        endpoint = parse_rtpengine_url(url)
        self.host = endpoint.host
        self.port = endpoint.port
        self.timeout = timeout

    def build_packet(self, command: str, fields: Dict[str, Any], cookie: Optional[str] = None) -> bytes:
        cookie = cookie or secrets.token_hex(8)
        payload = {"command": command}
        payload.update(fields)
        return cookie.encode("ascii") + b" " + bencode(normalize_bencode_value(payload))

    def decode_response(self, packet: bytes, cookie: Optional[str] = None) -> Dict[str, Any]:
        prefix, separator, payload = packet.partition(b" ")
        if not separator:
            raise RtpengineError("RTPengine response missing cookie separator")
        if cookie is not None and prefix.decode("ascii", errors="replace") != cookie:
            raise RtpengineError("RTPengine response cookie mismatch")

        decoded, position = bdecode(payload)
        if position != len(payload):
            raise RtpengineError("RTPengine response contains trailing bytes")
        if not isinstance(decoded, dict):
            raise RtpengineError("RTPengine response must be a dictionary")

        response = decode_bytes(decoded)
        result = response.get("result")
        if result == "error":
            reason = response.get("error-reason") or response.get("error") or "unknown RTPengine error"
            raise RtpengineError(str(reason))
        return response

    async def request(self, command: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        cookie = secrets.token_hex(8)
        packet = self.build_packet(command, fields, cookie=cookie)

        loop = asyncio.get_running_loop()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setblocking(False)
            await self._sendto(sock, packet, (self.host, self.port))
            deadline = loop.time() + self.timeout
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise asyncio.TimeoutError()
                response = await asyncio.wait_for(loop.sock_recv(sock, 65535), timeout=remaining)
                prefix, separator, _payload = response.partition(b" ")
                if separator and prefix.decode("ascii", errors="replace") != cookie:
                    continue
                break
        return self.decode_response(response, cookie=cookie)

    async def _sendto(self, sock: socket.socket, packet: bytes, address: Tuple[str, int]) -> None:
        while True:
            try:
                sock.sendto(packet, address)
                return
            except (BlockingIOError, InterruptedError):
                await asyncio.sleep(0)

    async def ping(self) -> Dict[str, Any]:
        return await self.request("ping", {})

    async def offer(
        self,
        *,
        call_id: str,
        from_tag: str,
        sdp: str,
        codec: Optional[Dict[str, Any]] = None,
        direction: Sequence[str] = (),
        transport_protocol: str = "",
        sdes: Sequence[str] = (),
        dtls: str = "",
    ) -> Dict[str, Any]:
        return await self.request(
            "offer",
            self._sdp_fields(
                call_id,
                from_tag,
                sdp,
                codec=codec,
                direction=direction,
                transport_protocol=transport_protocol,
                sdes=sdes,
                dtls=dtls,
            ),
        )

    async def answer(
        self,
        *,
        call_id: str,
        from_tag: str,
        to_tag: str,
        sdp: str,
        codec: Optional[Dict[str, Any]] = None,
        transport_protocol: str = "",
        sdes: Sequence[str] = (),
        dtls: str = "",
    ) -> Dict[str, Any]:
        fields = self._sdp_fields(
            call_id,
            from_tag,
            sdp,
            codec=codec,
            transport_protocol=transport_protocol,
            sdes=sdes,
            dtls=dtls,
        )
        fields["to-tag"] = to_tag
        return await self.request("answer", fields)

    async def query(self, *, call_id: str, from_tag: str = "", to_tag: str = "") -> Dict[str, Any]:
        fields: Dict[str, Any] = {"call-id": call_id}
        if from_tag:
            fields["from-tag"] = from_tag
        if to_tag:
            fields["to-tag"] = to_tag
        return await self.request("query", fields)

    async def delete(self, *, call_id: str, from_tag: str = "", to_tag: str = "") -> Dict[str, Any]:
        fields: Dict[str, Any] = {"call-id": call_id}
        if from_tag:
            fields["from-tag"] = from_tag
        if to_tag:
            fields["to-tag"] = to_tag
        return await self.request("delete", fields)

    def _sdp_fields(
        self,
        call_id: str,
        from_tag: str,
        sdp: str,
        codec: Optional[Dict[str, Any]] = None,
        direction: Sequence[str] = (),
        transport_protocol: str = "",
        sdes: Sequence[str] = (),
        dtls: str = "",
    ) -> Dict[str, Any]:
        flags = ["trust address"]
        flags.extend(f"SDES-{option}" for option in sdes)
        fields: Dict[str, Any] = {
            "call-id": call_id,
            "from-tag": from_tag,
            "sdp": sdp,
            "flags": flags,
            "replace": ["origin", "session-connection"],
        }
        if codec:
            fields["codec"] = codec
        if direction:
            if len(direction) != 2:
                raise ValueError("RTPengine direction must contain from and to interface names")
            fields["direction"] = list(direction)
        if transport_protocol:
            fields["transport protocol"] = transport_protocol
        if dtls:
            fields["DTLS"] = dtls
        return fields


def normalize_bencode_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        return {str(key): normalize_bencode_value(item) for key, item in value.items() if item is not None}
    if isinstance(value, (list, tuple)):
        return [normalize_bencode_value(item) for item in value]
    if isinstance(value, (bytes, str, int)):
        return value
    return str(value)


def bencode(value: Any) -> bytes:
    if isinstance(value, int):
        return f"i{value}e".encode("ascii")
    if isinstance(value, bytes):
        return str(len(value)).encode("ascii") + b":" + value
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        return str(len(encoded)).encode("ascii") + b":" + encoded
    if isinstance(value, list):
        return b"l" + b"".join(bencode(item) for item in value) + b"e"
    if isinstance(value, dict):
        items = []
        for key in sorted(value):
            items.append(bencode(str(key)))
            items.append(bencode(value[key]))
        return b"d" + b"".join(items) + b"e"
    raise TypeError(f"Cannot bencode {type(value).__name__}")


def bdecode(data: bytes, position: int = 0) -> Tuple[Any, int]:
    if position >= len(data):
        raise RtpengineError("Unexpected end of bencoded data")

    token = data[position : position + 1]
    if token == b"i":
        end = data.index(b"e", position)
        return int(data[position + 1 : end]), end + 1
    if token == b"l":
        position += 1
        items = []
        while data[position : position + 1] != b"e":
            item, position = bdecode(data, position)
            items.append(item)
        return items, position + 1
    if token == b"d":
        position += 1
        result = {}
        while data[position : position + 1] != b"e":
            key, position = bdecode(data, position)
            value, position = bdecode(data, position)
            result[key] = value
        return result, position + 1
    if token.isdigit():
        colon = data.index(b":", position)
        length = int(data[position:colon])
        start = colon + 1
        end = start + length
        return data[start:end], end
    raise RtpengineError(f"Invalid bencode token {token!r}")


def decode_bytes(value: Any) -> Any:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value
    if isinstance(value, list):
        return [decode_bytes(item) for item in value]
    if isinstance(value, dict):
        return {decode_bytes(key): decode_bytes(item) for key, item in value.items()}
    return value
