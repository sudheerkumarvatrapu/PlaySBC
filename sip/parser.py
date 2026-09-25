from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Dict, Iterable, Tuple


CRLF = b"\r\n"
HEADER_SEPARATOR = CRLF + CRLF
TOKEN = rb"[!#$%&'*+\-.^_`|~0-9A-Za-z]+"
REQUEST_LINE = re.compile(rb"^(" + TOKEN + rb") ([^ ]+) SIP/2\.0$")
STATUS_LINE = re.compile(rb"^SIP/2\.0 ([1-6][0-9]{2})(?: (.*))?$")
HEADER_LINE = re.compile(rb"^(" + TOKEN + rb"):[ \t]*(.*)$")
URI_TOKEN = re.compile(r"^[!#$%&'*+\-.0-9A-Z_a-z~]+$")
URI_USERINFO = re.compile(r"^(?:[A-Za-z0-9\-_.!~*'()&=+$,;?/]|%[0-9A-Fa-f]{2}|:)+$")
URI_COMPONENT = re.compile(r"^(?:[A-Za-z0-9\-_.!~*'()\[\]/:&+$]|%[0-9A-Fa-f]{2})*$")
HOST_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")

COMPACT_HEADERS = {
    "i": "call-id",
    "f": "from",
    "t": "to",
    "v": "via",
    "m": "contact",
    "l": "content-length",
    "c": "content-type",
    "s": "subject",
    "k": "supported",
}
SINGLETON_HEADERS = frozenset(
    {
        "call-id",
        "from",
        "to",
        "cseq",
        "max-forwards",
        "content-length",
    }
)
MANDATORY_HEADERS = frozenset({"via", "from", "to", "call-id", "cseq"})


class SipParseError(ValueError):
    """A deterministic SIP syntax or framing rejection."""

    def __init__(self, detail: str, *, status: int = 400, reason: str = "Bad Request") -> None:
        super().__init__(detail)
        self.status = status
        self.reason = reason


@dataclass(frozen=True)
class SipParseLimits:
    max_message_bytes: int = 65_535
    max_header_bytes: int = 16_384
    max_header_count: int = 100
    max_line_bytes: int = 4_096


@dataclass(frozen=True)
class ParsedSipUri:
    scheme: str
    userinfo: str
    host: str
    port: int | None
    parameters: Tuple[Tuple[str, str | None], ...]
    headers: Tuple[Tuple[str, str], ...]


@dataclass(frozen=True)
class MimePart:
    headers: Dict[str, str]
    body: bytes


@dataclass(frozen=True)
class ParsedSipMessage:
    start_line: str
    headers: Dict[str, Tuple[str, ...]]
    body: bytes
    method: str = ""
    request_uri: str = ""
    status_code: int = 0
    reason_phrase: str = ""

    @property
    def is_response(self) -> bool:
        return self.status_code != 0

    def header(self, name: str, default: str = "") -> str:
        values = self.headers.get(normalize_header_name(name))
        return ", ".join(values) if values else default

    def header_values(self, name: str) -> Tuple[str, ...]:
        return self.headers.get(normalize_header_name(name), ())

    def comma_values(self, name: str) -> Tuple[str, ...]:
        return tuple(
            item
            for value in self.header_values(name)
            for item in split_quoted(value, ",")
        )


def split_quoted(value: str, delimiter: str = ",") -> Tuple[str, ...]:
    """Split a SIP list without treating delimiters in quotes or angle URIs as separators."""
    parts: list[str] = []
    start = 0
    quoted = escaped = False
    angle_depth = 0
    for index, character in enumerate(value):
        if escaped:
            escaped = False
            continue
        if quoted and character == "\\":
            escaped = True
        elif character == '"':
            quoted = not quoted
        elif not quoted and character == "<":
            angle_depth += 1
        elif not quoted and character == ">":
            angle_depth -= 1
            if angle_depth < 0:
                raise SipParseError("Unbalanced angle bracket in SIP header")
        elif not quoted and not angle_depth and character == delimiter:
            item = value[start:index].strip()
            if not item:
                raise SipParseError("Empty value in SIP header list")
            parts.append(item)
            start = index + 1
    if quoted or escaped or angle_depth:
        raise SipParseError("Unterminated quoted string or URI in SIP header")
    item = value[start:].strip()
    if not item:
        raise SipParseError("Empty value in SIP header list")
    parts.append(item)
    return tuple(parts)


