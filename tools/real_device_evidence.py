#!/usr/bin/env python3
"""Build clean SIP and media evidence from a real-device packet capture."""

from __future__ import annotations

import html
import json
import socket
import struct
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


SIP_METHODS = {
    "ACK",
    "BYE",
    "CANCEL",
    "INFO",
    "INVITE",
    "MESSAGE",
    "NOTIFY",
    "OPTIONS",
    "PRACK",
    "REFER",
    "REGISTER",
    "SUBSCRIBE",
    "UPDATE",
}
VOICE_PAYLOADS = {0: "PCMU", 8: "PCMA"}
RTCP_TYPES = {
    200: "SR",
    201: "RR",
    202: "SDES",
    203: "BYE",
    204: "APP",
    205: "RTPFB",
    206: "PSFB",
    207: "XR",
}
CAPTURE_MIRROR_WINDOW_SECONDS = 0.01


@dataclass(frozen=True)
class Packet:
    timestamp: float
    src_ip: str
    src_port: int
    dst_ip: str
    dst_port: int
    transport: str
    payload: bytes


@dataclass(frozen=True)
class SipEvent:
    timestamp: float
    src: str
    dst: str
    start_line: str
    call_id: str
    cseq: str
    cseq_method: str
    transport: str


@dataclass(frozen=True)
class SipMessageEvent:
    timestamp: float
    src: str
    dst: str
    transport: str
    text: str


def _network_payload(linktype: int, frame: bytes) -> bytes | None:
    if linktype == 1:  # Ethernet
        if len(frame) < 14:
            return None
        offset = 14
        ether_type = struct.unpack("!H", frame[12:14])[0]
        while ether_type in {0x8100, 0x88A8} and len(frame) >= offset + 4:
            ether_type = struct.unpack("!H", frame[offset + 2 : offset + 4])[0]
            offset += 4
        return frame[offset:] if ether_type == 0x0800 else None
    if linktype == 113:  # Linux cooked capture v1
        return frame[16:] if len(frame) >= 16 and struct.unpack("!H", frame[14:16])[0] == 0x0800 else None
    if linktype == 276:  # Linux cooked capture v2
        return frame[20:] if len(frame) >= 20 and struct.unpack("!H", frame[0:2])[0] == 0x0800 else None
    if linktype in {12, 101}:  # Raw IP
        return frame
    return None


def _decode_ipv4(timestamp: float, packet: bytes) -> Packet | None:
    if len(packet) < 20 or packet[0] >> 4 != 4:
        return None
    ihl = (packet[0] & 0x0F) * 4
    if ihl < 20 or len(packet) < ihl:
        return None
    protocol = packet[9]
    src_ip = socket.inet_ntoa(packet[12:16])
    dst_ip = socket.inet_ntoa(packet[16:20])
    segment = packet[ihl:]
    if protocol == 17 and len(segment) >= 8:
        src_port, dst_port, length = struct.unpack("!HHH", segment[:6])
        payload_end = min(len(segment), max(8, length))
        return Packet(timestamp, src_ip, src_port, dst_ip, dst_port, "udp", segment[8:payload_end])
    if protocol == 6 and len(segment) >= 20:
        src_port, dst_port = struct.unpack("!HH", segment[:4])
        data_offset = (segment[12] >> 4) * 4
        if data_offset < 20 or len(segment) < data_offset:
            return None
        return Packet(timestamp, src_ip, src_port, dst_ip, dst_port, "tcp", segment[data_offset:])
    return None


def read_pcap(path: Path) -> list[Packet]:
    raw = path.read_bytes()
    if len(raw) < 24:
        raise ValueError("capture is shorter than a classic PCAP header")
    magic = raw[:4]
    formats = {
        b"\xd4\xc3\xb2\xa1": ("<", 1_000_000),
        b"\xa1\xb2\xc3\xd4": (">", 1_000_000),
        b"\x4d\x3c\xb2\xa1": ("<", 1_000_000_000),
        b"\xa1\xb2\x3c\x4d": (">", 1_000_000_000),
    }
    if magic not in formats:
        raise ValueError("unsupported capture format; expected classic PCAP")
    endian, fraction_scale = formats[magic]
    linktype = struct.unpack(f"{endian}I", raw[20:24])[0]
    offset = 24
    packets: list[Packet] = []
    while offset + 16 <= len(raw):
        seconds, fraction, captured_length, _wire_length = struct.unpack(
            f"{endian}IIII", raw[offset : offset + 16]
        )
        offset += 16
        frame = raw[offset : offset + captured_length]
        offset += captured_length
        if len(frame) != captured_length:
            break
        network = _network_payload(linktype, frame)
        decoded = _decode_ipv4(seconds + (fraction / fraction_scale), network or b"")
        if decoded:
            packets.append(decoded)
    return packets