def parse_parameters(value: str) -> Tuple[str, Tuple[Tuple[str, str | None], ...]]:
    """Parse a semicolon parameter list, preserving quoted delimiters and escapes."""
    pieces = split_quoted(value, ";")
    base = pieces[0]
    parameters: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for piece in pieces[1:]:
        name, equals, raw = piece.partition("=")
        name = name.strip().lower()
        if not URI_TOKEN.fullmatch(name) or name in seen:
            raise SipParseError("Malformed or duplicate SIP header parameter")
        raw = raw.strip()
        if equals and raw.startswith('"'):
            if len(raw) < 2 or not raw.endswith('"'):
                raise SipParseError("Unterminated quoted SIP header parameter")
            raw = re.sub(r"\\(.)", r"\1", raw[1:-1])
        elif equals and (not raw or not URI_COMPONENT.fullmatch(raw)):
            raise SipParseError("Malformed SIP header parameter value")
        parameters.append((name, raw if equals else None))
        seen.add(name)
    return base, tuple(parameters)


def parse_multipart_body(body: bytes, content_type: str) -> Tuple[MimePart, ...]:
    media_type, parameters = parse_parameters(content_type)
    if not media_type.strip().lower().startswith("multipart/"):
        raise SipParseError("Content-Type is not multipart")
    boundary = dict(parameters).get("boundary")
    if not boundary or len(boundary) > 70 or any(ord(char) < 32 for char in boundary):
        raise SipParseError("Multipart boundary is missing or invalid")
    delimiter = b"--" + boundary.encode("ascii")
    closing = delimiter + b"--"
    if closing not in body:
        raise SipParseError("Multipart body has no closing boundary")
    parts: list[MimePart] = []
    for section in body.split(delimiter)[1:]:
        if section.startswith(b"--"):
            break
        section = section.removeprefix(CRLF).removesuffix(CRLF)
        header_end = section.find(HEADER_SEPARATOR)
        if header_end < 0:
            raise SipParseError("Multipart part has no header terminator")
        part_headers: Dict[str, str] = {}
        for line in section[:header_end].split(CRLF):
            match = HEADER_LINE.fullmatch(line)
            if not match:
                raise SipParseError("Malformed multipart header")
            name = normalize_header_name(match.group(1).decode("ascii"))
            part_headers[name] = match.group(2).decode("utf-8").strip()
        parts.append(MimePart(part_headers, section[header_end + 4 :]))
    if not parts:
        raise SipParseError("Multipart body contains no parts")
    return tuple(parts)


def normalize_header_name(name: str) -> str:
    lowered = name.lower()
    return COMPACT_HEADERS.get(lowered, lowered)


def _validate_uri_component(value: str, label: str, *, allow_empty: bool = True) -> None:
    if (not allow_empty and not value) or not URI_COMPONENT.fullmatch(value):
        raise SipParseError(f"Malformed SIP URI {label}")


def _validate_uri_host(host: str, *, bracketed: bool) -> str:
    if not host:
        raise SipParseError("SIP URI host is empty")
    if bracketed:
        try:
            return str(ipaddress.IPv6Address(host))
        except ValueError as exc:
            raise SipParseError("Malformed SIP URI IPv6 literal") from exc
    if ":" in host:
        raise SipParseError("SIP URI IPv6 literal must be enclosed in brackets")
    try:
        ipaddress.IPv4Address(host)
        return host
    except ValueError:
        pass
    candidate = host[:-1] if host.endswith(".") else host
    labels = candidate.split(".")
    if (
        not candidate
        or any(not HOST_LABEL.fullmatch(label) for label in labels)
        or not labels[-1][0].isalpha()
    ):
        raise SipParseError("Malformed SIP URI host")
    return host