def _sip_messages(payload: bytes) -> Iterable[bytes]:
    if not payload:
        return ()
    text = payload.decode("ISO-8859-1", "replace")
    messages: list[bytes] = []
    cursor = 0
    while cursor < len(text):
        boundary = text.find("\r\n\r\n", cursor)
        separator = 4
        if boundary < 0:
            boundary = text.find("\n\n", cursor)
            separator = 2
        if boundary < 0:
            candidate = text[cursor:].strip("\x00\r\n")
            if candidate:
                messages.append(candidate.encode("ISO-8859-1", "replace"))
            break
        headers = text[cursor:boundary]
        content_length = 0
        for line in headers.replace("\r\n", "\n").split("\n"):
            if line.lower().startswith(("content-length:", "l:")):
                try:
                    content_length = int(line.split(":", 1)[1].strip())
                except ValueError:
                    content_length = 0
        end = min(len(text), boundary + separator + max(content_length, 0))
        candidate = text[cursor:end].strip("\x00\r\n")
        if candidate:
            messages.append(candidate.encode("ISO-8859-1", "replace"))
        cursor = end
        while cursor < len(text) and text[cursor] in "\x00\r\n":
            cursor += 1
    return messages


def _header(text: str, name: str) -> str:
    target = name.lower()
    for line in text.replace("\r\n", "\n").split("\n")[1:]:
        if ":" in line and line.split(":", 1)[0].strip().lower() == target:
            return line.split(":", 1)[1].strip()
    return ""


def sip_message_events(packets: Iterable[Packet]) -> list[SipMessageEvent]:
    """Return complete SIP messages retained in packet payloads."""
    messages: list[SipMessageEvent] = []
    for packet in packets:
        for raw_message in _sip_messages(packet.payload):
            text = raw_message.decode("ISO-8859-1", "replace")
            start_line = text.replace("\r\n", "\n").split("\n", 1)[0].strip()
            first_token = start_line.split(" ", 1)[0].upper() if start_line else ""
            if first_token not in SIP_METHODS and first_token != "SIP/2.0":
                continue
            messages.append(
                SipMessageEvent(
                    timestamp=packet.timestamp,
                    src=f"{packet.src_ip}:{packet.src_port}",
                    dst=f"{packet.dst_ip}:{packet.dst_port}",
                    transport=packet.transport.upper(),
                    text=text.rstrip("\x00\r\n"),
                )
            )
    return sorted(messages, key=lambda item: item.timestamp)


def sip_events(packets: Iterable[Packet]) -> list[SipEvent]:
    events: list[SipEvent] = []
    for message in sip_message_events(packets):
        text = message.text
        start_line = text.replace("\r\n", "\n").split("\n", 1)[0].strip()
        first_token = start_line.split(" ", 1)[0].upper() if start_line else ""
        cseq = _header(text, "CSeq")
        cseq_parts = cseq.split()
        events.append(
            SipEvent(
                timestamp=message.timestamp,
                src=message.src,
                dst=message.dst,
                start_line=start_line,
                call_id=_header(text, "Call-ID") or _header(text, "i") or "unknown",
                cseq=cseq,
                cseq_method=cseq_parts[-1].upper() if cseq_parts else first_token,
                transport=message.transport,
            )
        )
    return sorted(events, key=lambda item: item.timestamp)