def parse_sip_uri(value: str) -> ParsedSipUri:
    """Parse and validate an RFC 3261 SIP/SIPS URI used as a Request-URI."""
    if not value or any(ord(char) < 0x21 or ord(char) > 0x7E for char in value):
        raise SipParseError("SIP URI contains whitespace, control, or non-ASCII characters")
    scheme_separator = value.find(":")
    if scheme_separator < 1:
        raise SipParseError("SIP URI has no scheme")
    scheme = value[:scheme_separator].lower()
    if scheme not in {"sip", "sips"}:
        raise SipParseError("Unsupported Request-URI scheme", status=416, reason="Unsupported URI Scheme")
    remainder = value[scheme_separator + 1 :]

    address, question, header_text = remainder.partition("?")
    if question and not header_text:
        raise SipParseError("Malformed SIP URI header component")
    at = address.rfind("@")
    userinfo = ""
    host_and_parameters = address
    if at >= 0:
        userinfo = address[:at]
        host_and_parameters = address[at + 1 :]
        if not userinfo or not URI_USERINFO.fullmatch(userinfo):
            raise SipParseError("Malformed SIP URI userinfo")

    host_port, *raw_parameters = host_and_parameters.split(";")
    bracketed = host_port.startswith("[")
    port_text = ""
    if bracketed:
        close = host_port.find("]")
        if close < 0:
            raise SipParseError("Malformed SIP URI IPv6 literal")
        host = host_port[1:close]
        suffix = host_port[close + 1 :]
        if suffix:
            if not suffix.startswith(":"):
                raise SipParseError("Malformed SIP URI host/port")
            port_text = suffix[1:]
    else:
        if host_port.count(":") > 1:
            raise SipParseError("SIP URI IPv6 literal must be enclosed in brackets")
        host, separator, port_text = host_port.partition(":")
        if not separator:
            port_text = ""
    host = _validate_uri_host(host, bracketed=bracketed)

    port: int | None = None
    if port_text:
        if not port_text.isdigit() or not 1 <= int(port_text) <= 65_535:
            raise SipParseError("SIP URI port is outside 1..65535")
        port = int(port_text)
    elif host_port.endswith(":"):
        raise SipParseError("SIP URI port is empty")

    parameters: list[tuple[str, str | None]] = []
    seen_parameters: set[str] = set()
    for raw_parameter in raw_parameters:
        name, equals, parameter_value = raw_parameter.partition("=")
        lowered = name.lower()
        if not name or not URI_TOKEN.fullmatch(name) or lowered in seen_parameters:
            raise SipParseError("Malformed or duplicate SIP URI parameter")
        if equals:
            _validate_uri_component(parameter_value, "parameter", allow_empty=False)
        seen_parameters.add(lowered)
        parameters.append((lowered, parameter_value if equals else None))

    uri_headers: list[tuple[str, str]] = []
    if question:
        for raw_header in header_text.split("&"):
            name, equals, header_value = raw_header.partition("=")
            if not equals or not name:
                raise SipParseError("Malformed SIP URI header component")
            _validate_uri_component(name, "header name")
            _validate_uri_component(header_value, "header value")
            uri_headers.append((name, header_value))

    return ParsedSipUri(
        scheme=scheme,
        userinfo=userinfo,
        host=host,
        port=port,
        parameters=tuple(parameters),
        headers=tuple(uri_headers),
    )


def parse_sip_bytes(
    data: bytes,
    *,
    limits: SipParseLimits = SipParseLimits(),
    require_mandatory_headers: bool = True,
    allowed_content_types: Iterable[str] | None = None,
) -> ParsedSipMessage:
    if len(data) > limits.max_message_bytes:
        raise SipParseError("SIP message exceeds configured size limit", status=513, reason="Message Too Large")
    separator_at = data.find(HEADER_SEPARATOR)
    if separator_at < 0:
        raise SipParseError("SIP message has no CRLF header terminator")
    if separator_at > limits.max_header_bytes:
        raise SipParseError("SIP header section exceeds configured size limit", status=513, reason="Message Too Large")

    head = data[:separator_at]
    if b"\x00" in head:
        raise SipParseError("SIP header section contains a NUL byte")
    body = data[separator_at + len(HEADER_SEPARATOR) :]
    raw_lines = head.split(CRLF)
    if not raw_lines or not raw_lines[0]:
        raise SipParseError("SIP start line is empty")
    if len(raw_lines[0]) > limits.max_line_bytes:
        raise SipParseError("SIP Request-URI/start line exceeds configured size limit", status=414, reason="Request-URI Too Long")
    if any(len(line) > limits.max_line_bytes for line in raw_lines[1:]):
        raise SipParseError("SIP line exceeds configured size limit", status=513, reason="Message Too Large")

    start_line_bytes = raw_lines[0]
    request_match = REQUEST_LINE.fullmatch(start_line_bytes)
    status_match = STATUS_LINE.fullmatch(start_line_bytes)
    if not request_match and not status_match:
        raise SipParseError("Malformed SIP request/status line")

    unfolded: list[bytes] = []
    for line in raw_lines[1:]:
        if line.startswith((b" ", b"\t")):
            if not unfolded:
                raise SipParseError("Header continuation has no preceding header")
            unfolded[-1] += b" " + line.strip()
        else:
            unfolded.append(line)
    if len(unfolded) > limits.max_header_count:
        raise SipParseError("SIP header count exceeds configured limit", status=513, reason="Message Too Large")

    headers: Dict[str, list[str]] = {}
    for raw_line in unfolded:
        match = HEADER_LINE.fullmatch(raw_line)
        if not match:
            raise SipParseError("Malformed SIP header line")
        try:
            name = normalize_header_name(match.group(1).decode("ascii"))
            value = match.group(2).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SipParseError("SIP header is not valid UTF-8") from exc
        if name in SINGLETON_HEADERS and name in headers:
            raise SipParseError(f"Duplicate singleton header: {name}")
        headers.setdefault(name, []).append(value.strip())

    if require_mandatory_headers:
        missing = sorted(MANDATORY_HEADERS - headers.keys())
        if missing:
            raise SipParseError(f"Missing mandatory SIP headers: {', '.join(missing)}")

    content_lengths = headers.get("content-length", [])
    if content_lengths:
        value = content_lengths[0]
        if not value.isdigit():
            raise SipParseError("Content-Length is not an unsigned decimal integer")
        declared_length = int(value)
        if declared_length != len(body):
            raise SipParseError(
                f"Content-Length mismatch: declared {declared_length}, received {len(body)}"
            )
    elif body:
        raise SipParseError("SIP message body has no Content-Length header")

    max_forwards = headers.get("max-forwards", [])
    if max_forwards:
        if not max_forwards[0].isdigit() or int(max_forwards[0]) > 255:
            raise SipParseError("Max-Forwards is not an integer in 0..255")
        if int(max_forwards[0]) == 0:
            raise SipParseError("Max-Forwards exhausted", status=483, reason="Too Many Hops")

    content_type = headers.get("content-type", [])
    if body and not content_type:
        raise SipParseError("SIP message body has no Content-Type header", status=415, reason="Unsupported Media Type")
    if content_type:
        media_type, _parameters = parse_parameters(content_type[0])
        normalized_media_type = media_type.strip().lower()
        if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9a-z-]+/[!#$%&'*+.^_`|~0-9a-z-]+", normalized_media_type):
            raise SipParseError("Malformed Content-Type header")
        if allowed_content_types is not None and normalized_media_type not in {
            item.lower() for item in allowed_content_types
        }:
            raise SipParseError("Unsupported SIP message body type", status=415, reason="Unsupported Media Type")

    try:
        start_line = start_line_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SipParseError("SIP start line is not valid UTF-8") from exc

    method = ""
    request_uri = ""
    status_code = 0
    reason_phrase = ""
    if request_match:
        method = request_match.group(1).decode("ascii").upper()
        request_uri = request_match.group(2).decode("utf-8")
        if request_uri == "*":
            if method != "OPTIONS":
                raise SipParseError("Asterisk Request-URI is only valid for OPTIONS")
        else:
            parse_sip_uri(request_uri)
        cseq_method = headers.get("cseq", ("",))[0].split()
        if len(cseq_method) != 2 or cseq_method[1].upper() != method:
            raise SipParseError("Request method does not match CSeq method")
    else:
        status_code = int(status_match.group(1))
        reason_phrase = (status_match.group(2) or b"").decode("utf-8")

    return ParsedSipMessage(
        start_line=start_line,
        headers={name: tuple(values) for name, values in headers.items()},
        body=body,
        method=method,
        request_uri=request_uri,
        status_code=status_code,
        reason_phrase=reason_phrase,
    )