def canonicalize_sip(events: Iterable[SipEvent]) -> dict[str, object]:
    ordered = list(events)
    first_timestamp = min((event.timestamp for event in ordered), default=0.0)
    unique: list[dict[str, object]] = []
    duplicates: list[dict[str, object]] = []
    signatures: dict[tuple[str, str, str, str], dict[str, object]] = {}
    ack_times: dict[str, float] = {}
    capture_mirror_packets = 0
    for event in ordered:
        # Combined host captures observe a datagram at both the public-LB and
        # pod-facing interfaces. The B2BUA call ID keeps the two SIP legs
        # distinct; the timestamp window collapses only interface mirrors.
        signature = (event.call_id, event.cseq, event.start_line, event.transport)
        if signature in signatures:
            state = signatures[signature]
            if event.timestamp - float(state["last_occurrence_timestamp"]) <= CAPTURE_MIRROR_WINDOW_SECONDS:
                capture_mirror_packets += 1
                continue
            item = duplicates[int(state["duplicate_index"])]
            item["count"] = int(item["count"]) + 1
            item["last_offset_seconds"] = round(event.timestamp - first_timestamp, 6)
            state["last_occurrence_timestamp"] = event.timestamp
            continue
        signatures[signature] = {
            "duplicate_index": len(duplicates),
            "last_occurrence_timestamp": event.timestamp,
        }
        duplicate = {
            "call_id": event.call_id,
            "cseq": event.cseq,
            "message": event.start_line,
            "src": event.src,
            "dst": event.dst,
            "count": 0,
            "first_offset_seconds": round(event.timestamp - first_timestamp, 6),
            "last_offset_seconds": round(event.timestamp - first_timestamp, 6),
            "classification": "sip_udp_retransmission" if event.transport == "UDP" else "duplicate_capture",
        }
        duplicates.append(duplicate)
        canonical_event = asdict(event)
        canonical_event["message"] = canonical_event.pop("start_line")
        canonical_event["offset_seconds"] = round(event.timestamp - first_timestamp, 6)
        unique.append(canonical_event)
        if event.start_line.startswith("ACK "):
            ack_times[event.call_id] = event.timestamp
    retransmissions = []
    for item in duplicates:
        if int(item["count"]) <= 0:
            continue
        if (
            str(item["message"]).startswith("SIP/2.0 200")
            and str(item["cseq"]).upper().endswith(" INVITE")
            and ack_times.get(str(item["call_id"]), float("inf")) <= first_timestamp + float(item["last_offset_seconds"])
        ):
            item["classification"] = "expected_200_ok_retransmission_after_ack"
        retransmissions.append(item)
    return {
        "captured_events": len(ordered),
        "canonical_events": len(unique),
        "capture_mirror_packets": capture_mirror_packets,
        "retransmitted_packets": sum(int(item["count"]) for item in retransmissions),
        "events": unique,
        "retransmissions": retransmissions,
    }


def _rtp_header(payload: bytes) -> tuple[int, int] | None:
    if len(payload) < 12 or payload[0] >> 6 != 2:
        return None
    csrc_count = payload[0] & 0x0F
    offset = 12 + (4 * csrc_count)
    if payload[0] & 0x10:
        if len(payload) < offset + 4:
            return None
        extension_words = struct.unpack("!H", payload[offset + 2 : offset + 4])[0]
        offset += 4 + (extension_words * 4)
    return payload[1] & 0x7F, offset


def classify_media(packets: Iterable[Packet], sip: dict[str, object], rtp_min: int, rtp_max: int) -> dict[str, object]:
    answer_times = [
        float(item["timestamp"])
        for item in sip.get("events", [])
        if str(item.get("message", "")).startswith("SIP/2.0 200")
        and str(item.get("cseq", "")).upper().endswith(" INVITE")
    ]
    answer_time = min(answer_times, default=None)
    counters: Counter[str] = Counter()
    bytes_by_class: Counter[str] = Counter()
    flows: dict[tuple[str, str, str, str], dict[str, object]] = {}
    rtcp_types: Counter[str] = Counter()
    for packet in packets:
        if packet.transport != "udp" or packet.src_port == 2223 or packet.dst_port == 2223:
            continue
        if not (rtp_min <= packet.src_port <= rtp_max or rtp_min <= packet.dst_port <= rtp_max):
            continue
        payload = packet.payload
        category = "unknown_udp_media"
        codec = "unknown"
        if len(payload) >= 4 and payload[0] >> 6 == 2 and 192 <= payload[1] <= 223:
            category = "rtcp"
            codec = RTCP_TYPES.get(payload[1], f"PT{payload[1]}")
            rtcp_types[codec] += 1
        else:
            header = _rtp_header(payload)
            if not header:
                continue
            payload_type, header_length = header
            media_bytes = max(0, len(payload) - header_length)
            codec = VOICE_PAYLOADS.get(payload_type, f"PT{payload_type}")
            if payload_type in {101, 110}:
                category = "telephone_event_rtp"
            elif media_bytes <= 4:
                category = "nat_probe_rtp"
            elif payload_type in VOICE_PAYLOADS and media_bytes >= 20:
                category = "voice_rtp"
            else:
                category = "other_rtp"
        phase = "pre_answer" if answer_time is not None and packet.timestamp < answer_time else "established_or_unknown"
        counters[category] += 1
        counters[f"{category}_{phase}"] += 1
        bytes_by_class[category] += len(payload)
        key = (
            f"{packet.src_ip}:{packet.src_port}",
            f"{packet.dst_ip}:{packet.dst_port}",
            category,
            codec,
        )
        flow = flows.setdefault(
            key,
            {
                "src": key[0],
                "dst": key[1],
                "classification": category,
                "codec_or_type": codec,
                "packets": 0,
                "bytes": 0,
                "pre_answer_packets": 0,
            },
        )
        flow["packets"] = int(flow["packets"]) + 1
        flow["bytes"] = int(flow["bytes"]) + len(payload)
        if phase == "pre_answer":
            flow["pre_answer_packets"] = int(flow["pre_answer_packets"]) + 1
    voice_flows = [flow for flow in flows.values() if flow["classification"] == "voice_rtp"]
    rtcp_flows = [flow for flow in flows.values() if flow["classification"] == "rtcp"]
    reverse_voice = any(
        left["src"] == right["dst"] and left["dst"] == right["src"]
        for left in voice_flows
        for right in voice_flows
        if left is not right
    )
    reverse_rtcp = any(
        left["src"] == right["dst"] and left["dst"] == right["src"]
        for left in rtcp_flows
        for right in rtcp_flows
        if left is not right
    )
    return {
        "packet_counts": dict(sorted(counters.items())),
        "byte_counts": dict(sorted(bytes_by_class.items())),
        "rtcp_types": dict(sorted(rtcp_types.items())),
        "voice_flow_count": len(voice_flows),
        "rtcp_flow_count": len(rtcp_flows),
        "pcap_bidirectional_voice_pair": reverse_voice,
        "pcap_bidirectional_rtcp_pair": reverse_rtcp,
        "rtcp_status": "bidirectional" if reverse_rtcp else ("endpoint-limited" if rtcp_flows else "not-observed"),
        "flows": sorted(flows.values(), key=lambda item: (str(item["classification"]), str(item["src"]), str(item["dst"]))),
    }


def _rtpengine_bidirectional(verdict_path: Path) -> bool:
    if not verdict_path.exists():
        return False
    text = verdict_path.read_text(encoding="utf-8", errors="replace")
    return "caller_to_callee=observed" in text and "callee_to_caller=observed" in text


def _canonical_text(sip: dict[str, object]) -> str:
    lines = [
        "PLAY SBC REAL DEVICE CANONICAL SIP FLOW",
        f"capture_mirror_packets_collapsed={sip['capture_mirror_packets']}",
    ]
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for event in sip["events"]:
        grouped[str(event["call_id"])].append(event)
    for call_id, events in grouped.items():
        lines.extend(["", f"Call-ID: {call_id}"])
        for event in events:
            lines.append(
                f"{float(event['offset_seconds']):9.3f}s  {event['src']} -> {event['dst']}  "
                f"{event['message']}  [{event['transport']}]"
            )
    lines.extend(["", "RETRANSMISSION ANNOTATIONS"])
    retransmissions = sip["retransmissions"]
    if not retransmissions:
        lines.append("none")
    for item in retransmissions:
        lines.append(
            f"{item['classification']}: repeats={item['count']} call_id={item['call_id']} "
            f"cseq={item['cseq']} message={item['message']}"
        )
    return "\n".join(lines) + "\n"


def _media_text(media: dict[str, object]) -> str:
    counts = media["packet_counts"]
    lines = [
        "PLAY SBC REAL DEVICE MEDIA EVIDENCE",
        f"voice_rtp_packets={counts.get('voice_rtp', 0)}",
        f"pre_answer_voice_rtp_packets={counts.get('voice_rtp_pre_answer', 0)}",
        f"nat_probe_rtp_packets={counts.get('nat_probe_rtp', 0)}",
        f"rtcp_packets={counts.get('rtcp', 0)}",
        f"rtcp_status={media['rtcp_status']}",
        f"pcap_bidirectional_voice_pair={str(media['pcap_bidirectional_voice_pair']).lower()}",
        f"rtpengine_bidirectional_verdict={str(media['rtpengine_bidirectional_verdict']).lower()}",
        f"bidirectional_rtp_proven={str(media['bidirectional_rtp_proven']).lower()}",
        "",
        "MEDIA FLOWS",
    ]
    for flow in media["flows"]:
        lines.append(
            f"{flow['classification']} {flow['codec_or_type']} {flow['src']} -> {flow['dst']} "
            f"packets={flow['packets']} bytes={flow['bytes']} pre_answer={flow['pre_answer_packets']}"
        )
    return "\n".join(lines) + "\n"


def _render_html(sip: dict[str, object], media: dict[str, object], canonical_text: str, media_text: str) -> str:
    retransmission_count = int(sip["retransmitted_packets"])
    mirror_count = int(sip["capture_mirror_packets"])
    voice_packets = int(media["packet_counts"].get("voice_rtp", 0))
    rtcp_packets = int(media["packet_counts"].get("rtcp", 0))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PlaySBC Real Device Evidence</title><style>
body{{font-family:system-ui,sans-serif;margin:0;background:#f4f6f8;color:#18202a}}main{{max-width:1120px;margin:auto;padding:28px}}
h1{{font-size:28px}}.summary{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}}
.metric{{background:white;border:1px solid #dce2e8;border-radius:6px;padding:14px}}.metric b{{display:block;font-size:24px;color:#126e55}}
section{{margin-top:18px;background:white;border:1px solid #dce2e8;padding:18px}}pre{{overflow:auto;white-space:pre-wrap;font-size:12px;line-height:1.45}}
</style></head><body><main><h1>PlaySBC Real Device Evidence</h1>
<div class="summary"><div class="metric"><span>Canonical SIP events</span><b>{sip['canonical_events']}</b></div>
<div class="metric"><span>Capture mirrors collapsed</span><b>{mirror_count}</b></div>
<div class="metric"><span>Retransmitted packets</span><b>{retransmission_count}</b></div>
<div class="metric"><span>G.711 voice RTP</span><b>{voice_packets}</b></div>
<div class="metric"><span>RTCP packets</span><b>{rtcp_packets}</b></div>
<div class="metric"><span>Two-way RTP proven</span><b>{'YES' if media['bidirectional_rtp_proven'] else 'NO'}</b></div>
<div class="metric"><span>RTCP status</span><b>{html.escape(str(media['rtcp_status']).upper())}</b></div></div>
<section><h2>Canonical SIP Flow</h2><pre>{html.escape(canonical_text)}</pre></section>
<section><h2>Media Classification</h2><pre>{html.escape(media_text)}</pre></section>
</main></body></html>"""


def write_evidence_bundle(bundle: Path, *, rtp_min: int = 30000, rtp_max: int = 30049) -> dict[str, object]:
    capture = bundle / "capture.pcap"
    packets = read_pcap(capture)
    sip = canonicalize_sip(sip_events(packets))
    media = classify_media(packets, sip, rtp_min, rtp_max)
    media["rtpengine_bidirectional_verdict"] = _rtpengine_bidirectional(bundle / "rtpengine-verdict.log")
    media["bidirectional_rtp_proven"] = bool(
        media["packet_counts"].get("voice_rtp", 0)
        and (media["pcap_bidirectional_voice_pair"] or media["rtpengine_bidirectional_verdict"])
    )
    canonical_text = _canonical_text(sip)
    media_text = _media_text(media)
    # sipmsg.log is the human evidence view. Raw packets remain authoritative in capture.pcap.
    (bundle / "sipmsg.log").write_text(canonical_text, encoding="utf-8")
    (bundle / "canonical-sip.json").write_text(json.dumps(sip, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (bundle / "media-evidence.log").write_text(media_text, encoding="utf-8")
    (bundle / "media-evidence.json").write_text(json.dumps(media, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (bundle / "latest.html").write_text(_render_html(sip, media, canonical_text, media_text), encoding="utf-8")
    return {"sip": sip, "media": media}
