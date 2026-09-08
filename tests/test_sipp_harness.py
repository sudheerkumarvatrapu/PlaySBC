import base64
import copy
import hashlib
import hmac
import inspect
import io
import json
import socket
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import mini_call_server as server
from tools import run_sipp_regression
from tools import run_b2bua_sipp_smoke
from tools import run_regression_suite
from tools import run_real_topology
from tools import run_dual_realm_profile
from tools import run_k8s_regression
from tools import run_k8s_regression_job
from tools import run_real_device_capture
from tools import real_device_evidence
from tools import send_srtp_audio
from tools import check_kind_real_device_lab
from tools import serve_regression_report


ROOT = Path(__file__).resolve().parents[1]


def write_test_pcap(path: Path, timestamp: float, payload: bytes, linktype: int = 1):
    path.write_bytes(write_test_pcap_bytes(timestamp, payload, linktype=linktype))


def write_test_pcap_bytes(timestamp: float, payload: bytes, linktype: int = 1) -> bytes:
    seconds = int(timestamp)
    microseconds = int((timestamp - seconds) * 1_000_000)
    return (
        struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, linktype)
        + struct.pack("<IIII", seconds, microseconds, len(payload), len(payload))
        + payload
    )


def read_udp_pcap_packets(path: Path):
    return [(src, dst, payload) for _timestamp, src, dst, payload in read_udp_pcap_records(path)]


def read_udp_pcap_records(path: Path):
    return [(timestamp, src_port, dst_port, payload) for timestamp, _src_ip, src_port, _dst_ip, dst_port, payload in read_udp_pcap_flow_records(path)]


def read_udp_pcap_flow_records(path: Path):
    return [
        (timestamp, src_ip, src_port, dst_ip, dst_port, payload)
        for timestamp, protocol, src_ip, src_port, dst_ip, dst_port, payload in read_ip_pcap_flow_records(path)
        if protocol == 17
    ]


def read_ip_pcap_flow_records(path: Path):
    data = path.read_bytes()
    packets = []
    offset = 24
    while offset + 16 <= len(data):
        ts_sec, ts_usec, included_length, _original_length = struct.unpack("<IIII", data[offset : offset + 16])
        offset += 16
        frame = data[offset : offset + included_length]
        offset += included_length
        if len(frame) < 34:
            continue
        ip_header_length = (frame[14] & 0x0F) * 4
        ip_protocol = frame[23]
        l4_offset = 14 + ip_header_length
        if len(frame) < l4_offset + 4:
            continue
        src_ip = socket.inet_ntoa(frame[26:30])
        dst_ip = socket.inet_ntoa(frame[30:34])
        src_port, dst_port = struct.unpack("!HH", frame[l4_offset : l4_offset + 4])
        if ip_protocol == 6:
            if len(frame) < l4_offset + 20:
                continue
            l4_header_length = ((frame[l4_offset + 12] >> 4) & 0x0F) * 4
        elif ip_protocol == 17:
            if len(frame) < l4_offset + 8:
                continue
            l4_header_length = 8
        else:
            continue
        packets.append(
            (
                ts_sec + (ts_usec / 1_000_000),
                ip_protocol,
                src_ip,
                src_port,
                dst_ip,
                dst_port,
                frame[l4_offset + l4_header_length :],
            )
        )
    return packets


def read_tcp_pcap_records(path: Path):
    data = path.read_bytes()
    packets = []
    offset = 24
    while offset + 16 <= len(data):
        ts_sec, ts_usec, included_length, _original_length = struct.unpack("<IIII", data[offset : offset + 16])
        offset += 16
        frame = data[offset : offset + included_length]
        offset += included_length
        if len(frame) < 54 or frame[23] != 6:
            continue
        ip_header_length = (frame[14] & 0x0F) * 4
        tcp_offset = 14 + ip_header_length
        tcp_header_length = ((frame[tcp_offset + 12] >> 4) & 0x0F) * 4
        src_port, dst_port, sequence, acknowledgment = struct.unpack("!HHII", frame[tcp_offset : tcp_offset + 12])
        packets.append(
            (
                ts_sec + (ts_usec / 1_000_000),
                socket.inet_ntoa(frame[26:30]),
                src_port,
                socket.inet_ntoa(frame[30:34]),
                dst_port,
                sequence,
                acknowledgment,
                frame[tcp_offset + 13],
                frame[tcp_offset + tcp_header_length :],
            )
        )
    return packets


def sip_body(payload: bytes) -> bytes:
    _headers, separator, body = payload.partition(b"\r\n\r\n")
    return body if separator else b""


class SippScenarioTests(unittest.TestCase):
    def test_sipp_trace_parser_accepts_debian_and_macos_byte_formats(self):
        trace = """----------------------------------------------- 2026-07-04 04:36:46.320000
UDP message sent (100 bytes):

REGISTER sip:192.168.28.20:5060 SIP/2.0
Content-Length: 0

----------------------------------------------- 2026-07-04T04:36:46.324000
TLS message received [80] bytes :

SIP/2.0 401 Unauthorized
Content-Length: 0

---------- 2026-07-04T04:36:46.326000Z
UDP message sent [90] bytes:

OPTIONS sip:192.168.28.20:5060 SIP/2.0
Content-Length: 0

"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "messages.log"
            path.write_text(trace, encoding="utf-8")
            messages = run_b2bua_sipp_smoke.sipp_trace_messages(path)

        self.assertEqual([message[1] for message in messages], ["sent", "received", "sent"])
        self.assertTrue(messages[0][2].startswith(b"REGISTER "))
        self.assertTrue(messages[1][2].startswith(b"SIP/2.0 401"))
        self.assertTrue(messages[2][2].startswith(b"OPTIONS "))

    def test_ordered_sip_trace_keeps_full_tls_messages_in_core_peer_order(self):
        core_trace = """Problem EAGAIN on socket 10
----------------------------------------------- 2026-07-07T01:00:00.100000Z
TLS message sent [120] bytes:

INVITE sip:peer-b@172.28.0.20:5061 SIP/2.0
Call-ID: core-call
CSeq: 1 INVITE
Content-Length: 0

----------------------------------------------- 2026-07-07T01:00:00.300000Z
TLS message received [260] bytes:

SIP/2.0 200 OK
Call-ID: core-call
CSeq: 1 INVITE
Content-Type: application/sdp
Content-Length: 90

v=0
o=sipp-b 1 1 IN IP4 172.28.0.40
s=SIPp B
a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:test

"""
        peer_trace = """----------------------------------------------- 2026-07-07T01:00:00.200000Z
TLS message received [120] bytes:

INVITE sip:peer-b@192.168.28.30:5060;transport=tls SIP/2.0
Call-ID: peer-call
CSeq: 1 INVITE
Content-Length: 0

"""
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "sipp-a-uac").mkdir()
            (work / "sipp-b-uas").mkdir()
            (work / "sipp-a-uac" / "uac_messages.log").write_text(core_trace, encoding="utf-8")
            (work / "sipp-b-uas" / "uas_messages.log").write_text(peer_trace, encoding="utf-8")

            ordered = run_b2bua_sipp_smoke.ordered_sip_trace_text(work)

        self.assertIn("direction_order=CORE SIPp A <-> PlaySBC CORE <-> PlaySBC PEER <-> PEER SIPp B", ordered)
        self.assertLess(
            ordered.index("CORE SIPp A -> PlaySBC CORE | INVITE"),
            ordered.index("PlaySBC PEER -> PEER SIPp B | INVITE"),
        )
        self.assertLess(
            ordered.index("PlaySBC PEER -> PEER SIPp B | INVITE"),
            ordered.index("PlaySBC CORE -> CORE SIPp A | SIP/2.0 200 OK"),
        )
        self.assertIn("a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:test", ordered)
        self.assertNotIn("Problem EAGAIN", ordered)

    def test_collect_work_logs_keeps_log_sip_ordered_and_moves_raw_trace_to_log_sipp(self):
        trace = """----------------------------------------------- 2026-07-07T01:00:00.100000Z
TLS message sent [120] bytes:

INVITE sip:peer-b@172.28.0.20:5061 SIP/2.0
Call-ID: core-call
CSeq: 1 INVITE
Content-Length: 0

"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = root / "logs"
            work = root / "work"
            for folder in ("server", "registration-callee", "registration-caller", "sipp-a-uac", "sipp-b-uas"):
                (work / folder).mkdir(parents=True)
            (work / "server" / "stdout.log").write_text("server up", encoding="utf-8")
            (work / "sipp-a-uac" / "uac_messages.log").write_text(trace, encoding="utf-8")
            args = argparse_namespace(calls=1, rate=1)

            run_b2bua_sipp_smoke.collect_work_logs(log_dir, work, args)

            sip_log = (log_dir / "log.sip").read_text(encoding="utf-8")
            sipp_log = (log_dir / "log.sipp").read_text(encoding="utf-8")

        self.assertIn("ORDERED SIP MESSAGE TRACE CORE TO PEER", sip_log)
        self.assertIn("CORE SIPp A -> PlaySBC CORE", sip_log)
        self.assertNotIn("RAW SIP TRACE", sip_log)
        self.assertIn("SIPP-A-UAC RAW SIP TRACE", sipp_log)

    def test_all_xml_scenarios_are_well_formed(self):
        scenarios = ROOT / "sipp" / "scenarios"
        for scenario in sorted(scenarios.glob("*.xml")):
            with self.subTest(scenario=scenario.name):
                ET.parse(scenario)

    def test_successful_dialog_scenarios_follow_contact_remote_target(self):
        scenarios = ROOT / "sipp" / "scenarios"
        names = (
            "b2bua_uac_a.xml",
            "b2bua_uac_a_media.xml",
            "b2bua_uac_retransmit_invite.xml",
            "uac-reg-inbound.xml",
            "uac-reg-outbound.xml",
        )
        for name in names:
            with self.subTest(scenario=name):
                text = (scenarios / name).read_text(encoding="ISO-8859-1")
                self.assertIn('response="200" rtd="invite" rrs="true"', text)
                self.assertIn("ACK [next_url] SIP/2.0", text)
                self.assertIn("BYE [next_url] SIP/2.0", text)
                self.assertGreaterEqual(text.count("[routes]"), 2)

    def test_register_contact_preserves_sip_transport(self):
        scenario_text = (ROOT / "sipp" / "scenarios" / "register_contact.xml").read_text(encoding="ISO-8859-1")

        self.assertIn("Via: SIP/2.0/[transport] [local_ip]:[local_port];branch=[branch]", scenario_text)
        self.assertIn("Contact: <sip:[service]@[local_ip]:[contact_port];transport=[transport]>", scenario_text)

    def test_build_command_enables_traces(self):
        command = run_sipp_regression.build_sipp_command("sipp", "options", "127.0.0.1", 15062, 10, 5)
        self.assertIn("-trace_msg", command)
        self.assertIn("-trace_stat", command)
        self.assertIn("-trace_counts", command)
        self.assertEqual(command[1], "127.0.0.1:15062")

    def test_dry_run_creates_unique_summary_and_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "run_sipp_regression.py"),
                    "--dry-run",
                    "--output-root",
                    tmp,
                    "--run-id",
                    "unit-test-run",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            run_dir = Path(tmp) / "unit-test-run"
            summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(
                [item["status"] for item in summary["results"]],
                ["dry-run"] * len(run_sipp_regression.DEFAULT_SCENARIOS),
            )
            for scenario in run_sipp_regression.DEFAULT_SCENARIOS:
                scenario_dir = run_dir / scenario
                self.assertTrue(any(scenario_dir.glob("*command.txt")))

    def test_smoke_regression_default_scenarios_cover_python_smoke_clients(self):
        self.assertEqual(
            run_sipp_regression.DEFAULT_SCENARIOS,
            (
                "options",
                "register_digest",
                "register_digest_failure",
                "smoke_register_digest",
                "smoke_transaction_cache",
                "smoke_invalid_bye",
                "smoke_basic_call_media",
                "smoke_bridge_two_leg",
            ),
        )

    def test_bridge_smoke_scenario_builds_two_parallel_sipp_legs(self):
        commands = run_sipp_regression.build_sipp_commands("sipp", "smoke_bridge_two_leg", "127.0.0.1", 15062, 1, 1)

        self.assertEqual([name for name, _command in commands], ["bridge-a", "bridge-b"])
        self.assertIn("smoke_bridge_leg.xml", " ".join(commands[0][1]))
        self.assertIn("smoke_bridge_leg.xml", " ".join(commands[1][1]))
        self.assertIn("bridge-a", commands[0][1])
        self.assertIn("bridge-b", commands[1][1])

    def test_transaction_cache_smoke_disables_sipp_udp_retransmission(self):
        command = run_sipp_regression.build_sipp_command("sipp", "smoke_transaction_cache", "127.0.0.1", 15062, 1, 1)

        self.assertIn("-nr", command)

    def test_basic_call_smoke_scenario_uses_media_pcap_and_dtmf_offer(self):
        command = run_sipp_regression.build_sipp_command("sipp", "smoke_basic_call_media", "127.0.0.1", 15062, 1, 1)
        args = argparse_namespace(host="127.0.0.1", rtp_min=12000)
        sidecars = run_sipp_regression.build_sidecar_commands("smoke_basic_call_media", args)
        scenario_text = (ROOT / "sipp" / "scenarios" / "smoke_basic_call_media.xml").read_text(encoding="ISO-8859-1")

        self.assertIn("smoke_basic_call_media.xml", " ".join(command))
        self.assertNotIn("play_pcap_audio", scenario_text)
        self.assertEqual([name for name, _command, _delay in sidecars], ["media-pcap"])
        self.assertIn("play_g711_pcap_rtp.py", " ".join(sidecars[0][1]))
        self.assertIn("g711u_60s.pcap", " ".join(sidecars[0][1]))
        self.assertIn("12000", sidecars[0][1])
        self.assertIn("0", sidecars[0][1])
        self.assertIn("--expect-echo", sidecars[0][1])
        self.assertIn("telephone-event/8000", scenario_text)

    def test_g711_media_fixture_contains_complete_rfc4733_event(self):
        packets = run_b2bua_sipp_smoke.rtp_packets_from_pcap(
            ROOT / "sipp" / "scenarios" / "pcap" / "g711u_60s.pcap",
            2.0,
        )
        events = [payload for _timestamp, payload in packets if payload[1] & 0x7F == 101]

        self.assertGreaterEqual(len(events), 4)
        self.assertTrue(events[0][1] & 0x80)
        self.assertEqual(events[0][12], 5)
        self.assertTrue(any(payload[13] & 0x80 for payload in events))

    def test_basic_call_media_dry_run_writes_sidecar_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "run_sipp_regression.py"),
                    "--dry-run",
                    "--output-root",
                    tmp,
                    "--run-id",
                    "media-sidecar-dry-run",
                    "--scenario",
                    "smoke_basic_call_media",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            scenario_dir = Path(tmp) / "media-sidecar-dry-run" / "smoke_basic_call_media"
            command_text = (scenario_dir / "media-pcap-command.txt").read_text(encoding="utf-8")

            self.assertIn("delay_seconds=0.5", command_text)
            self.assertIn("play_g711_pcap_rtp.py", command_text)
            self.assertIn("--source-port 0", command_text)
            self.assertIn("--expect-echo", command_text)

    def test_b2bua_sipp_commands_support_load_and_hold_time(self):
        args = argparse_namespace(
            host="127.0.0.1",
            server_port=25062,
            uac_port=25081,
            uas_port=25082,
            register_port=25083,
            server_rtp_min=25100,
            server_rtp_max=25400,
            uac_rtp_min=36000,
            uac_rtp_max=36200,
            uas_rtp_min=27000,
            uas_rtp_max=27200,
            callee="dynamic-user",
            calls=5,
            rate=5,
            hold_ms=60000,
            media_enabled=False,
            media_codec=None,
            media_pcap="",
        )

        uac = run_b2bua_sipp_smoke.build_uac_command(args, "sipp")
        uas = run_b2bua_sipp_smoke.build_uas_command(args, "sipp")

        self.assertIn("-r", uac)
        self.assertIn("5", uac)
        self.assertIn("-d", uac)
        self.assertIn("60000", uac)
        self.assertIn("dynamic-user", uac)
        self.assertIn("dynamic-user", uas)
        self.assertGreaterEqual(run_b2bua_sipp_smoke.call_limit(5, 5, 60000), 300)
        self.assertEqual(run_b2bua_sipp_smoke.sipp_timeout_seconds(300, 5, 60000), 180)

    def test_b2bua_sipp_commands_can_use_tcp_transport(self):
        args = argparse_namespace(
            host="127.0.0.1",
            server_port=25062,
            sip_transport="tcp",
            uac_port=25081,
            uas_port=25082,
            uac_rtp_min=36000,
            uac_rtp_max=36200,
            uas_rtp_min=27000,
            uas_rtp_max=27200,
            callee="tcp-user",
            calls=1,
            rate=1,
            hold_ms=1000,
            media_enabled=False,
            media_codec=None,
            media_pcap="",
            media_driver="python",
            sipp_pcap_sudo=False,
            uac_scenario=ROOT / "sipp" / "scenarios" / "b2bua_uac_a.xml",
            uas_scenario=ROOT / "sipp" / "scenarios" / "b2bua_uas_b.xml",
        )

        uac = run_b2bua_sipp_smoke.build_uac_command(args, "sipp")
        uas = run_b2bua_sipp_smoke.build_uas_command(args, "sipp")

        self.assertIn("-t", uac)
        self.assertEqual(uac[uac.index("-t") + 1], "tn")
        self.assertEqual(uac[uac.index("-max_socket") + 1], "128")
        self.assertEqual(uac[uac.index("-p") + 1], "25081")
        self.assertEqual(uas[uas.index("-t") + 1], "t1")
        self.assertNotIn("-max_socket", uas)
        self.assertEqual(uas[uas.index("-p") + 1], "25082")

    def test_b2bua_register_command_uses_tcp_client_mode_with_bind_and_contact_ports(self):
        args = argparse_namespace(
            host="127.0.0.1",
            server_port=25062,
            sip_transport="tcp",
        )

        command = run_b2bua_sipp_smoke.build_register_command(
            args,
            "sipp",
            "tcp-b",
            contact_port=25082,
            local_port=25083,
        )

        self.assertEqual(command[command.index("-p") + 1], "25083")
        self.assertEqual(command[command.index("-key") + 1 : command.index("-key") + 3], ["contact_port", "25082"])
        self.assertIn("-t", command)
        self.assertEqual(command[command.index("-t") + 1], "tn")
        self.assertEqual(command[command.index("-max_socket") + 1], "128")

    def test_b2bua_register_command_keeps_udp_bind_and_contact_ports(self):
        args = argparse_namespace(
            host="127.0.0.1",
            server_port=25062,
            sip_transport="udp",
        )

        command = run_b2bua_sipp_smoke.build_register_command(
            args,
            "sipp",
            "udp-b",
            contact_port=25082,
            local_port=25083,
        )

        self.assertEqual(command[command.index("-p") + 1], "25083")
        self.assertEqual(command[command.index("-key") + 1 : command.index("-key") + 3], ["contact_port", "25082"])
        self.assertNotIn("-t", command)
        self.assertNotIn("-max_socket", command)

    def test_digest_register_profile_uses_helm_credentials_and_sipp_keys(self):
        values = dict(run_b2bua_sipp_smoke.BASE_DEFAULTS)
        values.update(run_b2bua_sipp_smoke.B2BUA_PROFILES["register-auth-success"])
        values.update(ladder_enabled=True, media_enabled=False, server_codec="PCMU")
        args = argparse_namespace(**values)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "registration-callee").mkdir()
            run_b2bua_sipp_smoke.prepare_registration_scenario(args, tmp_path)
            command = run_b2bua_sipp_smoke.build_register_command(
                args,
                "sipp",
                args.callee,
                contact_port=args.uas_port,
                local_port=args.register_port,
            )
            rendered = Path(args.registration_scenario).read_text(encoding="ISO-8859-1")
            config_path = run_b2bua_sipp_smoke.write_dynamic_config(args, tmp_path, tmp_path / "logs")
            config = server.load_config_file(str(config_path))

        self.assertIn("register_digest_resolved.xml", " ".join(command))
        self.assertIn("[authentication username=1001 password=secret-password]", rendered)
        self.assertNotIn("secret-password", " ".join(command))
        self.assertEqual(config.users, {"1001": "secret-password"})

    def test_digest_failure_profile_does_not_start_a_call(self):
        profile = run_b2bua_sipp_smoke.B2BUA_PROFILES["register-auth-failure"]

        self.assertEqual(profile["registration_auth_expected"], "failure")
        self.assertFalse(profile["run_call"])
        self.assertFalse(profile["start_uas"])

    def test_b2bua_load_profiles_run_5cps_for_60_seconds(self):
        for profile in ("load-5cps-60s", "load-5cps-60s-rtpengine-transcoding"):
            with self.subTest(profile=profile):
                self.assertEqual(run_b2bua_sipp_smoke.B2BUA_PROFILES[profile]["calls"], 300)
                self.assertEqual(run_b2bua_sipp_smoke.B2BUA_PROFILES[profile]["rate"], 5)
                self.assertEqual(run_b2bua_sipp_smoke.B2BUA_PROFILES[profile]["hold_ms"], 60000)
        self.assertGreaterEqual(run_b2bua_sipp_smoke.B2BUA_PROFILES["load-5cps-60s"]["server_rtp_max"], 26500)
        rtpengine_load = run_b2bua_sipp_smoke.B2BUA_PROFILES["load-5cps-60s-rtpengine-transcoding"]
        self.assertEqual(rtpengine_load["rtpengine_timeout"], 8.0)
        self.assertEqual(rtpengine_load["rtpengine_rtp_min"], 30000)
        self.assertEqual(rtpengine_load["rtpengine_rtp_max"], 32999)
        self.assertEqual(rtpengine_load["media_delivery_threshold_percent"], 99.5)
        self.assertEqual(rtpengine_load["media_per_call_threshold_percent"], 99.0)

    def test_b2bua_load_runs_use_stats_only_sipp_tracing(self):
        values = dict(run_b2bua_sipp_smoke.BASE_DEFAULTS)
        values.update(calls=300, rate=5, hold_ms=60000, media_enabled=False, media_codec=None, media_pcap="")
        args = argparse_namespace(**values)

        uac = run_b2bua_sipp_smoke.build_uac_command(args, "sipp")
        uas = run_b2bua_sipp_smoke.build_uas_command(args, "sipp")

        for command in (uac, uas):
            self.assertIn("-trace_err", command)
            self.assertIn("-trace_stat", command)
            self.assertIn("-trace_counts", command)
            self.assertNotIn("-trace_msg", command)
            self.assertNotIn("-trace_logs", command)

    def test_rtpengine_media_load_enables_temporary_trace_for_rtcp_anchor_discovery(self):
        values = dict(run_b2bua_sipp_smoke.BASE_DEFAULTS)
        values.update(run_b2bua_sipp_smoke.B2BUA_PROFILES["load-5cps-60s-rtpengine-transcoding"])
        values.update(media_enabled=True, profile="load-5cps-60s-rtpengine-transcoding")
        args = argparse_namespace(**values)

        self.assertIn("-trace_msg", run_b2bua_sipp_smoke.sipp_trace_args(args))
        self.assertTrue(run_b2bua_sipp_smoke.should_run_rtcp(args))

    def test_load_and_tcp_profiles_build_live_tcpdump_commands(self):
        load_values = dict(run_b2bua_sipp_smoke.BASE_DEFAULTS)
        load_values.update(run_b2bua_sipp_smoke.B2BUA_PROFILES["load-5cps-60s-rtpengine-transcoding"])
        load_values.update(media_enabled=True, sipp_pcap_sudo=True)
        tcp_values = dict(run_b2bua_sipp_smoke.BASE_DEFAULTS)
        tcp_values.update(run_b2bua_sipp_smoke.B2BUA_PROFILES["tcp-rtpengine-transcoding"])
        tcp_values.update(media_enabled=True, sipp_pcap_sudo=True)

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(run_b2bua_sipp_smoke, "resolve_binary", return_value="/usr/sbin/tcpdump"):
                load_commands = run_b2bua_sipp_smoke.live_capture_commands(argparse_namespace(**load_values), Path(tmp))
                tcp_commands = run_b2bua_sipp_smoke.live_capture_commands(argparse_namespace(**tcp_values), Path(tmp))

        self.assertEqual([name for name, _command in load_commands], ["live-pcap-control", "live-pcap-media-ring"])
        self.assertIn("-C", load_commands[1][1])
        self.assertIn("-W", load_commands[1][1])
        self.assertEqual([name for name, _command in tcp_commands], ["live-pcap"])
        self.assertIn("tcp", tcp_commands[0][1])

    def test_live_capture_segments_merge_in_timestamp_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            later = root / "live-media.pcap0"
            earlier = root / "live-control.pcap"
            destination = root / "capture.pcap"
            write_test_pcap(later, 20.0, b"later", linktype=0)
            write_test_pcap(earlier, 10.0, b"earlier", linktype=0)

            count = run_b2bua_sipp_smoke.merge_live_capture_files([later, earlier], destination)
            _header, linktype, records = run_b2bua_sipp_smoke.pcap_file_records(destination)

        self.assertEqual(count, 2)
        self.assertEqual(linktype, 0)
        self.assertEqual([frame for _timestamp, frame in records], [b"earlier", b"later"])

    def test_b2bua_single_call_runs_keep_full_sipp_tracing(self):
        values = dict(run_b2bua_sipp_smoke.BASE_DEFAULTS)
        values.update(calls=1, rate=1, hold_ms=1000, media_enabled=False, media_codec=None, media_pcap="")
        args = argparse_namespace(**values)

        uac = run_b2bua_sipp_smoke.build_uac_command(args, "sipp")

        self.assertIn("-trace_msg", uac)
        self.assertIn("-trace_logs", uac)

    def test_b2bua_sipp_commands_can_enable_g711_pcap_media(self):
        args = argparse_namespace(
            host="127.0.0.1",
            server_port=25062,
            uac_port=25081,
            uas_port=25082,
            register_port=25083,
            server_rtp_min=25100,
            server_rtp_max=25400,
            uac_rtp_min=36000,
            uac_rtp_max=36200,
            uas_rtp_min=27000,
            uas_rtp_max=27200,
            callee="media-user",
            calls=1,
            rate=1,
            hold_ms=60000,
            media_enabled=True,
            media_codec="PCMA",
            media_pcap="pcap/g711a_60s.pcap",
            media_driver="sipp-pcap",
            sipp_pcap_sudo=False,
            uac_scenario=ROOT / "sipp" / "scenarios" / "b2bua_uac_a_media.xml",
            uas_scenario=ROOT / "sipp" / "scenarios" / "b2bua_uas_b_media.xml",
        )

        uac = run_b2bua_sipp_smoke.build_uac_command(args, "sipp")
        uas = run_b2bua_sipp_smoke.build_uas_command(args, "sipp")

        self.assertIn("b2bua_uac_a_media.xml", " ".join(uac))
        self.assertIn("b2bua_uas_b_media.xml", " ".join(uas))
        self.assertIn("-key", uac)
        self.assertIn("caller", uac)
        self.assertIn("sipp-a", uac)
        self.assertNotIn("-key", uas)

    def test_sipp_pcap_sudo_wraps_only_media_sipp_commands(self):
        media_args = argparse_namespace(
            host="127.0.0.1",
            server_port=25062,
            uac_port=25081,
            uas_port=25082,
            uac_rtp_min=36000,
            uac_rtp_max=36200,
            uas_rtp_min=27000,
            uas_rtp_max=27200,
            caller="sipp-a",
            callee="media-user",
            calls=1,
            rate=1,
            hold_ms=1000,
            media_enabled=True,
            media_driver="sipp-pcap",
            sipp_pcap_sudo=True,
            uac_scenario=ROOT / "sipp" / "scenarios" / "b2bua_uac_a_media.xml",
            uas_scenario=ROOT / "sipp" / "scenarios" / "b2bua_uas_b_media.xml",
        )
        signalling_args = argparse_namespace(
            host="127.0.0.1",
            server_port=25062,
            uac_port=25081,
            uac_rtp_min=36000,
            uac_rtp_max=36200,
            caller="sipp-a",
            callee="sig-user",
            calls=1,
            rate=1,
            hold_ms=1000,
            media_enabled=False,
            media_driver="sipp-pcap",
            sipp_pcap_sudo=True,
            uac_scenario=ROOT / "sipp" / "scenarios" / "b2bua_uac_a.xml",
        )

        media_uac = run_b2bua_sipp_smoke.build_uac_command(media_args, "sipp")
        signalling_uac = run_b2bua_sipp_smoke.build_uac_command(signalling_args, "sipp")

        self.assertEqual(media_uac[:2], ["sudo", "-n"])
        self.assertNotEqual(signalling_uac[:2], ["sudo", "-n"])

    def test_b2bua_media_scenarios_resolve_pcap_path_per_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "media-run"
            (run_dir / "sipp-a-uac").mkdir(parents=True)
            (run_dir / "sipp-b-uas").mkdir(parents=True)
            args = argparse_namespace(
                media_enabled=True,
                media_pcap="pcap/g711u_60s.pcap",
                media_driver="sipp-pcap",
                sip_transport="tcp",
            )

            run_b2bua_sipp_smoke.prepare_media_scenarios(args, run_dir)

            self.assertTrue(args.uac_scenario.exists())
            self.assertTrue(args.uas_scenario.exists())
            self.assertIn(str(ROOT / "sipp" / "scenarios" / "pcap" / "g711u_60s.pcap"), args.uac_scenario.read_text(encoding="ISO-8859-1"))
            self.assertNotIn("[media_pcap]", args.uac_scenario.read_text(encoding="ISO-8859-1"))
            self.assertNotIn("[uas_sdp_payloads]", args.uas_scenario.read_text(encoding="ISO-8859-1"))
            uac_xml = args.uac_scenario.read_text(encoding="ISO-8859-1")
            self.assertIn("ACK [next_url] SIP/2.0", uac_xml)
            self.assertIn("BYE [next_url] SIP/2.0", uac_xml)
            self.assertGreaterEqual(uac_xml.count("[routes]"), 2)

    def test_tcp_signalling_scenario_uses_dialog_transport_for_ack_and_bye(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "tcp-run"
            (run_dir / "sipp-a-uac").mkdir(parents=True)
            args = argparse_namespace(
                media_enabled=False,
                media_driver="python",
                media_pcap="",
                sip_transport="tcp",
                uac_scenario="",
                uas_scenario="",
            )

            run_b2bua_sipp_smoke.prepare_media_scenarios(args, run_dir)
            run_b2bua_sipp_smoke.prepare_transport_scenario(args, run_dir)

            uac_xml = args.uac_scenario.read_text(encoding="ISO-8859-1")
            self.assertIn("ACK [next_url] SIP/2.0", uac_xml)
            self.assertIn("BYE [next_url] SIP/2.0", uac_xml)
            self.assertGreaterEqual(uac_xml.count("[routes]"), 2)

    def test_b2bua_transcoding_media_scenario_makes_b_leg_pcma_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "transcoding-run"
            (run_dir / "sipp-a-uac").mkdir(parents=True)
            (run_dir / "sipp-b-uas").mkdir(parents=True)
            args = argparse_namespace(
                media_enabled=True,
                media_pcap="pcap/g711u_60s.pcap",
                media_driver="sipp-pcap",
                media_codec="PCMU",
                server_codec="PCMA",
            )

            run_b2bua_sipp_smoke.prepare_media_scenarios(args, run_dir)

            uac_xml = args.uac_scenario.read_text(encoding="ISO-8859-1")
            uas_xml = args.uas_scenario.read_text(encoding="ISO-8859-1")
            self.assertIn(str(ROOT / "sipp" / "scenarios" / "pcap" / "g711u_60s.pcap"), uac_xml)
            self.assertIn(str(ROOT / "sipp" / "scenarios" / "pcap" / "g711a_60s.pcap"), uas_xml)
            self.assertIn("m=audio [media_port] RTP/AVP 0 101", uac_xml)
            self.assertIn("a=rtpmap:0 PCMU/8000", uac_xml)
            self.assertNotIn("a=rtpmap:8 PCMA/8000", uac_xml)
            self.assertIn("m=audio [media_port] RTP/AVP 8 101", uas_xml)
            self.assertIn("a=rtpmap:8 PCMA/8000", uas_xml)
            self.assertNotIn("a=rtpmap:0 PCMU/8000", uas_xml)
            self.assertNotIn("RTP/AVP 0 8 101", uas_xml)

    def test_python_media_driver_uses_plain_sipp_scenarios_and_player_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "media-run"
            (run_dir / "sipp-a-uac").mkdir(parents=True)
            (run_dir / "sipp-b-uas").mkdir(parents=True)
            args = argparse_namespace(
                host="127.0.0.1",
                server_rtp_min=25100,
                hold_ms=60000,
                media_enabled=True,
                media_pcap="pcap/g711u_60s.pcap",
                media_driver="python",
            )

            run_b2bua_sipp_smoke.prepare_media_scenarios(args, run_dir)
            commands = run_b2bua_sipp_smoke.build_media_player_commands(args)

            self.assertEqual(args.uac_scenario.name, "b2bua_uac_a.xml")
            self.assertEqual(args.uas_scenario.name, "b2bua_uas_b.xml")
            self.assertEqual([name for name, _command in commands], ["media-a-to-b2bua", "media-b-to-b2bua"])
            self.assertIn("25100", commands[0][1])
            self.assertIn("25102", commands[1][1])

    def test_b2bua_sipp_dry_run_writes_log_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "run_b2bua_sipp_smoke.py"),
                    "--dry-run",
                    "--output-root",
                    tmp,
                    "--run-id",
                    "b2bua-dry-run",
                    "--callee",
                    "drycallee",
                    "--calls",
                    "5",
                    "--rate",
                    "5",
                    "--hold-ms",
                    "60000",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            log_dir = Path(tmp) / run_b2bua_sipp_smoke.DEFAULT_LOG_FOLDER / "b2bua-dry-run"
            self.assertEqual({path.name for path in log_dir.iterdir()}, set(run_b2bua_sipp_smoke.LOG_FILES))
            self.assertIn("run_id=b2bua-dry-run", (log_dir / "log.platform").read_text(encoding="utf-8"))
            self.assertIn("callee=drycallee", (log_dir / "log.platform").read_text(encoding="utf-8"))
            self.assertIn("rate=5", (log_dir / "log.platform").read_text(encoding="utf-8"))
            self.assertIn("hold_ms=60000", (log_dir / "log.platform").read_text(encoding="utf-8"))
            self.assertIn("ladder_enabled=False", (log_dir / "log.platform").read_text(encoding="utf-8"))
            self.assertIn("sipp-a-uac:", (log_dir / "log.sipp").read_text(encoding="utf-8"))
            self.assertFalse((log_dir / "summary.json").exists())
            self.assertFalse((log_dir / "server-command.txt").exists())
            self.assertFalse((log_dir / "sipp-a-uac").exists())
            self.assertFalse((log_dir / "sipp-b-uas").exists())
            self.assertFalse(any((Path(tmp) / run_b2bua_sipp_smoke.DEFAULT_LOG_FOLDER).glob("*runner.log")))

    def test_b2bua_media_dry_run_sets_server_codec_and_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "run_b2bua_sipp_smoke.py"),
                    "--dry-run",
                    "--output-root",
                    tmp,
                    "--run-id",
                    "b2bua-media-dry-run",
                    "--callee",
                    "mediacallee",
                    "--calls",
                    "1",
                    "--rate",
                    "1",
                    "--hold-ms",
                    "60000",
                    "--media-codec",
                    "PCMU",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            log_dir = Path(tmp) / run_b2bua_sipp_smoke.DEFAULT_LOG_FOLDER / "b2bua-media-dry-run"
            platform = (log_dir / "log.platform").read_text(encoding="utf-8")
            sipp = (log_dir / "log.sipp").read_text(encoding="utf-8")
            self.assertIn("media_enabled=True", platform)
            self.assertIn("media_codec=PCMU", platform)
            self.assertIn("media_driver=python", platform)
            self.assertIn(f"media_pcap={ROOT / 'sipp' / 'scenarios' / 'pcap' / 'g711u_60s.pcap'}", platform)
            self.assertIn("b2bua_uac_a.xml", sipp)
            self.assertIn("media-a-to-b2bua:", sipp)
            self.assertIn("media-b-to-b2bua:", sipp)
            self.assertIn("MEDIA OBSERVATION", (log_dir / "log.media").read_text(encoding="utf-8"))
            self.assertIn("expected_rtp=True", (log_dir / "log.media").read_text(encoding="utf-8"))
            self.assertFalse((log_dir / "media-a-to-b2bua-command.txt").exists())

    def test_b2bua_platform_result_labels_uas_process_lifetime(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp) / "bundle"
            run_b2bua_sipp_smoke.initialize_log_dir(log_dir)
            run_b2bua_sipp_smoke.append_log_section(
                log_dir,
                "log.media",
                "CALL SUMMARY",
                (
                    "duration_seconds=60.500 media_mode=bridge "
                    "rtp_packets_received=3000 rtp_packets_sent=0 rtp_packets_relayed=3000"
                ),
            )
            run_b2bua_sipp_smoke.append_log_section(
                log_dir,
                "log.media",
                "CALL SUMMARY",
                (
                    "duration_seconds=60.250 media_mode=bridge "
                    "rtp_packets_received=3000 rtp_packets_sent=0 rtp_packets_relayed=3000"
                ),
            )
            args = argparse_namespace(
                resolved_run_id="unit-media-run",
                log_folder="b2bua-Regression",
                profile="basic-media",
                caller="sipp-a",
                callee="sipp-b",
                register_callee=True,
                register_caller=False,
                start_uas=True,
                reject_unknown_routes=False,
                registration_driver="sipp",
                calls=1,
                rate=1,
                hold_ms=60000,
                server_codec="PCMU",
                media_enabled=True,
                media_codec="PCMU",
                media_driver="sipp-pcap",
                sipp_pcap_sudo=True,
                media_pcap_resolved=ROOT / "sipp" / "scenarios" / "pcap" / "g711u_60s.pcap",
                media_backend="internal",
                rtpengine_url="",
                ladder_enabled=True,
            )
            results = [
                run_b2bua_sipp_smoke.SmokeResult("sipp-a-uac", [], 0, "passed", 60.9),
                run_b2bua_sipp_smoke.SmokeResult("sipp-b-uas", [], 0, "passed", 61.7),
            ]

            run_b2bua_sipp_smoke.append_results(log_dir, args, results)

            platform = (log_dir / "log.platform").read_text(encoding="utf-8")
            self.assertIn("sipp-b-uas: passed returncode=0 process_lifetime_seconds=61.700", platform)
            self.assertNotIn("sipp-b-uas: passed returncode=0 duration_seconds", platform)
            self.assertIn("MEDIA DURATION SUMMARY", platform)
            self.assertIn("media_call_summary_count=2", platform)
            self.assertIn("media_call_duration_seconds_max=60.500", platform)
            self.assertIn("media_rtp_packets_received_total=6000", platform)
            self.assertIn("media_rtp_packets_relayed_total=6000", platform)

    def test_b2bua_pcap_generation_creates_one_combined_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = root / "bundle"
            work_dir = root / "work"
            trace_dir = work_dir / "sipp-a-uac"
            trace_dir.mkdir(parents=True)
            (work_dir / "sipp-b-uas").mkdir()
            (work_dir / "registration-callee").mkdir()
            (work_dir / "registration-caller").mkdir()
            trace_dir.joinpath("b2bua_uac_a_messages.log").write_text(
                "\n".join(
                    [
                        "----------------------------------------------- 2026-06-14T10:00:00.100000",
                        "UDP message sent [92] bytes:",
                        "",
                        "OPTIONS sip:alice@127.0.0.1:25062 SIP/2.0",
                        "Call-ID: unit-pcap@127.0.0.1",
                        "Content-Length: 0",
                        "",
                        "----------------------------------------------- 2026-06-14T10:00:00.200000",
                        "UDP message received [72] bytes:",
                        "",
                        "SIP/2.0 200 OK",
                        "Call-ID: unit-pcap@127.0.0.1",
                        "Content-Length: 0",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            run_b2bua_sipp_smoke.initialize_log_dir(log_dir)
            run_b2bua_sipp_smoke.append_log_section(
                log_dir,
                "log.udp",
                "UDP RX",
                "protocol=sip source=127.0.0.1:25081 bytes=92",
            )
            args = argparse_namespace(
                dry_run=False,
                profile="basic-signalling",
                calls=1,
                rate=1,
                host="127.0.0.1",
                server_port=25062,
                server_rtp_min=25100,
                uac_port=25081,
                uas_port=25082,
            )

            created = run_b2bua_sipp_smoke.generate_pcap_artifacts(log_dir, work_dir, args)

            self.assertEqual([path.name for path in created], ["capture.pcap"])
            self.assertTrue((log_dir / "capture.pcap").exists())
            self.assertFalse((log_dir / "capture.sip.pcap").exists())
            self.assertFalse((log_dir / "capture.protocols.pcap").exists())
            self.assertEqual((log_dir / "capture.pcap").read_bytes()[:4], b"\xd4\xc3\xb2\xa1")
            pcap_packets = read_udp_pcap_packets(log_dir / "capture.pcap")
            pcap_flows = read_udp_pcap_flow_records(log_dir / "capture.pcap")
            sip_payloads = [payload for _src, _dst, payload in pcap_packets if payload.startswith((b"OPTIONS ", b"SIP/2.0 "))]
            self.assertEqual(len(sip_payloads), 2)
            self.assertTrue(all(payload.endswith(b"\r\n\r\n") for payload in sip_payloads))
            self.assertTrue(all(b"Content-Length: 0\r\n\r\n" in payload for payload in sip_payloads))
            options_flows = [
                (src_ip, src_port, dst_ip, dst_port)
                for _timestamp, src_ip, src_port, dst_ip, dst_port, payload in pcap_flows
                if payload.startswith(b"OPTIONS ")
            ]
            self.assertEqual(options_flows, [("10.10.10.10", 25081, "10.10.10.20", 25062)])
            diagnostic_packets = [
                (src, dst, payload)
                for src, dst, payload in pcap_packets
                if payload.startswith(b"PlaySBC diagnostic event")
            ]
            self.assertTrue(diagnostic_packets)
            self.assertTrue(
                all(
                    src == run_b2bua_sipp_smoke.DIAGNOSTIC_PCAP_PORT
                    and dst == run_b2bua_sipp_smoke.DIAGNOSTIC_PCAP_PORT
                    for src, dst, _payload in diagnostic_packets
                )
            )
            platform = (log_dir / "log.platform").read_text(encoding="utf-8")
            self.assertIn("PCAP GENERATION", platform)
            self.assertIn("file=capture.pcap", platform)

    def test_b2bua_tcp_pcap_generation_preserves_tcp_transport(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = root / "bundle"
            work_dir = root / "work"
            trace_dir = work_dir / "sipp-a-uac"
            trace_dir.mkdir(parents=True)
            (work_dir / "sipp-b-uas").mkdir()
            (work_dir / "registration-callee").mkdir()
            (work_dir / "registration-caller").mkdir()
            trace_dir.joinpath("b2bua_uac_a_messages.log").write_text(
                "\n".join(
                    [
                        "----------------------------------------------- 2026-06-22T10:00:00.100000",
                        "TCP message sent [190] bytes:",
                        "",
                        "INVITE sip:tcp-b@127.0.0.1:25062 SIP/2.0",
                        "Via: SIP/2.0/TCP 127.0.0.1:25081;branch=z9hG4bK-unit",
                        "From: <sip:tcp-a@127.0.0.1:25081>;tag=1",
                        "To: <sip:tcp-b@127.0.0.1:25062>",
                        "Call-ID: unit-tcp@127.0.0.1",
                        "CSeq: 1 INVITE",
                        "Content-Length: 0",
                        "",
                        "----------------------------------------------- 2026-06-22T10:00:00.200000",
                        "TCP message received [148] bytes:",
                        "",
                        "SIP/2.0 100 Trying",
                        "Via: SIP/2.0/TCP 127.0.0.1:25081;branch=z9hG4bK-unit",
                        "Call-ID: unit-tcp@127.0.0.1",
                        "CSeq: 1 INVITE",
                        "Content-Length: 0",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            run_b2bua_sipp_smoke.initialize_log_dir(log_dir)
            run_b2bua_sipp_smoke.append_log_section(
                log_dir,
                "log.tcp",
                "TCP RX",
                "protocol=sip source=127.0.0.1:25081 bytes=190",
            )
            args = argparse_namespace(
                dry_run=False,
                profile="tcp-rtpengine-transcoding",
                calls=1,
                rate=1,
                host="127.0.0.1",
                server_port=25062,
                server_rtp_min=25100,
                uac_port=25081,
                uas_port=25082,
                sip_transport="tcp",
                media_enabled=False,
            )

            created = run_b2bua_sipp_smoke.generate_pcap_artifacts(log_dir, work_dir, args)

            self.assertEqual([path.name for path in created], ["capture.pcap"])
            records = read_ip_pcap_flow_records(log_dir / "capture.pcap")
            sip_records = [
                (protocol, src_ip, src_port, dst_ip, dst_port, payload)
                for _timestamp, protocol, src_ip, src_port, dst_ip, dst_port, payload in records
                if payload.startswith((b"INVITE ", b"SIP/2.0 "))
            ]
            self.assertEqual({protocol for protocol, *_rest in sip_records}, {6})
            self.assertIn((6, "10.10.10.10", 25081, "10.10.10.20", 25062), [record[:5] for record in sip_records])
            self.assertIn((6, "10.10.10.20", 25062, "10.10.10.10", 25081), [record[:5] for record in sip_records])
            self.assertFalse(
                [
                    payload
                    for _timestamp, protocol, _src_ip, _src_port, _dst_ip, _dst_port, payload in records
                    if protocol == 17 and payload.startswith((b"INVITE ", b"SIP/2.0 "))
                ]
            )
            tcp_diagnostics = [payload for *_flow, payload in read_tcp_pcap_records(log_dir / "capture.pcap") if payload.startswith(b"PlaySBC diagnostic event")]
            self.assertFalse(tcp_diagnostics)
            tcp_records = read_tcp_pcap_records(log_dir / "capture.pcap")
            flags = [record[7] for record in tcp_records]
            self.assertEqual(flags[:3], [0x02, 0x12, 0x10])
            self.assertEqual(flags[-4:], [0x11, 0x10, 0x11, 0x10])
            self.assertEqual(sum(1 for record in tcp_records if record[8].startswith(b"INVITE ")), 1)
            self.assertEqual(sum(1 for record in tcp_records if record[8].startswith(b"SIP/2.0 ")), 1)
            for previous, current in zip(tcp_records, tcp_records[1:]):
                if previous[1:5] == current[1:5] and previous[8] and current[8]:
                    self.assertGreaterEqual(current[5], previous[5] + len(previous[8]))
            platform = (log_dir / "log.platform").read_text(encoding="utf-8")
            self.assertIn("tcp_packets=11", platform)
            self.assertIn("udp_packets=0", platform)

    def test_tcp_pcap_infers_client_initiator_when_first_trace_is_response(self):
        response = run_b2bua_sipp_smoke.PcapPacket(
            1.0,
            "10.10.10.20",
            5060,
            "10.10.10.10",
            5062,
            b"SIP/2.0 100 Trying\r\nContent-Length: 0\r\n\r\n",
            protocol="tcp",
        )

        frames = run_b2bua_sipp_smoke.tcp_connection_frame_specs([response])

        self.assertEqual(frames[0].tcp_flags, 0x02)
        self.assertEqual((frames[0].packet.src_ip, frames[0].packet.src_port), ("10.10.10.10", 5062))
        self.assertEqual((frames[0].packet.dst_ip, frames[0].packet.dst_port), ("10.10.10.20", 5060))
        self.assertEqual(frames[1].tcp_flags, 0x12)

    def test_b2bua_pcap_generation_includes_rtp_for_media_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = root / "bundle"
            work_dir = root / "work"
            trace_dir = work_dir / "sipp-b-uas"
            trace_dir.mkdir(parents=True)
            trace_dir.joinpath("b2bua_uas_b_messages.log").write_text(
                "\n".join(
                    [
                        "----------------------------------------------- 2026-06-14T10:00:00.500000",
                        "UDP message sent [180] bytes:",
                        "",
                        "SIP/2.0 200 OK",
                        "From: <sip:media-user@127.0.0.1>;tag=caller",
                        "To: <sip:media-user@127.0.0.1>;tag=callee",
                        "Call-ID: unit-media@127.0.0.1",
                        "Subject: B2BUA outbound leg for unit-media@127.0.0.1",
                        "Content-Type: application/sdp",
                        "Content-Length: 999",
                        "",
                        "v=0",
                        "o=playsbc 1 1 IN IP4 127.0.0.1",
                        "s=PlaySBC",
                        "c=IN IP4 127.0.0.1",
                        "t=0 0",
                        "m=audio 27000 RTP/AVP 0 8 101",
                        "a=rtpmap:0 PCMU/8000",
                        "a=rtpmap:8 PCMA/8000",
                        "",
                        "----------------------------------------------- 2026-06-14T10:00:00.700000",
                        "UDP message received [88] bytes:",
                        "",
                        "ACK sip:sipp-b@127.0.0.1:25082 SIP/2.0",
                        "CSeq: 1 ACK",
                        "Content-Length: 0",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            media_source = root / "media.pcap"
            rtp_payload = struct.pack("!BBHII", 0x80, 0, 1, 160, 0xC0DEC0DE) + (b"\xff" * 160)
            run_b2bua_sipp_smoke.write_udp_pcap(
                media_source,
                [
                    run_b2bua_sipp_smoke.PcapPacket(0.000, "10.0.0.1", 4000, "10.0.0.2", 4002, rtp_payload),
                    run_b2bua_sipp_smoke.PcapPacket(0.020, "10.0.0.1", 4000, "10.0.0.2", 4002, rtp_payload),
                ],
            )
            run_b2bua_sipp_smoke.initialize_log_dir(log_dir)
            run_b2bua_sipp_smoke.append_log_section(
                log_dir,
                "log.media",
                "RTP PACKET RX",
                "call_id=unit leg=inbound source=127.0.0.1:36000 seq=1 timestamp=160 payload_type=PCMU payload_bytes=160",
            )
            run_b2bua_sipp_smoke.append_log_section(
                log_dir,
                "log.media",
                "CALL SUMMARY",
                "rtp_packets_received=2 rtp_packets_sent=2 rtp_packets_relayed=2",
            )
            args = argparse_namespace(
                dry_run=False,
                profile="basic-media",
                calls=1,
                rate=1,
                hold_ms=20,
                host="127.0.0.1",
                server_port=25062,
                server_rtp_min=25100,
                uac_rtp_min=36000,
                uas_rtp_min=27000,
                uac_port=25081,
                uas_port=25082,
                media_enabled=True,
                media_codec="PCMU",
                server_codec="PCMA",
                media_pcap_resolved=media_source,
            )

            original_pcma_fixture = run_b2bua_sipp_smoke.MEDIA_PCAPS["PCMA"]
            run_b2bua_sipp_smoke.MEDIA_PCAPS["PCMA"] = "pcap/missing-test-fixture.pcap"
            try:
                created = run_b2bua_sipp_smoke.generate_pcap_artifacts(log_dir, work_dir, args)
            finally:
                run_b2bua_sipp_smoke.MEDIA_PCAPS["PCMA"] = original_pcma_fixture

            self.assertEqual([path.name for path in created], ["capture.pcap"])
            pcap_packets = read_udp_pcap_packets(log_dir / "capture.pcap")
            pcap_records = read_udp_pcap_records(log_dir / "capture.pcap")
            pcap_flows = read_udp_pcap_flow_records(log_dir / "capture.pcap")
            rtp_ports = {25100, 25102, 36000, 27000}
            rtp_packets = [
                (src, dst, payload)
                for src, dst, payload in pcap_packets
                if src in rtp_ports and dst in rtp_ports and len(payload) >= 12 and payload[0] >> 6 == 2
            ]
            rtp_flows = [
                (src_ip, src_port, dst_ip, dst_port)
                for _timestamp, src_ip, src_port, dst_ip, dst_port, payload in pcap_flows
                if src_port in rtp_ports and dst_port in rtp_ports and len(payload) >= 12 and payload[0] >> 6 == 2
            ]
            rtp_timestamps = [
                timestamp
                for timestamp, src, dst, payload in pcap_records
                if src in rtp_ports and dst in rtp_ports and len(payload) >= 12 and payload[0] >> 6 == 2
            ]
            self.assertEqual(len(rtp_packets), 8)
            self.assertAlmostEqual(
                min(rtp_timestamps),
                run_b2bua_sipp_smoke.parse_iso_timestamp("2026-06-14T10:00:00.700000") + 0.001,
                places=5,
            )
            self.assertEqual(
                {(src, dst) for src, dst, _payload in rtp_packets},
                {
                    (36000, 25100),
                    (27000, 25102),
                    (25100, 36000),
                    (25102, 27000),
                },
            )
            self.assertEqual(
                set(rtp_flows),
                {
                    ("10.10.10.10", 36000, "10.10.10.20", 25100),
                    ("10.10.10.30", 27000, "10.10.10.20", 25102),
                    ("10.10.10.20", 25100, "10.10.10.10", 36000),
                    ("10.10.10.20", 25102, "10.10.10.30", 27000),
                },
            )
            sip_payloads = [
                payload
                for _src, _dst, payload in pcap_packets
                if payload.startswith((b"SIP/2.0", b"INVITE ", b"ACK ", b"BYE ", b"REGISTER "))
            ]
            self.assertTrue(sip_payloads)
            self.assertFalse(any(b"127.0.0.1:25062" in payload for payload in sip_payloads))
            self.assertFalse(any(b"127.0.0.1:25082" in payload for payload in sip_payloads))
            non_call_id_lines = []
            for payload in sip_payloads:
                non_call_id_lines.extend(
                    line for line in payload.split(b"\r\n") if not line.lower().startswith(b"call-id:")
                )
            self.assertFalse(any(b"@127.0.0.1" in line for line in non_call_id_lines))
            self.assertIn(b"From: <sip:media-user@10.10.10.30>;tag=caller", b"\n".join(sip_payloads))
            self.assertIn(b"To: <sip:media-user@10.10.10.30>;tag=callee", b"\n".join(sip_payloads))
            self.assertIn(b"Subject: B2BUA outbound leg for unit-media@10.10.10.30", b"\n".join(sip_payloads))
            self.assertIn(b"ACK sip:sipp-b@10.10.10.30:25082 SIP/2.0", b"\n".join(sip_payloads))
            sdp_payloads = [payload for _src, _dst, payload in pcap_packets if b"m=audio 27000" in payload]
            self.assertEqual(len(sdp_payloads), 1)
            self.assertIn(b"o=playsbc 1 1 IN IP4 10.10.10.30", sdp_payloads[0])
            self.assertIn(b"c=IN IP4 10.10.10.30", sdp_payloads[0])
            self.assertNotIn(b"c=IN IP4 127.0.0.1", sdp_payloads[0])
            self.assertIn(f"Content-Length: {len(sip_body(sdp_payloads[0]))}".encode("utf-8"), sdp_payloads[0])
            payload_types_by_flow = {
                (src, dst): payload[1] & 0x7F
                for src, dst, payload in rtp_packets
            }
            self.assertEqual(payload_types_by_flow[(36000, 25100)], 0)
            self.assertEqual(payload_types_by_flow[(25100, 36000)], 0)
            self.assertEqual(payload_types_by_flow[(27000, 25102)], 8)
            self.assertEqual(payload_types_by_flow[(25102, 27000)], 8)
            ssrc_by_flow = {
                (src, dst): struct.unpack("!I", payload[8:12])[0]
                for src, dst, payload in rtp_packets
            }
            self.assertEqual(len(set(ssrc_by_flow.values())), 4)
            platform = (log_dir / "log.platform").read_text(encoding="utf-8")
            self.assertIn("rtp_packets=8", platform)
            self.assertIn("topology=logical", platform)
            self.assertIn("topology_uac_ip=10.10.10.10", platform)

    def test_rtcp_pcap_generation_uses_rtp_flow_ssrc_and_adjacent_ports(self):
        rtp_header = struct.pack("!BBHII", 0x80, 0, 1, 160, 0xA10A0001)
        rtp_packets = [
            run_b2bua_sipp_smoke.PcapPacket(
                float(second),
                "10.10.10.10",
                36000,
                "10.10.10.40",
                30000,
                rtp_header + (b"\xff" * 160),
            )
            for second in range(11)
        ]

        reports = run_b2bua_sipp_smoke.rtcp_media_packets(rtp_packets)

        self.assertEqual(len(reports), 2)
        self.assertEqual({(packet.src_port, packet.dst_port) for packet in reports}, {(36001, 30001)})
        for packet in reports:
            parsed = server.parse_compound_rtcp(packet.payload)
            self.assertEqual(int.from_bytes(parsed[0].payload[:4], "big"), 0xA10A0001)

    def test_rtpengine_pcap_uses_distinct_logical_media_anchor_ip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = root / "bundle"
            work_dir = root / "work"
            (work_dir / "sipp-a-uac").mkdir(parents=True)
            (work_dir / "sipp-b-uas").mkdir(parents=True)
            (work_dir / "registration-callee").mkdir()
            (work_dir / "registration-caller").mkdir()
            (work_dir / "sipp-a-uac" / "b2bua_uac_a_messages.log").write_text(
                "\n".join(
                    [
                        "----------------------------------------------- 2026-06-14T10:00:00.700000",
                        "UDP message received [220] bytes:",
                        "",
                        "SIP/2.0 200 OK",
                        "Content-Type: application/sdp",
                        "Content-Length: 999",
                        "",
                        "v=0",
                        "o=playsbc 1 1 IN IP4 127.0.0.1",
                        "s=PlaySBC",
                        "c=IN IP4 127.0.0.1",
                        "t=0 0",
                        "m=audio 30100 RTP/AVP 0 101",
                        "a=rtpmap:0 PCMU/8000",
                        "",
                        "----------------------------------------------- 2026-06-14T10:00:00.800000",
                        "UDP message sent [88] bytes:",
                        "",
                        "ACK sip:rtpengine-user@127.0.0.1:25062 SIP/2.0",
                        "CSeq: 1 ACK",
                        "Content-Length: 0",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            (work_dir / "sipp-b-uas" / "b2bua_uas_b_messages.log").write_text(
                "\n".join(
                    [
                        "----------------------------------------------- 2026-06-14T10:00:00.300000",
                        "UDP message received [220] bytes:",
                        "",
                        "INVITE sip:rtpengine-user@127.0.0.1:25082 SIP/2.0",
                        "Content-Type: application/sdp",
                        "Content-Length: 999",
                        "",
                        "v=0",
                        "o=playsbc 1 1 IN IP4 127.0.0.1",
                        "s=PlaySBC",
                        "c=IN IP4 127.0.0.1",
                        "t=0 0",
                        "m=audio 30102 RTP/AVP 8 101",
                        "a=rtpmap:8 PCMA/8000",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            media_source = root / "media.pcap"
            rtp_payload = struct.pack("!BBHII", 0x80, 0, 1, 160, 0xC0DEC0DE) + (b"\xff" * 160)
            run_b2bua_sipp_smoke.write_udp_pcap(
                media_source,
                [run_b2bua_sipp_smoke.PcapPacket(0.000, "10.0.0.1", 4000, "10.0.0.2", 4002, rtp_payload)],
            )
            run_b2bua_sipp_smoke.initialize_log_dir(log_dir)
            run_b2bua_sipp_smoke.append_log_section(log_dir, "log.media", "RTPENGINE ANSWER", "status=ok")
            args = argparse_namespace(
                dry_run=False,
                profile="rtpengine-transcoding",
                calls=1,
                rate=1,
                hold_ms=20,
                host="127.0.0.1",
                server_port=25062,
                server_rtp_min=25100,
                uac_rtp_min=36000,
                uas_rtp_min=27000,
                uac_port=25081,
                uas_port=25082,
                media_enabled=True,
                media_codec="PCMU",
                server_codec="PCMA",
                media_backend="rtpengine",
                media_pcap_resolved=media_source,
            )

            created = run_b2bua_sipp_smoke.generate_pcap_artifacts(log_dir, work_dir, args)

            self.assertEqual([path.name for path in created], ["capture.pcap"])
            pcap_flows = read_udp_pcap_flow_records(log_dir / "capture.pcap")
            sdp_payloads = [payload for _ts, _src_ip, _src, _dst_ip, _dst, payload in pcap_flows if b"m=audio 301" in payload]
            self.assertEqual(len(sdp_payloads), 2)
            self.assertTrue(all(b"c=IN IP4 10.10.10.40" in payload for payload in sdp_payloads))
            self.assertTrue(all(b"o=playsbc 1 1 IN IP4 10.10.10.40" in payload for payload in sdp_payloads))
            rtp_flows = {
                (src_ip, src_port, dst_ip, dst_port)
                for _timestamp, src_ip, src_port, dst_ip, dst_port, payload in pcap_flows
                if len(payload) >= 12 and payload[0] >> 6 == 2
            }
            self.assertEqual(
                rtp_flows,
                {
                    ("10.10.10.10", 36000, "10.10.10.40", 30100),
                    ("10.10.10.30", 27000, "10.10.10.40", 30102),
                    ("10.10.10.40", 30100, "10.10.10.10", 36000),
                    ("10.10.10.40", 30102, "10.10.10.30", 27000),
                },
            )
            platform = (log_dir / "log.platform").read_text(encoding="utf-8")
            self.assertIn("topology_rtpengine_ip=10.10.10.40", platform)

    def test_b2bua_pcap_generation_skips_load_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp) / "bundle"
            work_dir = Path(tmp) / "work"
            args = argparse_namespace(
                dry_run=False,
                profile="load-5cps-60s",
                calls=5,
                rate=5,
                host="127.0.0.1",
                server_port=25062,
                server_rtp_min=25100,
                uac_port=25081,
                uas_port=25082,
            )

            created = run_b2bua_sipp_smoke.generate_pcap_artifacts(log_dir, work_dir, args)

            self.assertEqual(created, [])
            self.assertFalse((log_dir / "capture.pcap").exists())

    def test_b2bua_basic_dry_run_enables_ladder_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "run_b2bua_sipp_smoke.py"),
                    "--dry-run",
                    "--output-root",
                    tmp,
                    "--run-id",
                    "b2bua-basic-dry-run",
                    "--callee",
                    "basiccallee",
                    "--calls",
                    "1",
                    "--rate",
                    "1",
                    "--hold-ms",
                    "1000",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            log_dir = Path(tmp) / run_b2bua_sipp_smoke.DEFAULT_LOG_FOLDER / "b2bua-basic-dry-run"
            self.assertIn("ladder_enabled=True", (log_dir / "log.platform").read_text(encoding="utf-8"))

    def test_registration_ladder_text_is_clear(self):
        ladder = run_b2bua_sipp_smoke.registration_ladder_text("SIPp B", "registered-b")

        self.assertIn("REGISTRATION LADDER", ladder)
        self.assertIn("user=registered-b", ladder)
        self.assertIn("REGISTER", ladder)
        self.assertIn("200 OK", ladder)
        self.assertIn("SIPp B", ladder)
        self.assertIn("B2BUA", ladder)

    def test_b2bua_dry_run_can_generate_rtpengine_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "run_b2bua_sipp_smoke.py"),
                    "--dry-run",
                    "--output-root",
                    tmp,
                    "--run-id",
                    "b2bua-rtpengine-dry-run",
                    "--callee",
                    "rtpcallee",
                    "--media-backend",
                    "rtpengine",
                    "--rtpengine-url",
                    "udp://127.0.0.1:2223",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            log_dir = Path(tmp) / run_b2bua_sipp_smoke.DEFAULT_LOG_FOLDER / "b2bua-rtpengine-dry-run"
            platform = (log_dir / "log.platform").read_text(encoding="utf-8")
            self.assertIn("media_backend=rtpengine", platform)
            self.assertIn("rtpengine_url=udp://127.0.0.1:2223", platform)

    def test_b2bua_load_rtpengine_timeout_is_written_to_server_config(self):
        rtpengine_load = run_b2bua_sipp_smoke.B2BUA_PROFILES["load-5cps-60s-rtpengine-transcoding"]
        values = dict(run_b2bua_sipp_smoke.BASE_DEFAULTS)
        values.update(
            media_backend="rtpengine",
            server_codec="PCMA",
            rtpengine_timeout=rtpengine_load["rtpengine_timeout"],
            ladder_enabled=False,
        )
        args = argparse_namespace(**values)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = run_b2bua_sipp_smoke.write_dynamic_config(args, tmp_path, tmp_path / "logs")
            config = server.load_config_file(str(config_path))

        self.assertEqual(config_path.name, "server-config.yaml")
        self.assertEqual(config.media_backend, "rtpengine")
        self.assertEqual(config.rtpengine_timeout, 8.0)

    def test_helm_chart_renders_server_yaml_from_values(self):
        chart = ROOT / "charts" / "playsbc"

        self.assertTrue((chart / "Chart.yaml").exists())
        values = (chart / "values.yaml").read_text(encoding="utf-8")
        configmap = (chart / "templates" / "configmap.yaml").read_text(encoding="utf-8")
        deployment = (chart / "templates" / "deployment.yaml").read_text(encoding="utf-8")

        self.assertIn("playsbc:", values)
        self.assertIn("route_policies:", values)
        self.assertIn("server.yaml: |", configmap)
        self.assertIn("deepCopy .Values.playsbc.config", configmap)
        self.assertIn(
            'if and (get $config "tls_verify_peer") (not (get $config "tls_cafile"))',
            configmap,
        )
        self.assertIn("toYaml $config", configmap)
        self.assertIn("/etc/playsbc/server.yaml", deployment)

    def test_helm_chart_exposes_active_active_dual_realm_topology(self):
        chart = ROOT / "charts" / "playsbc"
        values = (chart / "values.yaml").read_text(encoding="utf-8")
        deployment = (chart / "templates" / "deployment.yaml").read_text(encoding="utf-8")
        configmap = (chart / "templates" / "configmap.yaml").read_text(encoding="utf-8")
        service = (chart / "templates" / "service.yaml").read_text(encoding="utf-8")
        rtpengine = (chart / "templates" / "rtpengine.yaml").read_text(encoding="utf-8")
        shared_state = (chart / "templates" / "shared-state.yaml").read_text(encoding="utf-8")
        multus = (chart / "templates" / "multus.yaml").read_text(encoding="utf-8")

        self.assertIn("model: core-peer-active-active", values)
        self.assertIn("playsbcReplicas: 2", values)
        self.assertIn("rtpengineReplicas: 2", values)
        self.assertIn("cidr: 172.28.0.0/24", values)
        self.assertIn("cidr: 192.168.28.0/24", values)
        self.assertIn('kind: {{ if $useStatefulSet }}StatefulSet{{ else }}Deployment{{ end }}', deployment)
        self.assertIn("fieldPath: metadata.name", deployment)
        self.assertIn("ha-shared-state", deployment)
        self.assertIn('serviceName: {{ include "playsbc.fullname" . }}-headless', deployment)
        self.assertIn('name: {{ include "playsbc.fullname" . }}-headless', service)
        self.assertIn("publishNotReadyAddresses: true", service)
        self.assertIn('node_id" "$POD_NAME"', configmap)
        self.assertIn("rtpengine_pairs", configmap)
        self.assertIn("mergeOverwrite $node $existing", configmap)
        self.assertIn("mergeOverwrite $pair $existing", configmap)
        self.assertIn("rtpengine-headless", rtpengine)
        self.assertIn("--interface=default/${POD_IP}", rtpengine)
        self.assertIn("kind: PersistentVolumeClaim", shared_state)
        self.assertIn("NetworkAttachmentDefinition", multus)

    def test_helm_chart_includes_observability_stack(self):
        chart = ROOT / "charts" / "playsbc"
        values = (chart / "values.yaml").read_text(encoding="utf-8")
        stack = (chart / "templates" / "observability-stack.yaml").read_text(encoding="utf-8")
        dashboard = (chart / "templates" / "observability.yaml").read_text(encoding="utf-8")

        self.assertIn("observability:", values)
        self.assertIn("retention: 31d", values)
        self.assertIn("kind: PersistentVolumeClaim", stack)
        self.assertIn('--storage.tsdb.retention.time={{ get $prometheus "retention" | default "31d" }}', stack)
        self.assertIn("uid: prometheus", stack)
        self.assertIn("kind: Deployment", stack)
        self.assertIn("grafana", stack)
        self.assertIn("realm_model: core-peer", stack)
        self.assertIn("PlaySBC Core/Peer SBC Lab", dashboard)
        self.assertIn("playsbc_b2bua_calls_total", dashboard)
        self.assertIn("playsbc_sip_requests_total", dashboard)
        self.assertIn("playsbc_sip_responses_total", dashboard)
        self.assertIn("playsbc_media_negotiations_total", dashboard)
        self.assertIn("scrape_target: statefulset-pod", stack)
        self.assertIn("playsbc_pod", stack)
        self.assertIn("-headless.{{ $.Release.Namespace }}.svc.cluster.local", stack)
        self.assertIn("Transcoding Sessions In Range", dashboard)
        self.assertIn('transcoding=\\"true\\"', dashboard)
        self.assertIn("Transcoding By PlaySBC Node", dashboard)
        self.assertIn("sum(increase(playsbc_b2bua_calls_completed_total", dashboard)
        self.assertIn("sum(increase(playsbc_sip_requests_total", dashboard)
        self.assertIn("sum(increase(playsbc_sip_responses_total", dashboard)
        self.assertIn("max_over_time(sum(playsbc_active_calls", dashboard)
        self.assertIn("sum by (realm,trunk) (max_over_time(playsbc_trunk_healthy", dashboard)
        self.assertIn("sum by (from_realm,to_realm)", dashboard)

    def test_helm_chart_includes_azure_aks_exposure_track(self):
        chart = ROOT / "charts" / "playsbc"
        values = (chart / "values.yaml").read_text(encoding="utf-8")
        azure = (chart / "templates" / "azure-services.yaml").read_text(encoding="utf-8")
        aks_values = (ROOT / "configs" / "kubernetes" / "aks-values.yaml").read_text(encoding="utf-8")
        product_guide = (ROOT / "docs" / "PRODUCT_GUIDE.md").read_text(encoding="utf-8")

        self.assertIn("cloud:", values)
        self.assertIn("azure:", values)
        self.assertIn("playsbc-aks-aa", aks_values)
        self.assertIn("service.beta.kubernetes.io/azure-pip-name", azure)
        self.assertIn("service.beta.kubernetes.io/azure-load-balancer-resource-group", azure)
        self.assertIn("service.beta.kubernetes.io/azure-load-balancer-internal", azure)
        self.assertIn("playsbc.io/exposure: sip-public", azure)
        self.assertIn("playsbc.io/exposure: rtp-public", azure)
        self.assertIn("allowedSourceRanges", values)
        self.assertIn("$sipPublicAllowedRanges", azure)
        self.assertIn("$sipPrivateAllowedRanges", azure)
        self.assertIn("$mediaPublicAllowedRanges", azure)
        self.assertIn("documentedPortRange", aks_values)
        self.assertIn("portRange:", aks_values)
        self.assertIn("Azure AKS Administration", product_guide)
        self.assertIn("AKS Regression", product_guide)
        self.assertIn("--aks-profiles", product_guide)
        self.assertIn("PLAYSBC_VERSION=2.6.0", product_guide)

    def test_current_release_keeps_kind_regression_path(self):
        chart = ROOT / "charts" / "playsbc"
        current_version = "2.6.0"
        version = (ROOT / "VERSION").read_text(encoding="utf-8")
        chart_yaml = (chart / "Chart.yaml").read_text(encoding="utf-8")
        values = (chart / "values.yaml").read_text(encoding="utf-8")
        aks_values = (ROOT / "configs" / "kubernetes" / "aks-values.yaml").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        runbook = (ROOT / "docs" / "PRODUCT_GUIDE.md").read_text(encoding="utf-8")
        local_runbook = (ROOT / "docs" / "KUBERNETES_LOCAL.md").read_text(encoding="utf-8")
        release_notes = (ROOT / "release" / f"RELEASE_NOTES_{current_version}.md").read_text(encoding="utf-8")

        self.assertEqual(version.strip(), current_version)
        self.assertEqual(server.PLAYSBC_VERSION, current_version)
        self.assertIn(f"version: {current_version}", chart_yaml)
        self.assertIn(f'appVersion: "{current_version}"', chart_yaml)
        self.assertIn(f'tag: "{current_version}"', values)
        self.assertIn(f'tag: "{current_version}"', aks_values)
        self.assertIn(f"kind/minikube must track the current release (`v{current_version}`", readme)
        self.assertIn(f"export PLAYSBC_VERSION={current_version}", runbook)
        self.assertIn(
            "[PlaySBC v2.6.0 Product Guide](../output/pdf/PlaySBC-v2.6.0-Product-Guide.pdf)",
            local_runbook,
        )
        self.assertIn("--all-profiles", runbook)
        self.assertIn("--set-rtpengine-image", runbook)
        self.assertNotIn("playsbc-k8s-regression:1.4.2", runbook)
        self.assertIn("Local Real-Device Lab", release_notes)
        self.assertIn("Evidence Hardening", release_notes)
        self.assertIn("kind-playsbc", release_notes)
        self.assertIn("AKS", release_notes)

        args = run_k8s_regression_job.parse_args(
            [
                "--all-profiles",
                "--build-playsbc-image",
                "--build-runner-image",
                "--build-sipp-image",
                "--build-rtpengine-image",
                "--kind-load-images",
                "--set-playsbc-image",
                "--set-rtpengine-image",
                "--kind-cluster",
                "playsbc",
            ]
        )
        self.assertTrue(args.build_playsbc_image)
        self.assertTrue(args.build_runner_image)
        self.assertTrue(args.build_sipp_image)
        self.assertTrue(args.build_rtpengine_image)
        self.assertTrue(args.kind_load_images)
        self.assertTrue(args.load_rtpengine_image)
        self.assertTrue(args.set_playsbc_image)
        self.assertTrue(args.set_rtpengine_image)
        self.assertEqual(args.playsbc_image, "playsbc:k8s-regression")
        self.assertEqual(args.rtpengine_image, "playsbc/rtpengine:local")
        self.assertEqual(args.runner_image, "playsbc-k8s-regression:local")
        self.assertEqual(args.sipp_image, "playsbc-sipp:local")

        auto_args = run_k8s_regression_job.parse_args(
            [
                "--all-profiles",
                "--build-playsbc-image",
                "--build-runner-image",
                "--build-sipp-image",
                "--build-rtpengine-image",
                "--kind-load-images",
                "--kind-cluster",
                "playsbc",
            ]
        )
        self.assertTrue(auto_args.set_playsbc_image)
        self.assertTrue(auto_args.set_rtpengine_image)
        self.assertTrue(auto_args.load_rtpengine_image)

    def test_b2bua_profiles_are_listed(self):
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools" / "run_b2bua_sipp_smoke.py"),
                "--list-profiles",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("transcoding", completed.stdout)
        self.assertIn("registered-outbound", completed.stdout)
        self.assertIn("register-auth-success", completed.stdout)
        self.assertIn("register-auth-failure", completed.stdout)
        self.assertIn("dtmf-rfc4733", completed.stdout)
        self.assertIn("rtpengine-media", completed.stdout)
        self.assertIn("rtpengine-transcoding", completed.stdout)
        self.assertIn("tcp-rtpengine-transcoding", completed.stdout)
        self.assertIn("unknown-route", completed.stdout)
        self.assertIn("failed-outbound", completed.stdout)
        self.assertIn("cancel", completed.stdout)
        self.assertIn("retransmission", completed.stdout)
        self.assertIn("esbc-options-keepalive", completed.stdout)
        self.assertIn("esbc-static-trunk-route", completed.stdout)
        self.assertIn("esbc-e164-route-policy", completed.stdout)
        self.assertIn("esbc-trunk-failure", completed.stdout)
        self.assertIn("load-5cps-60s-rtpengine-transcoding", completed.stdout)

    def test_esbc_profiles_wire_expected_scenarios_and_policies(self):
        options = run_b2bua_sipp_smoke.B2BUA_PROFILES["esbc-options-keepalive"]
        self.assertEqual(options["uac_scenario"], "options.xml")
        self.assertFalse(options["register_callee"])
        self.assertFalse(options["start_uas"])

        static_trunk = run_b2bua_sipp_smoke.B2BUA_PROFILES["esbc-static-trunk-route"]
        self.assertFalse(static_trunk["register_callee"])
        self.assertEqual(static_trunk["route_policies"][0]["name"], "esbc-static-trunk")
        self.assertEqual(static_trunk["route_policies"][0]["target"], "sip:{user}@{host}:{uas_port}")

        e164 = run_b2bua_sipp_smoke.B2BUA_PROFILES["esbc-e164-route-policy"]
        self.assertEqual(e164["callee"], "+18005550100")
        self.assertEqual(e164["route_policies"][0]["match"], "+1800*")

        trunk_failure = run_b2bua_sipp_smoke.B2BUA_PROFILES["esbc-trunk-failure"]
        self.assertEqual(trunk_failure["uac_scenario"], "b2bua_uac_failed_outbound.xml")
        self.assertEqual(trunk_failure["uas_scenario"], "b2bua_uas_failed_outbound.xml")

    def test_esbc_static_trunk_profile_renders_static_route_policy_config(self):
        values = dict(run_b2bua_sipp_smoke.BASE_DEFAULTS)
        values.update(run_b2bua_sipp_smoke.B2BUA_PROFILES["esbc-static-trunk-route"])
        values.update(ladder_enabled=True, media_enabled=False, server_codec="PCMU")
        args = argparse_namespace(**values)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = run_b2bua_sipp_smoke.write_dynamic_config(args, tmp_path, tmp_path / "logs")
            config = server.load_config_file(str(config_path))

        self.assertEqual(config.route_policies[0]["name"], "esbc-static-trunk")
        self.assertEqual(config.route_policies[0]["target"], "sip:{user}@127.0.0.1:25082")

    def test_esbc_e164_route_policy_dry_run_logs_policy_and_skips_registration(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "run_b2bua_sipp_smoke.py"),
                    "--dry-run",
                    "--output-root",
                    tmp,
                    "--run-id",
                    "esbc-e164-profile",
                    "--profile",
                    "esbc-e164-route-policy",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            log_dir = Path(tmp) / run_b2bua_sipp_smoke.DEFAULT_LOG_FOLDER / "esbc-e164-profile"
            platform = (log_dir / "log.platform").read_text(encoding="utf-8")
            sipp = (log_dir / "log.sipp").read_text(encoding="utf-8")
            self.assertIn("profile=esbc-e164-route-policy", platform)
            self.assertIn("callee=+18005550100", platform)
            self.assertIn('"match": "+1800*"', platform)
            self.assertIn('"target": "sip:{user}@127.0.0.1:25082"', platform)
            self.assertIn("register_callee=False", platform)
            self.assertNotIn("registration-callee:", sipp)

    def test_b2bua_negative_profiles_wire_expected_scenarios(self):
        self.assertEqual(run_b2bua_sipp_smoke.B2BUA_PROFILES["unknown-route"]["uac_scenario"], "b2bua_uac_unknown_route.xml")
        self.assertFalse(run_b2bua_sipp_smoke.B2BUA_PROFILES["unknown-route"]["register_callee"])
        self.assertFalse(run_b2bua_sipp_smoke.B2BUA_PROFILES["unknown-route"]["start_uas"])
        self.assertTrue(run_b2bua_sipp_smoke.B2BUA_PROFILES["unknown-route"]["reject_unknown_routes"])
        self.assertEqual(run_b2bua_sipp_smoke.B2BUA_PROFILES["failed-outbound"]["uac_scenario"], "b2bua_uac_failed_outbound.xml")
        self.assertEqual(run_b2bua_sipp_smoke.B2BUA_PROFILES["failed-outbound"]["uas_scenario"], "b2bua_uas_failed_outbound.xml")
        self.assertEqual(run_b2bua_sipp_smoke.B2BUA_PROFILES["cancel"]["uac_scenario"], "b2bua_uac_cancel.xml")
        self.assertEqual(run_b2bua_sipp_smoke.B2BUA_PROFILES["cancel"]["uas_scenario"], "b2bua_uas_cancel.xml")
        self.assertEqual(run_b2bua_sipp_smoke.B2BUA_PROFILES["retransmission"]["uac_scenario"], "b2bua_uac_retransmit_invite.xml")

    def test_b2bua_tcp_rtpengine_transcoding_profile_sets_transport_backend_and_codec_mismatch(self):
        profile = run_b2bua_sipp_smoke.B2BUA_PROFILES["tcp-rtpengine-transcoding"]

        self.assertEqual(profile["sip_transport"], "tcp")
        self.assertEqual(profile["media_backend"], "rtpengine")
        self.assertEqual(profile["media_driver"], "sipp-pcap")
        self.assertEqual(profile["media_codec"], "PCMU")
        self.assertEqual(profile["server_codec"], "PCMA")

    def test_b2bua_tcp_rtpengine_transcoding_dry_run_writes_tcp_rtpengine_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "run_b2bua_sipp_smoke.py"),
                    "--dry-run",
                    "--output-root",
                    tmp,
                    "--run-id",
                    "tcp-rtpengine-transcoding-profile",
                    "--profile",
                    "tcp-rtpengine-transcoding",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            log_dir = Path(tmp) / run_b2bua_sipp_smoke.DEFAULT_LOG_FOLDER / "tcp-rtpengine-transcoding-profile"
            platform = (log_dir / "log.platform").read_text(encoding="utf-8")
            sipp = (log_dir / "log.sipp").read_text(encoding="utf-8")
            self.assertIn("profile=tcp-rtpengine-transcoding", platform)
            self.assertIn("media_backend=rtpengine", platform)
            self.assertIn("sip_transport=tcp", platform)
            self.assertIn("transcoding_expected=True", platform)
            self.assertIn("transcoding_owner=rtpengine", platform)
            self.assertIn("-t t1", sipp)

    def test_b2bua_unknown_route_dry_run_skips_registration_and_uas(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "run_b2bua_sipp_smoke.py"),
                    "--dry-run",
                    "--output-root",
                    tmp,
                    "--run-id",
                    "unknown-route-profile",
                    "--profile",
                    "unknown-route",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            log_dir = Path(tmp) / run_b2bua_sipp_smoke.DEFAULT_LOG_FOLDER / "unknown-route-profile"
            platform = (log_dir / "log.platform").read_text(encoding="utf-8")
            sipp = (log_dir / "log.sipp").read_text(encoding="utf-8")

            self.assertIn("register_callee=False", platform)
            self.assertIn("start_uas=False", platform)
            self.assertIn("reject_unknown_routes=True", platform)
            self.assertIn("b2bua_uac_unknown_route.xml", sipp)
            self.assertNotIn("registration-callee:", sipp)
            self.assertNotIn("sipp-b-uas:", sipp)

    def test_b2bua_transcoding_profile_sets_codec_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "run_b2bua_sipp_smoke.py"),
                    "--dry-run",
                    "--output-root",
                    tmp,
                    "--run-id",
                    "transcoding-profile",
                    "--profile",
                    "transcoding",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            log_dir = Path(tmp) / run_b2bua_sipp_smoke.DEFAULT_LOG_FOLDER / "transcoding-profile"
            platform = (log_dir / "log.platform").read_text(encoding="utf-8")
            self.assertIn("profile=transcoding", platform)
            self.assertIn("media_codec=PCMU", platform)
            self.assertIn("uas_media_codec=PCMA", platform)
            self.assertIn("server_codec=PCMA", platform)
            self.assertIn("hold_ms=60000", platform)
            self.assertIn("transcoding_expected=True", platform)
            self.assertIn("transcoding_owner=internal", platform)
            transcoding = (log_dir / "log.transcoding").read_text(encoding="utf-8")
            self.assertIn("TRANSCODING OBSERVATION", transcoding)
            self.assertIn("expected=True", transcoding)

    def test_b2bua_registered_outbound_profile_registers_caller_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "run_b2bua_sipp_smoke.py"),
                    "--dry-run",
                    "--output-root",
                    tmp,
                    "--run-id",
                    "registered-outbound-profile",
                    "--profile",
                    "registered-outbound",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            log_dir = Path(tmp) / run_b2bua_sipp_smoke.DEFAULT_LOG_FOLDER / "registered-outbound-profile"
            platform = (log_dir / "log.platform").read_text(encoding="utf-8")
            sipp = (log_dir / "log.sipp").read_text(encoding="utf-8")
            self.assertIn("profile=registered-outbound", platform)
            self.assertIn("caller=registered-a", platform)
            self.assertIn("callee=registered-b", platform)
            self.assertIn("register_caller=True", platform)
            self.assertIn("registration_driver=sipp", platform)
            self.assertIn("register_contact.xml", sipp)
            self.assertIn("uac-reg-outbound.xml", sipp)
            self.assertIn("uas-reg-outbound.xml", sipp)
            self.assertIn("-key caller registered-a", sipp)

    def test_b2bua_registered_inbound_profile_uses_named_sipp_scripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "run_b2bua_sipp_smoke.py"),
                    "--dry-run",
                    "--output-root",
                    tmp,
                    "--run-id",
                    "registered-inbound-profile",
                    "--profile",
                    "registered-inbound",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            log_dir = Path(tmp) / run_b2bua_sipp_smoke.DEFAULT_LOG_FOLDER / "registered-inbound-profile"
            platform = (log_dir / "log.platform").read_text(encoding="utf-8")
            sipp = (log_dir / "log.sipp").read_text(encoding="utf-8")
            self.assertIn("profile=registered-inbound", platform)
            self.assertIn("caller=reg-inbound-a", platform)
            self.assertIn("callee=registered-b", platform)
            self.assertIn("registration_driver=sipp", platform)
            self.assertIn("register_contact.xml", sipp)
            self.assertIn("uac-reg-inbound.xml", sipp)
            self.assertIn("uas-reg-inbound.xml", sipp)

    def test_regression_report_html_marks_pass_and_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            report_dir = Path(tmp) / "reports"
            bundle = Path(tmp) / "options"
            report_dir.mkdir()
            bundle.mkdir()
            (bundle / "log.sipp").write_text("SIPp passed\n", encoding="utf-8")
            (bundle / "sipmsg.log").write_text("OPTIONS sip:playsbc SIP/2.0\n", encoding="utf-8")
            (bundle / "capture.pcap").write_bytes(b"pcap-evidence")
            rows = [
                run_regression_suite.ReportRow(
                    "SIPp Smoke",
                    "options",
                    "passed",
                    0,
                    0.1,
                    str(bundle),
                    "cmd",
                    phases=[
                        run_regression_suite.ReportPhase(
                            "Test Execution", "passed", 0.1, "OPTIONS returned 200"
                        )
                    ],
                ),
                run_regression_suite.ReportRow("B2BUA", "media", "failed", 1, 0.2, "/tmp/logs", "cmd"),
                run_regression_suite.ReportRow("B2BUA", "rtpengine-preflight", "blocked", None, 0.01, "/tmp/logs", "cmd"),
            ]

            report = run_regression_suite.render_html(
                rows,
                "2026-06-13 10:00:00 IST",
                "unit-report",
                report_dir=report_dir,
            )

            self.assertIn("PlaySBC Regression Evidence Report", report)
            self.assertIn("PASSED", report)
            self.assertIn("FAILED", report)
            self.assertIn("BLOCKED", report)
            self.assertIn("Blocked: 1", report)
            self.assertIn("badge pass", report)
            self.assertIn("badge fail", report)
            self.assertIn("badge blocked", report)
            self.assertIn("Execution and evidence", report)
            self.assertNotIn("Robot-style execution log", report)
            self.assertIn("serve_regression_report.py", report)
            self.assertIn("Evidence Files", report)
            self.assertIn("log.sipp", report)
            self.assertIn("sipmsg.log", report)
            self.assertIn("Test Execution", report)
            self.assertIn('href="evidence/options/log.sipp.html"', report)
            self.assertIn('href="evidence/options/sipmsg.log.html"', report)
            self.assertIn('href="evidence/options/capture.pcap.html"', report)
            self.assertNotIn('href="../options/', report)
            self.assertEqual(
                (report_dir / "evidence" / "options" / "log.sipp").read_text(encoding="utf-8"),
                "SIPp passed\n",
            )
            self.assertEqual(
                (report_dir / "evidence" / "options" / "sipmsg.log").read_text(encoding="utf-8"),
                "OPTIONS sip:playsbc SIP/2.0\n",
            )
            viewer = (report_dir / "evidence" / "options" / "sipmsg.log.html").read_text(encoding="utf-8")
            self.assertIn("OPTIONS sip:playsbc SIP/2.0", viewer)
            self.assertIn("Download raw file", viewer)
            self.assertIn('href="../../latest.html"', viewer)
            self.assertIn('href="sipmsg.log?raw=1" download', viewer)
            pcap_viewer = (report_dir / "evidence" / "options" / "capture.pcap.html").read_text(encoding="utf-8")
            self.assertIn("This binary evidence cannot be rendered as text in a browser.", pcap_viewer)
            self.assertIn('href="capture.pcap?raw=1" download', pcap_viewer)

    def test_regression_evidence_server_wraps_text_and_binary_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            text_path = root / "sipmsg.log"
            pcap_path = root / "capture.pcap"
            text_path.write_text("INVITE sip:1002@example.test SIP/2.0\n", encoding="utf-8")
            pcap_path.write_bytes(b"pcap-evidence")

            text_page = serve_regression_report.render_text_evidence(text_path, root).decode("utf-8")
            binary_page = serve_regression_report.render_binary_evidence(pcap_path, root).decode("utf-8")
            evidence_paths = {}
            rewritten = serve_regression_report.rewrite_report_links(
                '<a href="../evidence/sipmsg.log">SIP messages</a>'
                '<a href="capture.pcap?raw=1" download>Download raw file</a>',
                "/k8s-reports/latest.html",
                evidence_paths,
            ).decode("utf-8")

            self.assertIn("<!doctype html>", text_page)
            self.assertIn("INVITE sip:1002@example.test SIP/2.0", text_page)
            self.assertIn("open -a Wireshark", binary_page)
            self.assertIn(hashlib.sha256(b"pcap-evidence").hexdigest(), binary_page)
            self.assertRegex(rewritten, r'/evidence/[0-9a-f]{24}')
            self.assertIn('href="capture.pcap?raw=1" download', rewritten)
            self.assertEqual(list(evidence_paths.values()), ["evidence/sipmsg.log"])

    def test_regression_report_embeds_single_call_sip_ladder(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            (bundle / "log.sip").write_text(
                "2026-07-05 09:59:59 | CALLEE REGISTRATION LADDER\n"
                "REGISTRATION LADDER\nuser=registered-b\n"
                "2026-07-05 10:00:00 | B2BUA SIP LADDER | call_id=call-1\n"
                "SIP LADDER\nStep       SIPp A       B2BUA       SIPp B\n"
                "2026-07-05 10:00:00 | AI VOICE CALL LADDER | call_id=call-ai\n"
                "AI VOICE CALL LADDER\nStep       SIPp A       PlaySBC       STT Adapter       Rasa Bot       TTS Adapter\n"
                "2026-07-05 10:00:01 | SIP RX REQUEST\n",
                encoding="utf-8",
            )
            ladder = run_regression_suite.read_sip_ladder(bundle)
            row = run_regression_suite.ReportRow(
                "B2BUA basic-signalling", "basic-signalling", "passed", 0, 1.0, str(bundle), "cmd",
                sip_ladder=ladder,
            )

            report = run_regression_suite.render_html([row], "2026-07-05", "ladder-report")

            self.assertIn("SIP LADDER", ladder)
            self.assertIn("CALLEE REGISTRATION LADDER", ladder)
            self.assertIn("AI VOICE CALL LADDER", ladder)
            self.assertIn("<h2>Unified SIP/RTP/AI Ladder</h2>", report)
            self.assertIn("SIPp A", report)

    def test_regression_report_embeds_ai_speech_audio_players(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "bundle"
            report_dir = root / "reports"
            bundle.mkdir()
            report_dir.mkdir()
            (bundle / "ai-speech-input-call.wav").write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")
            (bundle / "ai-tts-output-call.wav").write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")
            row = run_regression_suite.ReportRow(
                "Kubernetes AI/Rasa Speech RTPengine",
                "ai-rasa-rtpengine-speech",
                "passed",
                0,
                1.0,
                str(bundle),
                "cmd",
                sip_ladder="AI VOICE CALL LADDER\nStep  Core SIPp A  RTPengine  PlaySBC  Vosk STT  Rasa Bot  Piper TTS",
            )

            report = run_regression_suite.render_html([row], "2026-07-15", "audio-report", report_dir=report_dir)

            self.assertIn("AI Speech Audio Evidence", report)
            self.assertIn("Caller speech input", report)
            self.assertIn("Piper TTS output", report)
            self.assertIn("<audio controls", report)
            self.assertIn("data:audio/wav;base64,", report)
            self.assertIn("embedded WAV", report)
            self.assertIn("Open WAV file", report)
            self.assertIn("evidence/bundle/ai-speech-input-call.wav", report)
            self.assertIn("evidence/bundle/ai-tts-output-call.wav", report)
            self.assertTrue((report_dir / "evidence" / "bundle" / "ai-speech-input-call.wav").is_file())
            self.assertTrue((report_dir / "evidence" / "bundle" / "ai-tts-output-call.wav").is_file())

    def test_regression_report_reads_measured_robot_phases_from_platform_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            records = [
                {
                    "name": "Setup Preparation",
                    "status": "passed",
                    "duration_seconds": 0.125,
                    "detail": "Prepare SIPp scenarios.",
                },
                {
                    "name": "Test Execution",
                    "status": "passed",
                    "duration_seconds": 60.25,
                    "detail": "Execute one 60-second B2BUA call.",
                },
            ]
            (bundle / "log.platform").write_text(
                "\n".join(run_regression_suite.ROBOT_PHASE_PREFIX + json.dumps(record) for record in records) + "\n",
                encoding="utf-8",
            )

            phases = run_regression_suite.read_execution_phases(bundle)
            row = run_regression_suite.ReportRow(
                "B2BUA basic-media",
                "basic-media",
                "passed",
                0,
                61.0,
                str(bundle),
                "cmd",
                phases,
            )
            report = run_regression_suite.render_html([row], "2026-07-04 10:00:00 IST", "robot-report")

            self.assertEqual([phase.name for phase in phases], ["Setup Preparation", "Test Execution"])
            self.assertIn("Prepare SIPp scenarios.", report)
            self.assertIn("Execute one 60-second B2BUA call.", report)
            self.assertIn("0.125 s", report)
            self.assertIn("60.250 s", report)

    def test_dual_realm_robot_phase_is_recorded_in_platform_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            run_b2bua_sipp_smoke.initialize_log_dir(bundle)
            records = []

            run_dual_realm_profile.append_robot_phase(
                bundle,
                records,
                "Configuration",
                "passed",
                time.monotonic(),
                "Render Helm configuration.",
            )
            run_dual_realm_profile.flush_robot_phases(bundle, records)

            phases = run_regression_suite.read_execution_phases(bundle)
            self.assertEqual(len(records), 1)
            self.assertEqual(phases[0].name, "Configuration")
            self.assertEqual(phases[0].status, "passed")
            self.assertEqual(phases[0].detail, "Render Helm configuration.")

    def test_rtpengine_blocked_row_has_actionable_detail(self):
        row = run_regression_suite.rtpengine_blocked_row(
            "rtpengine",
            "udp://127.0.0.1:2223",
            "TimeoutError",
            0.01,
            Path("/tmp/playsbc-logs"),
            "python3 tools/run_b2bua_sipp_smoke.py --profile rtpengine",
        )

        self.assertEqual(row.status, "blocked")
        self.assertEqual(row.name, "rtpengine-preflight")
        self.assertIn("RTPengine not reachable at udp://127.0.0.1:2223", row.command)

    def test_b2bua_stdout_parser_uses_profile_bundle_path(self):
        rows = run_regression_suite.parse_b2bua_stdout(
            "basic-media",
            "B2BUA SIPp logs: /tmp/playsbc/basic-media-bundle\nregistration: passed\nsipp-a-uac: passed\n",
            0,
            0.2,
            Path("/tmp/playsbc/default"),
            "cmd",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].suite, "B2BUA basic-media")
        self.assertEqual(rows[0].name, "basic-media")
        self.assertEqual(rows[0].status, "passed")
        self.assertEqual({row.log_path for row in rows}, {"/tmp/playsbc/basic-media-bundle"})
        self.assertIn("steps: registration=passed, sipp-a-uac=passed", rows[0].command)

    def test_b2bua_stdout_parser_collapses_failed_steps_into_one_failed_profile_row(self):
        rows = run_regression_suite.parse_b2bua_stdout(
            "basic-signalling",
            (
                "B2BUA SIPp logs: /tmp/playsbc/basic-signalling-bundle\n"
                "registration: passed\n"
                "sipp-a-uac: failed\n"
                "sipp-b-uas: passed\n"
            ),
            1,
            3.5,
            Path("/tmp/playsbc/default"),
            "cmd",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].suite, "B2BUA basic-signalling")
        self.assertEqual(rows[0].name, "basic-signalling")
        self.assertEqual(rows[0].status, "failed")
        self.assertEqual(rows[0].returncode, 1)
        self.assertEqual(rows[0].duration_seconds, 3.5)
        self.assertIn("sipp-a-uac=failed", rows[0].command)

    def test_b2bua_stdout_parser_marks_nonzero_command_failed_even_if_steps_passed(self):
        rows = run_regression_suite.parse_b2bua_stdout(
            "basic-signalling",
            "B2BUA SIPp logs: /tmp/playsbc/basic-signalling-bundle\nregistration: passed\nsipp-a-uac: passed\n",
            1,
            2.0,
            Path("/tmp/playsbc/default"),
            "cmd",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, "failed")
        self.assertEqual(rows[0].returncode, 1)

    def test_cleanup_non_failed_b2bua_bundles_keeps_failed_and_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_root = Path(tmp) / "b2bua-Regression"
            report_dir = Path(tmp) / "reports"
            passed = log_root / "passed-bundle"
            failed = log_root / "failed-bundle"
            blocked = log_root / "blocked-bundle"
            unknown = log_root / "unknown-bundle"

            run_regression_suite.append_bundle_log(
                passed,
                "log.platform",
                "B2BUA SIPP RUN RESULT",
                "registration: passed\nsipp-a-uac: passed\nsipp-b-uas: passed",
            )
            run_regression_suite.append_bundle_log(
                failed,
                "log.platform",
                "B2BUA SIPP RUN RESULT",
                "registration: passed\nsipp-a-uac: failed\nsipp-b-uas: passed",
            )
            run_regression_suite.append_bundle_log(
                blocked,
                "log.platform",
                "RTPENGINE PREFLIGHT BLOCKED",
                "reason=connection refused",
            )
            unknown.mkdir(parents=True)

            deleted = run_regression_suite.cleanup_non_failed_b2bua_log_bundles(log_root, report_dir)

            self.assertEqual(deleted, [blocked, passed])
            self.assertFalse(passed.exists())
            self.assertFalse(blocked.exists())
            self.assertTrue(failed.exists())
            self.assertTrue(unknown.exists())

    def test_cleanup_non_failed_b2bua_bundles_can_use_previous_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_root = Path(tmp) / "b2bua-Regression"
            report_dir = Path(tmp) / "reports"
            report_dir.mkdir()
            passed = log_root / "reported-passed"
            blocked = log_root / "reported-blocked"
            failed = log_root / "reported-failed"
            passed.mkdir(parents=True)
            blocked.mkdir(parents=True)
            failed.mkdir(parents=True)
            report = [
                {
                    "suite": "B2BUA basic-media",
                    "name": "registration",
                    "status": "passed",
                    "returncode": 0,
                    "duration_seconds": 0,
                    "log_path": str(passed),
                    "command": "cmd",
                },
                {
                    "suite": "B2BUA basic-media",
                    "name": "sipp-a-uac",
                    "status": "passed",
                    "returncode": 0,
                    "duration_seconds": 0,
                    "log_path": str(passed),
                    "command": "cmd",
                },
                {
                    "suite": "B2BUA rtpengine",
                    "name": "rtpengine-preflight",
                    "status": "blocked",
                    "returncode": None,
                    "duration_seconds": 0,
                    "log_path": str(blocked),
                    "command": "cmd",
                },
                {
                    "suite": "B2BUA transcoding",
                    "name": "sipp-a-uac",
                    "status": "failed",
                    "returncode": 1,
                    "duration_seconds": 0,
                    "log_path": str(failed),
                    "command": "cmd",
                },
            ]
            (report_dir / "previous.json").write_text(json.dumps(report), encoding="utf-8")

            deleted = run_regression_suite.cleanup_non_failed_b2bua_log_bundles(log_root, report_dir)

            self.assertEqual(deleted, [blocked, passed])
            self.assertFalse(passed.exists())
            self.assertFalse(blocked.exists())
            self.assertTrue(failed.exists())

    def test_cleanup_old_reports_keeps_latest_and_current_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            report_dir = Path(tmp)
            for name in (
                "regression-old.html",
                "regression-old.json",
                "custom-old.html",
                "custom-old.json",
                "latest.html",
                "regression-current.html",
                "regression-current.json",
                "notes.txt",
            ):
                (report_dir / name).write_text("x", encoding="utf-8")

            deleted = run_regression_suite.cleanup_old_reports(report_dir, "regression-current")

            self.assertEqual(
                {path.name for path in deleted},
                {"regression-old.html", "regression-old.json", "custom-old.html", "custom-old.json"},
            )
            self.assertEqual(
                {path.name for path in report_dir.iterdir()},
                {"latest.html", "regression-current.html", "regression-current.json", "notes.txt"},
            )

    def test_sudo_keepalive_refreshes_cached_credentials(self):
        completed = subprocess.CompletedProcess(["sudo", "-n", "-v"], 0, stdout="", stderr="")
        with mock.patch.object(run_regression_suite.subprocess, "run", return_value=completed) as run:
            keepalive = run_regression_suite.SudoKeepalive(interval_seconds=60)
            try:
                ok, detail = keepalive.start()
            finally:
                keepalive.stop()

        self.assertTrue(ok)
        self.assertEqual(detail, "sudo credentials refreshed")
        run.assert_called_with(["sudo", "-n", "-v"], text=True, capture_output=True)

    def test_sudo_keepalive_reports_missing_cached_credentials(self):
        completed = subprocess.CompletedProcess(
            ["sudo", "-n", "-v"],
            1,
            stdout="",
            stderr="sudo: a password is required",
        )
        with mock.patch.object(run_regression_suite.subprocess, "run", return_value=completed):
            keepalive = run_regression_suite.SudoKeepalive(interval_seconds=60)
            ok, detail = keepalive.start()

        self.assertFalse(ok)
        self.assertIn("password is required", detail)

    def test_regression_suite_can_target_all_b2bua_profiles(self):
        self.assertEqual(
            len(run_regression_suite.ALL_B2BUA_PROFILES),
            len(run_b2bua_sipp_smoke.B2BUA_PROFILES) + 1,
        )
        self.assertEqual(
            set(run_regression_suite.ALL_B2BUA_PROFILES),
            set(run_b2bua_sipp_smoke.B2BUA_PROFILES) | {run_regression_suite.REAL_TOPOLOGY_PROFILE},
        )
        self.assertIn("ai-rasa-real-lab", run_regression_suite.SELECTABLE_B2BUA_PROFILES)
        self.assertIn("ai-rasa-real-lab", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("ai-rasa-rtpengine-speech", run_regression_suite.SELECTABLE_B2BUA_PROFILES)
        self.assertIn("evidence-b2bua-two-leg-pcap", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertEqual(len(run_k8s_regression.ALL_PROFILES), 78)
        self.assertIn("ai-rasa-rtpengine-speech", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("ai-rasa-rtpengine-speech-whisper", run_regression_suite.SELECTABLE_B2BUA_PROFILES)
        self.assertIn("ai-rasa-rtpengine-speech-whisper", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("ai-rasa-long-response-streaming", run_regression_suite.SELECTABLE_B2BUA_PROFILES)
        self.assertIn("ai-rasa-long-response-streaming", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("ai-rasa-contact-center-sales", run_regression_suite.SELECTABLE_B2BUA_PROFILES)
        self.assertIn("ai-rasa-contact-center-sales", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("ai-rasa-contact-center-sales-coqui", run_regression_suite.SELECTABLE_B2BUA_PROFILES)
        self.assertIn("ai-rasa-contact-center-sales-coqui", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertEqual(
            run_regression_suite.RASA_B2BUA_PROFILES,
            (
                "ai-rasa-lab",
                "ai-rasa-rtpengine",
                "ai-rasa-real-lab",
                "ai-rasa-rtpengine-speech",
                "ai-rasa-rtpengine-speech-whisper",
                "ai-rasa-long-response-streaming",
                "ai-rasa-contact-center-sales",
                "ai-rasa-contact-center-sales-coqui",
            ),
        )
        self.assertIn("rtpengine", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("rtpengine-media", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("rtpengine-transcoding", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("tcp-rtpengine-transcoding", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("register-auth-success", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("register-auth-failure", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("dtmf-rfc4733", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("ai-rasa-lab", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("ai-rasa-rtpengine", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("unknown-route", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("failed-outbound", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("cancel", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("retransmission", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("esbc-options-keepalive", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("esbc-static-trunk-route", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("esbc-e164-route-policy", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("esbc-trunk-failure", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("small-load-2cps-10s", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("soak-1cps-30s", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("load-5cps-60s-rtpengine-transcoding", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("tcp-rtpengine-transcoding", run_regression_suite.RTPENGINE_B2BUA_PROFILES)
        self.assertIn("ai-rasa-rtpengine", run_regression_suite.RTPENGINE_B2BUA_PROFILES)
        self.assertIn("ai-rasa-real-lab", run_regression_suite.RTPENGINE_B2BUA_PROFILES)
        self.assertIn("ai-rasa-rtpengine-speech", run_regression_suite.RTPENGINE_B2BUA_PROFILES)
        self.assertIn("ai-rasa-rtpengine-speech-whisper", run_regression_suite.RTPENGINE_B2BUA_PROFILES)
        self.assertIn("ai-rasa-long-response-streaming", run_regression_suite.RTPENGINE_B2BUA_PROFILES)
        self.assertIn("ai-rasa-contact-center-sales", run_regression_suite.RTPENGINE_B2BUA_PROFILES)
        self.assertIn("ai-rasa-contact-center-sales-coqui", run_regression_suite.RTPENGINE_B2BUA_PROFILES)
        self.assertIn("real-topology-rtpengine-transcoding", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("esbc-trunk-failover", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("ha-shared-state-rtpengine", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("ha-options-health-recovery", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("ha-node-draining", run_regression_suite.ALL_B2BUA_PROFILES)
        for profile in (
            "rfc5359-unattended-transfer",
            "rfc5359-unconditional-forwarding",
            "rfc5359-forwarding-on-busy",
            "rfc5359-forwarding-on-no-answer",
            "rfc5359-unattended-transfer-rtpengine",
            "rfc5359-unconditional-forwarding-rtpengine",
            "rfc5359-forwarding-on-busy-rtpengine",
            "rfc5359-forwarding-on-no-answer-rtpengine",
        ):
            self.assertIn(profile, run_regression_suite.ALL_B2BUA_PROFILES)
        for profile in (
            "rfc5359-unattended-transfer-rtpengine",
            "rfc5359-unconditional-forwarding-rtpengine",
            "rfc5359-forwarding-on-busy-rtpengine",
            "rfc5359-forwarding-on-no-answer-rtpengine",
        ):
            self.assertIn(profile, run_regression_suite.RTPENGINE_B2BUA_PROFILES)
            values = run_k8s_regression.profile_values(profile, "unit-k8s")
            self.assertEqual(values.media_backend, "rtpengine")
            self.assertIn(
                "RTPENGINE OFFER",
                values.expected_log_markers["log.media"],
            )
        for profile in (
            "ha-playsbc-precall-failover",
            "ha-playsbc-midcall-failover",
            "ha-playsbc-postcall-failover",
            "ha-rtpengine-precall-failover",
            "ha-rtpengine-midcall-recovery",
            "ha-node-drain-active-calls",
            "ha-active-active-load-distribution",
            "ha-shared-registrar-dialog-restore",
        ):
            self.assertIn(profile, run_regression_suite.ALL_B2BUA_PROFILES)
            self.assertIn(profile, run_regression_suite.RTPENGINE_B2BUA_PROFILES)
        self.assertIn("tls-transport-policy", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("rtpengine-port-exhaustion", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("rtcp-receiver-quality", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("tls-srtp-to-udp-rtp", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("tls-srtp-to-tcp-rtp", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("udp-rtp-to-tls-srtp", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("ha-shared-state-rtpengine", run_regression_suite.RTPENGINE_B2BUA_PROFILES)

    def test_real_topology_profile_uses_one_regression_bundle(self):
        command = run_regression_suite.real_topology_command(
            "regression-test-real-topology-rtpengine-transcoding",
            Path("/tmp/playsbc-regression"),
        )

        self.assertIn("run_real_topology.py", " ".join(command))
        self.assertEqual(command[-4:], ["--run-id", "regression-test-real-topology-rtpengine-transcoding", "--output-root", "/tmp/playsbc-regression"])

    def test_every_regression_profile_uses_dual_realm_runner(self):
        for profile in run_regression_suite.ALL_B2BUA_PROFILES:
            command = run_regression_suite.dual_realm_command(
                profile,
                f"regression-test-{profile}",
                Path("/tmp/playsbc-regression"),
            )
            self.assertIn("run_dual_realm_profile.py", " ".join(command))
            self.assertEqual(command[command.index("--profile") + 1], profile)
            self.assertIn("--skip-build", command)

    def test_first_dual_realm_profile_can_rebuild_current_images(self):
        command = run_regression_suite.dual_realm_command(
            "basic-signalling",
            "regression-test-basic-signalling",
            Path("/tmp/playsbc-regression"),
            rebuild=True,
        )

        self.assertIn("--rebuild", command)
        self.assertNotIn("--skip-build", command)

    def test_direct_rtpengine_profile_blocks_before_sipp_when_down(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "run_b2bua_sipp_smoke.py"),
                    "--output-root",
                    tmp,
                    "--run-id",
                    "rtpengine-down",
                    "--profile",
                    "rtpengine-media",
                    "--rtpengine-url",
                    "udp://127.0.0.1:9",
                    "--rtpengine-timeout",
                    "0.05",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )

            self.assertEqual(completed.returncode, 2, completed.stderr)
            self.assertIn("rtpengine-preflight: blocked", completed.stdout)
            log_dir = Path(tmp) / run_b2bua_sipp_smoke.DEFAULT_LOG_FOLDER / "rtpengine-down"
            self.assertIn("RTPENGINE PREFLIGHT BLOCKED", (log_dir / "log.platform").read_text(encoding="utf-8"))
            self.assertIn("status=blocked", (log_dir / "log.media").read_text(encoding="utf-8"))

    def test_rtpengine_transcoding_profile_sets_media_and_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "run_b2bua_sipp_smoke.py"),
                    "--dry-run",
                    "--output-root",
                    tmp,
                    "--run-id",
                    "rtpengine-transcoding-profile",
                    "--profile",
                    "rtpengine-transcoding",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            log_dir = Path(tmp) / run_b2bua_sipp_smoke.DEFAULT_LOG_FOLDER / "rtpengine-transcoding-profile"
            platform = (log_dir / "log.platform").read_text(encoding="utf-8")
            sipp = (log_dir / "log.sipp").read_text(encoding="utf-8")
            self.assertIn("profile=rtpengine-transcoding", platform)
            self.assertIn("media_backend=rtpengine", platform)
            self.assertIn("media_driver=sipp-pcap", platform)
            self.assertIn("media_codec=PCMU", platform)
            self.assertIn("server_codec=PCMA", platform)
            self.assertIn("transcoding_owner=rtpengine", platform)
            self.assertIn("b2bua_uac_a_media_resolved.xml", sipp)

    def test_b2bua_load_rtpengine_transcoding_profile_sets_load_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "run_b2bua_sipp_smoke.py"),
                    "--dry-run",
                    "--output-root",
                    tmp,
                    "--run-id",
                    "load-rtpengine-transcoding-profile",
                    "--profile",
                    "load-5cps-60s-rtpengine-transcoding",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            log_dir = Path(tmp) / run_b2bua_sipp_smoke.DEFAULT_LOG_FOLDER / "load-rtpengine-transcoding-profile"
            platform = (log_dir / "log.platform").read_text(encoding="utf-8")
            self.assertIn("profile=load-5cps-60s-rtpengine-transcoding", platform)
            self.assertIn("calls=300", platform)
            self.assertIn("rate=5", platform)
            self.assertIn("hold_ms=60000", platform)
            self.assertIn("media_backend=rtpengine", platform)
            self.assertIn("media_driver=sipp-pcap", platform)
            self.assertIn("server_codec=PCMA", platform)
            self.assertIn("transcoding_expected=True", platform)
            self.assertIn("transcoding_owner=rtpengine", platform)
            self.assertIn("ladder_enabled=False", platform)

    def test_rtpengine_dockerfile_exposes_load_sized_media_range(self):
        dockerfile = (ROOT / "docker" / "rtpengine.Dockerfile").read_text(encoding="utf-8")

        self.assertIn("EXPOSE 30000-32000/udp", dockerfile)
        self.assertIn("--port-min=30000", dockerfile)
        self.assertIn("--port-max=32000", dockerfile)

    def test_playsbc_dockerfile_uses_current_piper_voice_download_cli(self):
        dockerfile = (ROOT / "docker" / "playsbc.Dockerfile").read_text(encoding="utf-8")

        self.assertIn("python3 -m piper.download_voices en_US-lessac-low --data-dir", dockerfile)
        self.assertIn("for attempt in 1 2 3 4 5", dockerfile)
        self.assertIn('if [ "$attempt" -eq 5 ]; then exit 1; fi', dockerfile)
        self.assertIn("sleep $((attempt * 15))", dockerfile)
        self.assertIn("test -s /opt/playsbc/models/piper/en_US-lessac-low.onnx", dockerfile)
        self.assertIn("test -s /opt/playsbc/models/piper/en_US-lessac-low.onnx.json", dockerfile)
        self.assertNotIn("--download-dir", dockerfile)

    def test_rtpengine_load_observation_uses_query_packet_totals(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            run_b2bua_sipp_smoke.initialize_log_dir(log_dir)
            run_b2bua_sipp_smoke.append_log_section(
                log_dir,
                "log.media",
                "B2BUA RTPENGINE CODEC POLICY",
                "offered=PCMU,101 target=PCMA policy=mask=PCMU transcode=PCMA",
            )
            run_b2bua_sipp_smoke.append_log_section(
                log_dir,
                "log.media",
                "B2BUA RTPENGINE ANSWER",
                "status=ok call_id=load-1 from_tag=1 to_tag=sipp-b-1 rewritten_sdp_bytes=230",
            )
            run_b2bua_sipp_smoke.append_log_section(
                log_dir,
                "log.media",
                "B2BUA RTPENGINE QUERY",
                "result=ok rtp_packets_total=6000 rtp_bytes_total=1032000 rtp_errors_total=0",
            )
            args = argparse_namespace(
                media_enabled=True,
                media_backend="rtpengine",
                media_driver="sipp-pcap",
                media_codec="PCMU",
                server_codec="PCMA",
                media_pcap_resolved="/tmp/g711u_60s.pcap",
                hold_ms=60000,
            )

            run_b2bua_sipp_smoke.append_media_observation(log_dir, args)
            run_b2bua_sipp_smoke.append_transcoding_observation(log_dir, args)

            media = (log_dir / "log.media").read_text(encoding="utf-8")
            transcoding = (log_dir / "log.transcoding").read_text(encoding="utf-8")
            self.assertIn("status=rtpengine_media_anchored", media)
            self.assertIn("rtpengine_query_count=1", media)
            self.assertIn("rtpengine_rtp_packets_total=6000", media)
            self.assertIn("status=delegated_and_media_confirmed", transcoding)
            self.assertIn("rtpengine_rtp_packets_total=6000", transcoding)

    def test_rtpengine_load_completeness_accepts_full_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            run_b2bua_sipp_smoke.initialize_log_dir(log_dir)
            args = argparse_namespace(
                profile="load-5cps-60s-rtpengine-transcoding",
                calls=300,
                hold_ms=60000,
                media_delivery_threshold_percent=99.5,
            )
            (log_dir / "log.media").write_text(
                "\n".join(
                    f"2026-07-03 20:00:00 | B2BUA RTPENGINE QUERY | call_id={index} | "
                    "result=ok rtp_packets_total=6000 rtp_bytes_total=1032000 rtp_errors_total=0"
                    for index in range(300)
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertTrue(run_b2bua_sipp_smoke.rtpengine_load_media_complete(log_dir, args))

            text = (log_dir / "log.media").read_text(encoding="utf-8")
            self.assertIn("expected_rtp_packets=1800000 observed_rtp_packets=1800000", text)
            self.assertIn("media_delivery_percent=100.000 media_loss_percent=0.000", text)

    def test_rtpengine_load_query_drain_counts_success_and_failure_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            (log_dir / "log.media").write_text(
                "\n".join(
                    [
                        "2026-07-03 20:00:00 | B2BUA RTPENGINE QUERY | call_id=1 | result=ok",
                        "2026-07-03 20:00:01 | B2BUA RTPENGINE QUERY FAILED | call_id=2 | error_type=TimeoutError",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            observed, duration = run_b2bua_sipp_smoke.wait_for_rtpengine_load_queries(log_dir, 2, timeout=0)

            self.assertEqual(observed, 2)
            self.assertGreaterEqual(duration, 0)

    def test_rtpengine_load_completeness_uses_strict_delivery_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            run_b2bua_sipp_smoke.initialize_log_dir(log_dir)
            args = argparse_namespace(
                profile="load-5cps-60s-rtpengine-transcoding",
                calls=2,
                hold_ms=60000,
                media_delivery_threshold_percent=99.5,
            )
            (log_dir / "log.media").write_text(
                "\n".join(
                    [
                        "2026-07-03 20:00:00 | B2BUA RTPENGINE QUERY | call_id=1 | "
                        "result=ok rtp_packets_total=5970 rtp_bytes_total=1 rtp_errors_total=0 query_retry_count=1",
                        "2026-07-03 20:00:01 | B2BUA RTPENGINE QUERY | call_id=2 | "
                        "result=ok rtp_packets_total=5970 rtp_bytes_total=1 rtp_errors_total=0 query_retry_count=0",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertTrue(run_b2bua_sipp_smoke.rtpengine_load_media_complete(log_dir, args))

            text = (log_dir / "log.media").read_text(encoding="utf-8")
            self.assertIn("required_rtp_packets=11940", text)
            self.assertIn("required_rtp_packets_per_call=5940", text)
            self.assertIn("query_failures=0 query_retries=1", text)
            self.assertIn("per_call_rtp_packets_min=5970 per_call_rtp_packets_max=5970", text)

    def test_rtpengine_load_completeness_rejects_failed_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            run_b2bua_sipp_smoke.initialize_log_dir(log_dir)
            args = argparse_namespace(
                profile="load-5cps-60s-rtpengine-transcoding",
                calls=2,
                hold_ms=60000,
                media_delivery_threshold_percent=99.5,
            )
            (log_dir / "log.media").write_text(
                "\n".join(
                    [
                        "2026-07-03 20:00:00 | B2BUA RTPENGINE QUERY | call_id=1 | "
                        "result=ok rtp_packets_total=6000 rtp_bytes_total=1 rtp_errors_total=0",
                        "2026-07-03 20:00:01 | B2BUA RTPENGINE QUERY FAILED | call_id=2 | "
                        "error_type=TimeoutError error=no additional detail",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertFalse(run_b2bua_sipp_smoke.rtpengine_load_media_complete(log_dir, args))

            stats = run_b2bua_sipp_smoke.rtpengine_query_stats(log_dir)
            self.assertEqual(stats["query_count"], 1)
            self.assertEqual(stats["query_failures"], 1)

    def test_rtpengine_load_completeness_rejects_delivery_below_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            run_b2bua_sipp_smoke.initialize_log_dir(log_dir)
            args = argparse_namespace(
                profile="load-5cps-60s-rtpengine-transcoding",
                calls=2,
                hold_ms=60000,
                media_delivery_threshold_percent=99.5,
            )
            (log_dir / "log.media").write_text(
                "\n".join(
                    f"2026-07-03 20:00:0{index} | B2BUA RTPENGINE QUERY | call_id={index} | "
                    f"result=ok rtp_packets_total={packets} rtp_bytes_total=1 rtp_errors_total=0"
                    for index, packets in ((1, 5969), (2, 5970))
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertFalse(run_b2bua_sipp_smoke.rtpengine_load_media_complete(log_dir, args))

    def test_rtpengine_load_completeness_rejects_bad_individual_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            run_b2bua_sipp_smoke.initialize_log_dir(log_dir)
            args = argparse_namespace(
                profile="load-5cps-60s-rtpengine-transcoding",
                calls=2,
                hold_ms=60000,
                media_delivery_threshold_percent=90.0,
                media_per_call_threshold_percent=99.0,
            )
            (log_dir / "log.media").write_text(
                "\n".join(
                    f"2026-07-03 20:00:0{index} | B2BUA RTPENGINE QUERY | call_id={index} | "
                    f"result=ok rtp_packets_total={packets} rtp_bytes_total=1 rtp_errors_total=0"
                    for index, packets in ((1, 5900), (2, 6000))
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertFalse(run_b2bua_sipp_smoke.rtpengine_load_media_complete(log_dir, args))


class RealTopologyTests(unittest.TestCase):
    def test_topology_waits_for_all_one_shot_services_together(self):
        completed = subprocess.CompletedProcess(["docker", "compose", "wait"], 0, "0\n0\n0\n0\n", "")
        with mock.patch.object(run_real_topology, "run", return_value=completed) as mocked_run:
            codes = run_real_topology.wait_services_exit(["sipp-a", "sipp-b", "rtcp-a", "rtcp-b"], {})

        self.assertEqual(codes, {"sipp-a": 0, "sipp-b": 0, "rtcp-a": 0, "rtcp-b": 0})
        command = mocked_run.call_args.args[0]
        self.assertEqual(command[-5:], ["wait", "sipp-a", "sipp-b", "rtcp-a", "rtcp-b"])

    def test_dual_realm_compose_has_isolated_core_and_peer_addresses(self):
        compose = (ROOT / "docker-compose.topology.yml").read_text(encoding="utf-8")

        self.assertIn("subnet: 172.28.0.0/24", compose)
        self.assertIn("subnet: 192.168.28.0/24", compose)
        self.assertIn("ipv4_address: 172.28.0.20", compose)
        self.assertIn("ipv4_address: 192.168.28.20", compose)
        self.assertIn("--interface=core/172.28.0.40", compose)
        self.assertIn("--interface=peer/192.168.28.40", compose)
        self.assertIn("network_mode: service:rtpengine", compose)
        self.assertIn("core-agent:", compose)
        self.assertIn("peer-agent:", compose)
        self.assertIn("network_mode: service:core-agent", compose)
        self.assertIn("network_mode: service:peer-agent", compose)

    def test_topology_helm_values_select_dual_rtpengine_directions(self):
        values = (ROOT / "configs" / "topology" / "helm-values.yaml").read_text(encoding="utf-8")

        self.assertIn("sip_advertised_ip: 172.28.0.20", values)
        self.assertIn("b2bua_advertised_ip: 192.168.28.20", values)
        self.assertIn("rtpengine_url: udp://172.28.0.40:2223", values)
        self.assertIn("rtpengine_directions:", values)
        self.assertIn("- core", values)
        self.assertIn("- peer", values)

    def test_topology_runner_tracks_every_compose_image(self):
        self.assertEqual(len(run_real_topology.TOPOLOGY_IMAGES), 3)
        self.assertIn("playsbc-real-topology-playsbc:latest", run_real_topology.TOPOLOGY_IMAGES)
        self.assertIn("playsbc-real-topology-rtpengine:latest", run_real_topology.TOPOLOGY_IMAGES)
        self.assertIn("playsbc-real-topology-sipp:latest", run_real_topology.TOPOLOGY_IMAGES)

    def test_dual_realm_profile_places_uac_and_uas_on_opposite_realms(self):
        args = run_dual_realm_profile.profile_args("basic-media", "regression-test", "b2bua-Regression")
        uac = run_dual_realm_profile.uac_command(args, "/scenarios/b2bua_uac_a_media.xml")
        uas = run_dual_realm_profile.uas_command(args, "/scenarios/b2bua_uas_b_media.xml")

        self.assertEqual(uac[1], "172.28.0.20:5060")
        self.assertEqual(uac[uac.index("-i") + 1], "172.28.0.10")
        self.assertEqual(uas[uas.index("-i") + 1], "192.168.28.30")
        self.assertEqual(args.rtpengine_url, "udp://172.28.0.40:2223")

    def test_dual_realm_ai_profile_targets_rasa_mock_and_skips_peer_uas(self):
        args = run_dual_realm_profile.profile_args("ai-rasa-lab", "ai-call", "b2bua-Regression")
        uac = run_dual_realm_profile.uac_command(args, "/output/work/sipp-a-uac/ai.xml")

        self.assertTrue(run_dual_realm_profile.needs_ai_mock(args))
        self.assertFalse(args.start_uas)
        self.assertFalse(args.register_callee)
        self.assertIn("172.28.0.60:5005", args.ai_voice_gateway["rasa_webhook_url"])
        self.assertEqual(uac[1], "172.28.0.20:5060")
        self.assertIn("log.ai", run_regression_suite.B2BUA_LOG_FILES)
        self.assertIn("log.ai", run_b2bua_sipp_smoke.LOG_FILES)
        self.assertIn("sipmsg.log", run_regression_suite.B2BUA_LOG_FILES)
        self.assertIn("sipmsg.log", run_b2bua_sipp_smoke.LOG_FILES)
        self.assertEqual(run_b2bua_sipp_smoke.rtcp_expected_sender_names(args), ("rtcp-a",))

    def test_dual_realm_ai_rtpengine_profile_anchors_media_with_rtpengine(self):
        args = run_dual_realm_profile.profile_args("ai-rasa-rtpengine", "ai-rtpengine", "b2bua-Regression")

        self.assertTrue(run_dual_realm_profile.needs_ai_mock(args))
        self.assertEqual(args.media_backend, "rtpengine")
        self.assertFalse(args.start_uas)
        self.assertEqual(args.rasa_mock_response_count, 2)
        self.assertEqual(args.rasa_mock_action, "transfer")
        self.assertEqual(run_b2bua_sipp_smoke.rtcp_expected_sender_names(args), ("rtcp-a",))

    def test_dual_realm_real_rasa_profile_uses_optional_real_service(self):
        args = run_dual_realm_profile.profile_args("ai-rasa-real-lab", "ai-real", "b2bua-Regression")

        self.assertEqual(args.rasa_deployment, "real")
        self.assertTrue(run_dual_realm_profile.needs_real_rasa(args))
        self.assertFalse(run_dual_realm_profile.needs_ai_mock(args))
        self.assertIn("172.28.0.61:5005", args.ai_voice_gateway["rasa_webhook_url"])
        self.assertEqual(args.route_policies[0]["target"], "ai-gateway:rasa-support")
        self.assertEqual(run_b2bua_sipp_smoke.rtcp_expected_sender_names(args), ("rtcp-a",))

    def test_dual_realm_contact_center_sales_profile_uses_virtual_sipp_b_agent(self):
        args = run_dual_realm_profile.profile_args("ai-rasa-contact-center-sales", "ai-sales", "b2bua-Regression")

        self.assertEqual(args.rasa_deployment, "real")
        self.assertFalse(args.start_uas)
        self.assertFalse(args.register_callee)
        self.assertTrue(run_dual_realm_profile.needs_real_rasa(args))
        self.assertEqual(args.media_backend, "rtpengine")
        self.assertEqual(args.ai_voice_gateway["agent_label"], "SIPp B Bot Agent")
        self.assertEqual(args.ai_voice_gateway["contact_center_queue"], "sales")
        self.assertEqual(args.ai_voice_gateway["speech_input_pcap"], "sipp/scenarios/pcap/ai_contact_center_sales_g711u.pcap")
        self.assertEqual(args.route_policies[0]["target"], "ai-gateway:sales-support")
        self.assertEqual(run_b2bua_sipp_smoke.rtcp_expected_sender_names(args), ("rtcp-a",))

    def test_dual_realm_ai_whisper_profile_uses_whisper_adapter_boundary(self):
        args = run_dual_realm_profile.profile_args("ai-rasa-rtpengine-speech-whisper", "ai-whisper", "b2bua-Regression")

        self.assertEqual(args.rasa_deployment, "real")
        self.assertTrue(run_dual_realm_profile.needs_real_rasa(args))
        self.assertEqual(args.media_backend, "rtpengine")
        self.assertEqual(args.ai_voice_gateway["stt_provider"], "whisper")
        self.assertIn("whisper_stt_wrapper.py", args.ai_voice_gateway["stt_command"])
        self.assertIn("--allow-lab-fallback", args.ai_voice_gateway["stt_command"])
        self.assertEqual(args.ai_voice_gateway["tts_provider"], "piper")
        self.assertEqual(args.route_policies[0]["target"], "ai-gateway:rasa-support")
        self.assertEqual(run_b2bua_sipp_smoke.rtcp_expected_sender_names(args), ("rtcp-a",))

    def test_dual_realm_ai_streaming_profile_generates_chunked_tts_prompts(self):
        args = run_dual_realm_profile.profile_args("ai-rasa-long-response-streaming", "ai-stream", "b2bua-Regression")

        self.assertFalse(run_dual_realm_profile.needs_ai_mock(args))
        self.assertTrue(run_dual_realm_profile.needs_real_rasa(args))
        self.assertEqual(args.media_backend, "rtpengine")
        self.assertEqual(args.ai_voice_gateway["response_mode"], "streaming")
        self.assertEqual(args.ai_voice_gateway["tts_chunk_chars"], 120)
        self.assertEqual(args.ai_voice_gateway["tts_provider"], "piper")
        self.assertIn("piper_tts_wrapper.py", args.ai_voice_gateway["tts_command"])
        self.assertEqual(run_b2bua_sipp_smoke.rtcp_expected_sender_names(args), ("rtcp-a",))

    def test_dual_realm_contact_center_coqui_profile_uses_coqui_tts(self):
        args = run_dual_realm_profile.profile_args("ai-rasa-contact-center-sales-coqui", "ai-coqui", "b2bua-Regression")

        self.assertEqual(args.rasa_deployment, "real")
        self.assertTrue(run_dual_realm_profile.needs_real_rasa(args))
        self.assertEqual(args.media_backend, "rtpengine")
        self.assertEqual(args.ai_voice_gateway["agent_label"], "SIPp B Bot Agent")
        self.assertEqual(args.ai_voice_gateway["contact_center_queue"], "sales")
        self.assertEqual(args.ai_voice_gateway["stt_provider"], "vosk")
        self.assertEqual(args.ai_voice_gateway["tts_provider"], "coqui")
        self.assertIn("coqui_tts_wrapper.py", args.ai_voice_gateway["tts_command"])
        self.assertIn("--allow-lab-fallback", args.ai_voice_gateway["tts_command"])
        self.assertEqual(args.route_policies[0]["target"], "ai-gateway:sales-support")
        self.assertEqual(run_b2bua_sipp_smoke.rtcp_expected_sender_names(args), ("rtcp-a",))

    def test_dual_realm_ha_profiles_render_shared_state_and_pairing(self):
        basic = run_dual_realm_profile.profile_args("basic-signalling", "ha-all-basic", "b2bua-Regression")
        self.assertTrue(basic.ha["enabled"])
        self.assertEqual(basic.ha["node_id"], "playsbc-a")
        self.assertEqual(basic.ha["load_balancing"]["policy"], "external-lb")
        self.assertEqual(len(basic.ha["nodes"]), 2)

        args = run_dual_realm_profile.profile_args("ha-shared-state-rtpengine", "ha-call", "b2bua-Regression")

        self.assertEqual(args.media_backend, "rtpengine")
        self.assertTrue(args.ha["enabled"])
        self.assertEqual(args.ha["node_id"], "playsbc-a")
        self.assertIn("{rtpengine_url}", args.ha["rtpengine_pairs"][0]["rtpengine_url"])
        self.assertIn("HA RTPENGINE PAIR SELECTED", args.expected_log_markers["log.platform"])

        probe = run_dual_realm_profile.profile_args("ha-options-health-recovery", "ha-probe", "b2bua-Regression")
        self.assertTrue(probe.run_call)
        self.assertFalse(probe.start_uas)
        self.assertEqual(probe.uac_scenario, "options.xml")
        self.assertTrue(probe.ha["enabled"])
        self.assertTrue(probe.trunk_groups[0]["members"][0]["options_probe"]["enabled"])
        self.assertEqual(probe.trunk_groups[0]["members"][0]["options_probe"]["recovery_successes"], 1)
        rendered_trunks = run_b2bua_sipp_smoke.render_harness_config_templates(probe.trunk_groups, probe)
        self.assertEqual(rendered_trunks[0]["members"][0]["uri"], "sip:options@172.28.0.20:5060")

        draining = run_dual_realm_profile.profile_args("ha-node-draining", "ha-drain", "b2bua-Regression")
        self.assertTrue(draining.ha["enabled"])
        self.assertEqual(draining.ha["nodes"][0]["state"], "draining")
        self.assertEqual(draining.uac_scenario, "b2bua_uac_failed_outbound.xml")

    def test_all_dual_realm_regression_profiles_render_ha_enabled(self):
        for profile in run_regression_suite.ALL_B2BUA_PROFILES:
            with self.subTest(profile=profile):
                args = run_dual_realm_profile.profile_args(profile, f"ha-all-{profile}", "b2bua-Regression")
                self.assertTrue(args.ha["enabled"])
                self.assertEqual(args.ha["cluster_id"], "playsbc-aa-lab")
                self.assertGreaterEqual(len(args.ha["nodes"]), 2)
                self.assertIn("load_balancing", args.ha)

    def test_dual_realm_mixed_tls_srtp_profile_uses_independent_leg_transports(self):
        args = run_dual_realm_profile.profile_args("tls-srtp-to-tcp-rtp", "secure-call", "b2bua-Regression")
        uac = run_dual_realm_profile.uac_command(args, "/output/work/sipp-a-uac/secure.xml")
        uas = run_dual_realm_profile.uas_command(args, "/output/work/sipp-b-uas/plain.xml")

        self.assertEqual(args.uac_transport, "tls")
        self.assertEqual(args.uas_transport, "tcp")
        self.assertEqual(uac[1], "172.28.0.20:5061")
        self.assertEqual(uac[uac.index("-t") + 1], "ln")
        self.assertEqual(uas[uas.index("-t") + 1], "t1")
        self.assertEqual(args.rtpengine_offer_transport_protocol, "RTP/AVP")
        self.assertEqual(args.rtpengine_answer_transport_protocol, "RTP/SAVP")
        self.assertIn("no-AEAD_AES_256_GCM", args.rtpengine_sdes)
        self.assertIn("no-NULL_HMAC_SHA1_32", args.rtpengine_sdes)
        self.assertNotIn("no-AES_CM_128_HMAC_SHA1_80", args.rtpengine_sdes)
        self.assertEqual(args.rtpengine_dtls, "disable")

    def test_secure_media_profile_generates_savp_and_plain_avp_scenarios(self):
        args = run_dual_realm_profile.profile_args("tls-srtp-to-udp-rtp", "secure-call", "b2bua-Regression")
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "sipp-a-uac").mkdir()
            (work / "sipp-b-uas").mkdir()
            run_b2bua_sipp_smoke.prepare_media_scenarios(args, work)
            secure = Path(args.uac_scenario).read_text(encoding="ISO-8859-1")
            plain = Path(args.uas_scenario).read_text(encoding="ISO-8859-1")

        self.assertIn("RTP/SAVP", secure)
        self.assertIn("AES_CM_128_HMAC_SHA1_80", secure)
        self.assertIn(run_b2bua_sipp_smoke.SRTP_TEST_MASTER_KEY_SALT, secure)
        self.assertIn(
            f"m=audio {run_b2bua_sipp_smoke.SRTP_TEST_MEDIA_PORT} RTP/SAVP",
            secure,
        )
        self.assertIn(
            f"a=rtcp:{run_b2bua_sipp_smoke.SRTP_TEST_RTCP_PORT}",
            secure,
        )
        self.assertNotIn("play_pcap_audio", secure)
        self.assertNotIn("send_srtp_audio.py", secure)
        self.assertNotIn("<exec command=", secure)
        self.assertNotIn("rtp_echo=", secure)
        self.assertIn("RTP/AVP", plain)
        self.assertIn("play_pcap_audio", plain)

    def test_kubernetes_secure_media_profiles_render_sdes_on_the_secure_leg(self):
        for profile_name, secure_role, plain_role in (
            ("tls-srtp-to-udp-rtp", "uac", "uas"),
            ("udp-rtp-to-tls-srtp", "uas", "uac"),
        ):
            with self.subTest(profile=profile_name):
                profile = run_k8s_regression.profile_values(profile_name, "secure-k8s")
                secure = run_k8s_regression.rendered_scenario(profile, secure_role)
                plain = run_k8s_regression.rendered_scenario(profile, plain_role)

                self.assertIn("RTP/SAVP", secure)
                self.assertIn("a=crypto:", secure)
                self.assertNotIn("send_srtp_audio.py", secure)
                self.assertNotIn("<exec command=", secure)
                self.assertIn(
                    f"m=audio {run_b2bua_sipp_smoke.SRTP_TEST_MEDIA_PORT} RTP/SAVP",
                    secure,
                )
                self.assertNotIn("rtp_echo=", secure)
                self.assertNotIn("play_pcap_audio", secure)
                self.assertIn("RTP/AVP", plain)
                self.assertIn("m=audio [media_port] RTP/AVP", plain)
                self.assertNotIn("a=crypto:", plain)
                self.assertIn("play_pcap_audio", plain)

    def test_kubernetes_secure_media_profiles_enable_srtp_diagnostics_only_on_secure_leg(self):
        runner = object.__new__(run_k8s_regression.K8sRegressionRunner)
        runner.args = SimpleNamespace(
            callee="1002",
            caller="1001",
            call_hold_ms=1000,
            service="playsbc-playsbc",
            sip_port=5062,
            tls_port=5061,
        )

        for profile_name, secure_role in (
            ("tls-srtp-to-udp-rtp", "uac"),
            ("udp-rtp-to-tls-srtp", "uas"),
        ):
            with self.subTest(profile=profile_name):
                profile = run_k8s_regression.profile_values(profile_name, "secure-k8s")
                uac = runner.b2bua_uac_args(profile, "/tmp/uac.xml", "10.244.0.10")
                uas = runner.b2bua_uas_args(profile, "/tmp/uas.xml", "10.244.0.11")
                secure = uac if secure_role == "uac" else uas
                plain = uas if secure_role == "uac" else uac

                self.assertIn("-rtpcheck_debug", secure)
                self.assertIn("-srtpcheck_debug", secure)
                self.assertNotIn("-rtpcheck_debug", plain)
                self.assertNotIn("-srtpcheck_debug", plain)

    def test_srtp_sender_command_is_runner_managed_without_shell_operators(self):
        command = run_b2bua_sipp_smoke.srtp_sender_command("10.244.0.25")

        self.assertEqual(command[:3], ["python3", "/app/tools/send_srtp_audio.py", "--bind-ip"])
        self.assertEqual(command[3], "10.244.0.25")
        self.assertEqual(
            command[command.index("--port") + 1],
            str(run_b2bua_sipp_smoke.SRTP_TEST_MEDIA_PORT),
        )
        self.assertEqual(command[command.index("--wait-timeout") + 1], "30")
        self.assertFalse(any("&" in value or ">" in value for value in command))

    def test_kubernetes_srtp_sender_runs_in_the_secure_endpoint_pod(self):
        runner = object.__new__(run_k8s_regression.K8sRegressionRunner)
        runner.args = SimpleNamespace(kubectl_bin="kubectl", namespace="playsbc")
        process = mock.Mock()

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(run_k8s_regression.subprocess, "Popen", return_value=process) as popen, \
                mock.patch.object(run_k8s_regression, "wait_for_process_log_marker", return_value=True):
            sender = runner.start_srtp_sender(
                SimpleNamespace(uac_srtp=False, uas_srtp=True),
                "core-pod",
                "10.244.0.10",
                "peer-pod",
                "10.244.0.11",
                Path(tmp),
            )
            self.assertIsNotNone(sender)
            command = popen.call_args.args[0]
            self.assertEqual(command[:7], ["kubectl", "-n", "playsbc", "exec", "peer-pod", "--", "python3"])
            self.assertEqual(command[command.index("--bind-ip") + 1], "10.244.0.11")
            runner.close_process_files(process)

    def test_kubernetes_trace_collection_includes_srtp_sender_and_debug_logs(self):
        source = inspect.getsource(run_k8s_regression.K8sRegressionRunner.collect_sipp_traces)

        self.assertIn("/tmp/*srtp*.log", source)

    def test_sipp_docker_image_is_built_with_tls_and_pcap(self):
        dockerfile = (ROOT / "docker" / "sipp.Dockerfile").read_text(encoding="utf-8")

        self.assertIn("SIPP_VERSION=v3.7.7", dockerfile)
        self.assertIn("-DUSE_SSL=1", dockerfile)
        self.assertIn("-DUSE_PCAP=1", dockerfile)
        self.assertIn("python3", dockerfile)
        self.assertIn("openssl", dockerfile)
        self.assertIn("send_rtcp_reports.py", dockerfile)
        self.assertIn("send_srtp_audio.py", dockerfile)

    def test_srtp_sender_matches_rfc3711_session_key_vectors(self):
        master_key_salt = bytes.fromhex(
            "E1F97A0D3E018BE0D64FA32C06DE4139"
            "0EC675AD498AFEEBB6960B3AABE6"
        )
        encryption_key, authentication_key, salt = send_srtp_audio.session_keys(master_key_salt)

        self.assertEqual(encryption_key.hex().upper(), "C61E7A93744F39EE10734AFE3FF7A087")
        self.assertEqual(authentication_key.hex().upper(), "CEBE321F6FF7716B6FD4AB49AF256A156D38BAA4")
        self.assertEqual(salt.hex().upper(), "30CBBC08863D8C85D49DB34A9AE1")

    def test_srtp_sender_builds_authenticated_pcmu_packet(self):
        encryption_key, authentication_key, salt = send_srtp_audio.session_keys(
            base64.b64decode(send_srtp_audio.DEFAULT_MASTER_KEY_SALT)
        )
        packet = send_srtp_audio.srtp_packet(
            bytes([0xFF]) * 160,
            sequence=1000,
            timestamp=0,
            ssrc=0x53525450,
            encryption_key=encryption_key,
            authentication_key=authentication_key,
            session_salt=salt,
        )

        self.assertEqual(len(packet), 12 + 160 + 10)
        self.assertEqual(packet[:2], b"\x80\x00")
        self.assertNotEqual(packet[12:172], bytes([0xFF]) * 160)
        expected_tag = hmac.new(
            authentication_key,
            packet[:-10] + struct.pack("!I", 0),
            hashlib.sha1,
        ).digest()[:10]
        self.assertEqual(packet[-10:], expected_tag)

    def test_kubernetes_chart_has_health_secret_rtpengine_and_affinity_lab(self):
        chart = ROOT / "charts" / "playsbc"
        deployment = (chart / "templates" / "deployment.yaml").read_text(encoding="utf-8")
        configmap = (chart / "templates" / "configmap.yaml").read_text(encoding="utf-8")
        rtpengine = (chart / "templates" / "rtpengine.yaml").read_text(encoding="utf-8")
        rasa = (chart / "templates" / "rasa.yaml").read_text(encoding="utf-8")
        kind_values = (ROOT / "configs" / "kubernetes" / "kind-values.yaml").read_text(encoding="utf-8")
        ai_rasa_values = (ROOT / "configs" / "kubernetes" / "ai-rasa-real-values.yaml").read_text(
            encoding="utf-8"
        )

        self.assertIn("readinessProbe:", deployment)
        self.assertIn("livenessProbe:", deployment)
        self.assertIn("users_file", configmap)
        self.assertIn("rtpengine_url", configmap)
        self.assertIn("status.hostIP", rtpengine)
        self.assertIn("sessionAffinity", rtpengine)
        self.assertIn("rtpengine:\n  enabled: true", kind_values)
        self.assertIn("startupProbe:", rasa)
        self.assertNotIn(".Values.rasa.enabled", configmap)
        self.assertNotIn(".Values.rasa.enabled", rasa)
        self.assertIn(".Values.rasa | default dict", configmap)
        self.assertIn(".Values.rasa | default dict", rasa)
        self.assertIn("name: {{ include \"playsbc.fullname\" . }}-rasa", rasa)
        self.assertIn("rasa train", rasa)
        self.assertIn("rasa run --enable-api", rasa)
        self.assertIn("-i 0.0.0.0", rasa)
        self.assertNotIn("--host 0.0.0.0", rasa)
        self.assertIn("rasa:\n  enabled: true", ai_rasa_values)
        self.assertIn("target: ai-gateway:rasa-support", ai_rasa_values)

    def test_dual_realm_load_profiles_use_bounded_media_capture(self):
        internal = run_dual_realm_profile.profile_args("load-5cps-60s", "internal-load", "b2bua-Regression")
        internal.media_codec = "PCMU"
        internal.media_enabled = True
        rtpengine = run_dual_realm_profile.profile_args(
            "load-5cps-60s-rtpengine-transcoding",
            "rtpengine-load",
            "b2bua-Regression",
        )

        self.assertIn("capture-internal-media-ring", run_dual_realm_profile.capture_services(internal))
        self.assertIn("capture-media-ring", run_dual_realm_profile.capture_services(rtpengine))

    def test_kubernetes_load_profiles_use_expanded_sipp_timeout(self):
        args = run_k8s_regression.parse_args(["--all-profiles"])
        runner = run_k8s_regression.K8sRegressionRunner(args, "unit-k8s")
        internal = run_k8s_regression.profile_values("load-5cps-60s", "unit-k8s")
        rtpengine = run_k8s_regression.profile_values("load-5cps-60s-rtpengine-transcoding", "unit-k8s")

        self.assertEqual(run_k8s_regression.sipp_timeout_seconds(300, 5, 60000), 180)
        self.assertGreaterEqual(run_k8s_regression.k8s_sipp_timeout_seconds(internal), 300)
        self.assertGreaterEqual(run_k8s_regression.k8s_sipp_timeout_seconds(rtpengine), 600)
        self.assertIn(str(run_k8s_regression.k8s_sipp_timeout_seconds(internal)), runner.b2bua_base_args(internal, "10.244.0.10", 5060))
        self.assertIn(str(run_k8s_regression.k8s_sipp_timeout_seconds(rtpengine)), runner.b2bua_base_args(rtpengine, "10.244.0.11", 5060))

    def test_dual_realm_evidence_cleanup_removes_temporary_work_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "work"
            (work / "sipp-a-uac").mkdir(parents=True)
            (work / "sipp-a-uac" / "trace.log").write_text("temporary", encoding="utf-8")

            self.assertTrue(run_dual_realm_profile.cleanup_work_dir(work))
            self.assertFalse(work.exists())

    def test_kubernetes_profiles_advertise_pod_ip_for_media_sdp(self):
        args = run_k8s_regression.parse_args(["--all-profiles"])
        runner = run_k8s_regression.K8sRegressionRunner(args, "unit-k8s")

        internal = run_k8s_regression.profile_values("basic-media", "unit-k8s")
        rtpengine = run_k8s_regression.profile_values("rtpengine-media", "unit-k8s")

        self.assertEqual(runner.profile_config(internal)["sip_advertised_ip"], "$POD_IP")
        self.assertEqual(runner.profile_config(internal)["b2bua_advertised_ip"], "$POD_IP")
        self.assertEqual(runner.profile_config(rtpengine)["sip_advertised_ip"], "$POD_IP")
        self.assertEqual(runner.profile_config(internal)["log_dir"], "/tmp/playsbc-logs")
        self.assertEqual(runner.profile_config(rtpengine)["log_dir"], "/tmp/playsbc-logs")
        self.assertIn("rtpengine_g711_only", runner.profile_config(rtpengine))
        self.assertIn("rtpengine_plain_rtp_sdp", runner.profile_config(rtpengine))
        self.assertIn("rtpengine_sip_source_address", runner.profile_config(rtpengine))
        self.assertIn("rtpengine_media_handover", runner.profile_config(rtpengine))
        self.assertIn("rtpengine_nat_wait", runner.profile_config(rtpengine))
        self.assertIn("rtpengine_pierce_nat", runner.profile_config(rtpengine))
        ha = runner.profile_config(rtpengine)["ha"]
        self.assertTrue(ha["enabled"])
        self.assertEqual(ha["node_id"], "$POD_NAME")
        self.assertEqual(ha["cluster_id"], "playsbc-aa-lab")
        self.assertEqual(ha["nodes"][0]["node_id"], "playsbc-playsbc-0")
        self.assertEqual(
            ha["rtpengine_pairs"][1]["rtpengine_url"],
            "udp://playsbc-playsbc-rtpengine-1.playsbc-playsbc-rtpengine-headless:2223",
        )
        self.assertEqual(ha["failover"]["mid_call_failover"], "dialog-restore-only")

    def test_kubernetes_business_service_profiles_preserve_policy_and_timeout(self):
        args = run_k8s_regression.parse_args(["--profile", "rfc5359-forwarding-on-no-answer"])
        runner = run_k8s_regression.K8sRegressionRunner(args, "unit-k8s")
        profile = run_k8s_regression.profile_values(
            "rfc5359-forwarding-on-no-answer",
            "unit-k8s",
        )

        config = runner.profile_config(profile)

        self.assertEqual(config["b2bua_invite_timeout"], 1.0)
        self.assertEqual(
            config["business_services"]["forwarding"]["rules"][0]["condition"],
            "no-answer",
        )
        self.assertEqual(
            config["business_services"]["forwarding"]["rules"][0]["target"],
            "forward-target",
        )

    def test_kubernetes_profile_auth_secret_does_not_leak_from_real_device_values(self):
        args = run_k8s_regression.parse_args(["--aks-profiles", "--aks-mode"])
        runner = run_k8s_regression.K8sRegressionRunner(args, "unit-k8s")
        inherited = {
            "authSecret": {
                "enabled": True,
                "existingSecret": "playsbc-real-device-users",
                "users": {"1001": "secret-password", "1002": "secret-password"},
            }
        }

        open_register = copy.deepcopy(inherited)
        runner.apply_profile_auth_secret_values(
            open_register,
            run_k8s_regression.profile_values("registered-inbound", "unit-k8s"),
        )

        self.assertFalse(open_register["authSecret"]["enabled"])
        self.assertEqual(open_register["authSecret"]["existingSecret"], "")
        self.assertEqual(open_register["authSecret"]["users"], {})

        digest_register = copy.deepcopy(inherited)
        runner.apply_profile_auth_secret_values(
            digest_register,
            run_k8s_regression.profile_values("register-auth-success", "unit-k8s"),
        )

        self.assertTrue(digest_register["authSecret"]["enabled"])
        self.assertEqual(digest_register["authSecret"]["existingSecret"], "")
        self.assertEqual(digest_register["authSecret"]["users"], {"1001": "secret-password"})

    def test_kubernetes_active_active_ha_profiles_normalize_legacy_node_aliases(self):
        args = run_k8s_regression.parse_args(["--profile", "ha-node-draining"])
        runner = run_k8s_regression.K8sRegressionRunner(args, "unit-k8s")
        profile = run_k8s_regression.profile_values("ha-node-draining", "unit-k8s")
        ha = runner.profile_config(profile)["ha"]

        self.assertEqual(ha["node_id"], "$POD_NAME")
        self.assertEqual(ha["shared_state_path"], "/var/lib/playsbc/ha-state.sqlite3")
        self.assertTrue(ha["draining"])
        self.assertEqual(ha["nodes"][0]["node_id"], "playsbc-playsbc-0")
        self.assertIn("playsbc-a", ha["nodes"][0]["aliases"])

    def test_kubernetes_ha_ladder_names_play_sbc_and_rtpengine_replicas(self):
        args = run_k8s_regression.parse_args(["--profile", "ha-playsbc-midcall-failover"])
        runner = run_k8s_regression.K8sRegressionRunner(args, "unit-k8s")
        profile = run_k8s_regression.profile_values("ha-playsbc-midcall-failover", "unit-k8s")

        ladder = runner.dual_realm_ladder(profile)

        self.assertIn("PlaySBC-1", ladder)
        self.assertIn("PlaySBC-2", ladder)
        self.assertIn("RTPengine-1", ladder)
        self.assertIn("RTPengine-2", ladder)
        self.assertIn("dialog restore", ladder)

    def test_kubernetes_ha_profiles_emit_transcoding_evidence(self):
        shared = run_k8s_regression.profile_values("ha-shared-state-rtpengine", "unit-k8s")
        load = run_k8s_regression.profile_values("ha-active-active-load-distribution", "unit-k8s")

        self.assertEqual(shared.media_codec, "PCMU")
        self.assertEqual(shared.server_codec, "PCMA")
        self.assertEqual(load.media_codec, "PCMU")
        self.assertEqual(load.server_codec, "PCMA")
        self.assertEqual(load.k8s_service_session_affinity, "None")

    def test_kubernetes_rtpengine_interface_failure_survives_active_active_defaults(self):
        args = run_k8s_regression.parse_args(["--profile", "rtpengine-interface-failure"])
        runner = run_k8s_regression.K8sRegressionRunner(args, "unit-k8s")
        profile = run_k8s_regression.profile_values("rtpengine-interface-failure", "unit-k8s")
        config = runner.profile_config(profile)
        configmap = (ROOT / "charts" / "playsbc" / "templates" / "configmap.yaml").read_text(encoding="utf-8")

        self.assertEqual(config["rtpengine_directions"], ["missing-core", "missing-peer"])
        self.assertEqual(config["rtpengine_interfaces"], ["core", "peer"])
        self.assertIn('if not (get $config "rtpengine_directions")', configmap)
        self.assertIn('if not (get $config "rtpengine_interfaces")', configmap)

    def test_kubernetes_profiles_can_disable_active_active_topology(self):
        args = run_k8s_regression.parse_args(["--all-profiles", "--no-active-active-topology"])
        runner = run_k8s_regression.K8sRegressionRunner(args, "unit-k8s")
        profile = run_k8s_regression.profile_values("basic-media", "unit-k8s")
        values = {
            "replicaCount": 2,
            "topology": {"activeActive": {"enabled": True, "useStatefulSet": True}},
            "rtpengine": {"replicas": 2, "hostNetwork": True},
        }

        self.assertEqual(runner.profile_config(profile)["ha"], {})
        runner.apply_active_active_values(values, profile)
        self.assertEqual(values["topology"]["model"], "core-peer-single-workload")
        self.assertFalse(values["topology"]["activeActive"]["enabled"])
        self.assertFalse(values["topology"]["activeActive"]["useStatefulSet"])
        self.assertEqual(values["replicaCount"], 1)
        self.assertEqual(values["rtpengine"]["replicas"], 1)
        self.assertFalse(values["rtpengine"]["hostNetwork"])

    def test_kubernetes_pcap_capture_roles_follow_expected_traffic(self):
        cases = {
            "basic-media": ("core", "peer"),
            "register-auth-failure": ("peer",),
            "ai-rasa-lab": ("core",),
            "unknown-route": ("core",),
            "ha-options-health-recovery": ("core",),
            "load-5cps-60s": (),
        }

        for profile_name, expected in cases.items():
            with self.subTest(profile=profile_name):
                profile = run_k8s_regression.profile_values(profile_name, "unit-k8s")
                self.assertEqual(run_k8s_regression.k8s_pcap_capture_roles(profile), expected)

    def test_kubernetes_pcap_capture_filter_keeps_evidence_focused(self):
        profile = run_k8s_regression.profile_values("rtpengine-transcoding", "unit-k8s")
        load_profile = run_k8s_regression.profile_values(
            "load-5cps-60s-rtpengine-transcoding",
            "unit-k8s",
        )

        capture_filter = run_k8s_regression.k8s_pcap_capture_filter(profile)
        load_capture_filter = run_k8s_regression.k8s_pcap_capture_filter(load_profile)

        self.assertIn("portrange 5060-5079", capture_filter)
        self.assertIn("portrange 30000-32000", capture_filter)
        self.assertNotEqual(capture_filter, "udp or tcp")
        self.assertNotIn("port 53", capture_filter)
        self.assertIn("portrange 30000-32999", load_capture_filter)

    def test_kubernetes_options_capture_closes_after_request_response_pair(self):
        options = run_k8s_regression.profile_values("ha-options-health-recovery", "unit-k8s")
        hold = run_k8s_regression.profile_values("rfc5359-call-hold-resume", "unit-k8s")

        self.assertEqual(run_k8s_regression.k8s_pcap_packet_limit(options), 2)
        self.assertIsNone(run_k8s_regression.k8s_pcap_packet_limit(hold))

        with tempfile.TemporaryDirectory() as tmp:
            process = mock.Mock()
            process.poll.return_value = None
            args = run_k8s_regression.parse_args(["--profile", "ha-options-health-recovery"])
            runner = run_k8s_regression.K8sRegressionRunner(args, "unit-k8s")
            with (
                mock.patch.object(run_k8s_regression.subprocess, "Popen", return_value=process) as popen,
                mock.patch.object(run_k8s_regression.time, "sleep"),
            ):
                captures = runner.start_packet_captures(options, Path(tmp), [("core", "core-pod")])

            shell_command = popen.call_args.args[0][-1]
            self.assertIn(" --immediate-mode ", shell_command)
            self.assertIn(" -c 2 -w ", shell_command)
            self.assertIn("packet_limit=2", (Path(tmp) / "log.networking").read_text(encoding="utf-8"))
            runner.close_process_files(captures[0].process)

    def test_kubernetes_merged_pcap_is_sorted_by_packet_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            later = root / "capture-core.pcap"
            earlier = root / "capture-peer.pcap"
            destination = root / "capture.pcap"
            write_test_pcap(later, 20.0, b"later", linktype=1)
            write_test_pcap(earlier, 10.0, b"earlier", linktype=1)

            merged_bytes = run_k8s_regression.merge_pcap_files([later, earlier], destination)
            _header, linktype, records = run_b2bua_sipp_smoke.pcap_file_records(destination)

        self.assertGreater(merged_bytes, 24)
        self.assertEqual(linktype, 1)
        self.assertEqual([frame for _timestamp, frame in records], [b"earlier", b"later"])

    def test_kubernetes_packet_capture_waits_for_tcpdump_flush_before_fallback_termination(self):
        process = mock.Mock()
        process.poll.return_value = None
        process.wait.return_value = 0
        process._playsbc_stdout = io.StringIO()
        process._playsbc_stderr = io.StringIO()
        capture = run_k8s_regression.CaptureProcess(
            "core",
            "core-pod",
            "/tmp/core.pcap",
            Path("capture-core.pcap"),
            process,
        )
        args = run_k8s_regression.parse_args(["--profile", "ha-options-health-recovery"])
        runner = run_k8s_regression.K8sRegressionRunner(args, "unit-k8s")

        with mock.patch.object(runner, "kubectl") as kubectl:
            runner.stop_packet_captures([capture])

        kubectl.assert_called_once_with(
            "exec",
            "core-pod",
            "--",
            "sh",
            "-lc",
            "pkill -INT tcpdump || true",
            check=False,
        )
        process.wait.assert_called_once_with(timeout=5)
        process.terminate.assert_not_called()
        process.kill.assert_not_called()

    def test_kubernetes_collect_packet_captures_keeps_only_combined_pcap(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            for role in ("core", "peer"):
                (bundle / f"k8s-pcap-{role}").mkdir()

            captures = [
                run_k8s_regression.CaptureProcess(
                    "core",
                    "core-pod",
                    "/tmp/core.pcap",
                    bundle / "capture-core.pcap",
                    mock.Mock(),
                ),
                run_k8s_regression.CaptureProcess(
                    "peer",
                    "peer-pod",
                    "/tmp/peer.pcap",
                    bundle / "capture-peer.pcap",
                    mock.Mock(),
                ),
            ]
            args = run_k8s_regression.parse_args(["--profile", "basic-media"])
            runner = run_k8s_regression.K8sRegressionRunner(args, "unit-k8s")

            def fake_copy(command, **_kwargs):
                destination = Path(command[5])
                timestamp = 20.0 if destination.name == "capture-core.pcap" else 10.0
                if destination.name == "capture-core.pcap":
                    packet = run_b2bua_sipp_smoke.PcapPacket(
                        timestamp,
                        "10.10.10.10",
                        5060,
                        "10.10.10.20",
                        5062,
                        b"INVITE sip:1002@lab SIP/2.0\r\nCall-ID: core-leg\r\nCSeq: 1 INVITE\r\nContent-Length: 0\r\n\r\n",
                    )
                else:
                    packet = run_b2bua_sipp_smoke.PcapPacket(
                        timestamp,
                        "10.10.10.20",
                        5062,
                        "10.10.10.30",
                        5060,
                        b"INVITE sip:1002@peer SIP/2.0\r\nCall-ID: peer-leg\r\nCSeq: 1 INVITE\r\nContent-Length: 0\r\n\r\n",
                    )
                run_b2bua_sipp_smoke.write_udp_pcap(destination, [packet])
                return run_k8s_regression.CommandResult(command, 0, 0.1, "", "")

            with mock.patch.object(run_k8s_regression, "run_command", side_effect=fake_copy):
                profile = run_k8s_regression.profile_values("basic-media", "unit-k8s")
                self.assertTrue(runner.collect_packet_captures(captures, bundle, profile))

            self.assertTrue((bundle / "capture.pcap").exists())
            self.assertFalse((bundle / "capture-core.pcap").exists())
            self.assertFalse((bundle / "capture-peer.pcap").exists())
            networking_log = (bundle / "log.networking").read_text(encoding="utf-8")
            self.assertIn("retained_file=capture.pcap", networking_log)
            self.assertIn("discarded_role_pcaps=capture-core.pcap,capture-peer.pcap", networking_log)
            self.assertIn("merged_roles=core,peer", networking_log)
            self.assertIn("invite_legs=2", networking_log)
            leg_summary = json.loads((bundle / "pcap-legs.json").read_text(encoding="utf-8"))
            self.assertEqual(leg_summary["expected_roles"], ["core", "peer"])
            self.assertEqual(leg_summary["invite_call_ids"], ["core-leg", "peer-leg"])
            self.assertEqual(leg_summary["status"], "passed")

    def test_kubernetes_packet_capture_rejects_empty_expected_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            for role in ("core", "peer"):
                (bundle / f"k8s-pcap-{role}").mkdir()
            captures = [
                run_k8s_regression.CaptureProcess(
                    role,
                    f"{role}-pod",
                    f"/tmp/{role}.pcap",
                    bundle / f"capture-{role}.pcap",
                    mock.Mock(),
                )
                for role in ("core", "peer")
            ]
            args = run_k8s_regression.parse_args(["--profile", "basic-media"])
            runner = run_k8s_regression.K8sRegressionRunner(args, "unit-k8s")

            def fake_copy(command, **_kwargs):
                destination = Path(command[5])
                if destination.name == "capture-core.pcap":
                    run_b2bua_sipp_smoke.write_udp_pcap(
                        destination,
                        [
                            run_b2bua_sipp_smoke.PcapPacket(
                                1.0,
                                "10.10.10.10",
                                5060,
                                "10.10.10.20",
                                5062,
                                b"INVITE sip:1002@lab SIP/2.0\r\nCall-ID: core-only\r\nCSeq: 1 INVITE\r\nContent-Length: 0\r\n\r\n",
                            )
                        ],
                    )
                    return run_k8s_regression.CommandResult(command, 0, 0.1, "", "")
                return run_k8s_regression.CommandResult(command, 1, 0.1, "", "copy failed")

            profile = run_k8s_regression.profile_values("basic-media", "unit-k8s")
            with mock.patch.object(run_k8s_regression, "run_command", side_effect=fake_copy):
                self.assertFalse(runner.collect_packet_captures(captures, bundle, profile))

            summary = json.loads((bundle / "pcap-legs.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["role_packet_counts"], {"core": 1, "peer": 0})
            self.assertEqual(summary["status"], "failed")

    def test_kubernetes_combined_sipmsg_log_is_written_from_sipp_traces(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            core = bundle / "core-sipp-a-uac"
            peer = bundle / "peer-sipp-b-uas"
            core.mkdir()
            peer.mkdir()
            (core / "sipp-traces.log").write_text(
                "===== /tmp/uac_messages.log =====\nINVITE sip:1002@example.test SIP/2.0\n"
                "===== /tmp/uac_errors.log =====\nignored error trace\n",
                encoding="utf-8",
            )
            (peer / "sipp-traces.log").write_text(
                "===== /tmp/uas_messages.log =====\nSIP/2.0 200 OK\n",
                encoding="utf-8",
            )

            section_count = run_k8s_regression.write_combined_sipmsg_log(bundle, "basic-media")

            sipmsg = (bundle / "sipmsg.log").read_text(encoding="utf-8")
            self.assertEqual(section_count, 2)
            self.assertIn("core-sipp-a-uac / uac_messages.log", sipmsg)
            self.assertIn("peer-sipp-b-uas / uas_messages.log", sipmsg)
            self.assertIn("INVITE sip:1002@example.test SIP/2.0", sipmsg)
            self.assertIn("SIP/2.0 200 OK", sipmsg)
            self.assertNotIn("ignored error trace", sipmsg)

    def test_kubernetes_options_catalog_ladder_is_options_only(self):
        args = run_k8s_regression.parse_args(["--aks-profiles"])
        runner = run_k8s_regression.K8sRegressionRunner(args, "unit-k8s")
        profile = run_k8s_regression.profile_values("esbc-options-keepalive", "unit-k8s")

        ladder = runner.dual_realm_ladder(profile)

        self.assertIn("OPTIONS", ladder)
        self.assertIn("200 OK", ladder)
        self.assertNotIn("INVITE", ladder)
        self.assertNotIn("BYE", ladder)

    def test_kubernetes_invalid_bye_ladder_matches_out_of_dialog_exchange(self):
        args = run_k8s_regression.parse_args(["--aks-profiles"])
        runner = run_k8s_regression.K8sRegressionRunner(args, "unit-k8s")
        profile = run_k8s_regression.profile_values("invalid-bye", "unit-k8s")

        ladder = runner.dual_realm_ladder(profile)

        self.assertIn("BYE (unknown dialog)", ladder)
        self.assertIn("481 No Matching Dialog", ladder)
        self.assertNotIn("INVITE", ladder)
        self.assertNotIn("Peer SIPp B", ladder)

    def test_kubernetes_invalid_bye_evidence_requires_exact_exchange(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            write_test_pcap(bundle / "capture.pcap", 1.0, b"packet", linktype=1)
            (bundle / "sipmsg.log").write_text(
                "BYE sip:missing@example.test SIP/2.0\n"
                "CSeq: 2 BYE\n"
                "SIP/2.0 481 Call/Transaction Does Not Exist\n",
                encoding="utf-8",
            )
            self.assertEqual(
                run_k8s_regression.validate_k8s_profile_evidence("invalid-bye", bundle),
                [],
            )

            (bundle / "sipmsg.log").write_text(
                "INVITE sip:callee@example.test SIP/2.0\nCSeq: 1 INVITE\n",
                encoding="utf-8",
            )
            failures = run_k8s_regression.validate_k8s_profile_evidence(
                "invalid-bye", bundle
            )
            self.assertTrue(any("missing the out-of-dialog BYE" in item for item in failures))
            self.assertTrue(any("unexpectedly contains an INVITE" in item for item in failures))

    def test_kubernetes_rejection_ladders_use_profile_specific_statuses(self):
        args = run_k8s_regression.parse_args(["--aks-profiles"])
        runner = run_k8s_regression.K8sRegressionRunner(args, "unit-k8s")
        expected = {
            "unknown-route": "404 Not Found",
            "esbc-call-admission": "503 Service Unavailable",
            "rtpengine-control-failure": "488 Not Acceptable Here",
            "rtpengine-port-exhaustion": "503 Media Exhausted",
            "rtpengine-interface-failure": "488 Not Acceptable Here",
            "tcp-connection-failure": "480 Temporary Unavailable",
        }
        for profile_name, response in expected.items():
            with self.subTest(profile=profile_name):
                profile = run_k8s_regression.profile_values(profile_name, "unit-k8s")
                ladder = runner.dual_realm_ladder(profile)
                self.assertIn(response, ladder)
                self.assertNotIn("final rejection", ladder)
                self.assertNotIn("BYE", ladder)

    def test_kubernetes_rfc5359_ladders_retain_cleanup_and_endpoint_roles(self):
        args = run_k8s_regression.parse_args(["--aks-profiles"])
        runner = run_k8s_regression.K8sRegressionRunner(args, "unit-k8s")

        unconditional = runner.dual_realm_ladder(
            run_k8s_regression.profile_values("rfc5359-unconditional-forwarding", "unit-k8s")
        )
        self.assertIn("Peer SIPp B", unconditional)
        self.assertNotIn("Target SIPp C", unconditional)
        self.assertIn("ACK", unconditional)
        self.assertIn("BYE", unconditional)

        no_answer = runner.dual_realm_ladder(
            run_k8s_regression.profile_values("rfc5359-forwarding-on-no-answer", "unit-k8s")
        )
        for token in ("Peer SIPp B", "Target SIPp C", "CANCEL", "487 Request Terminated", "ACK", "BYE"):
            self.assertIn(token, no_answer)

        transfer = runner.dual_realm_ladder(
            run_k8s_regression.profile_values("rfc5359-unattended-transfer", "unit-k8s")
        )
        for token in ("REFER", "NOTIFY", "Target SIPp C", "ACK", "BYE"):
            self.assertIn(token, transfer)

    def test_report_catalog_retains_ai_and_transport_evidence(self):
        artifacts = {filename for _label, filename in run_regression_suite.REPORT_ARTIFACTS}
        self.assertTrue({"log.ai", "log.udp", "log.tcp", "log.tls"}.issubset(artifacts))
        execution = set(run_regression_suite.PHASE_ARTIFACTS["Test Execution"])
        self.assertTrue({"log.ai", "log.udp", "log.tcp", "log.tls"}.issubset(execution))

    def test_ladder_validation_rejects_invented_and_omitted_sip_events(self):
        correct = "Core | BYE | PlaySBC\nCore | 481 No Matching Dialog | PlaySBC"
        sipmsg = (
            "BYE sip:missing@example.test SIP/2.0\nCSeq: 2 BYE\n"
            "SIP/2.0 481 Call/Transaction Does Not Exist\nCSeq: 2 BYE\n"
        )
        self.assertEqual(
            run_k8s_regression.validate_ladder_against_sipmsg(correct, sipmsg),
            [],
        )
        failures = run_k8s_regression.validate_ladder_against_sipmsg(
            "Core | INVITE | PlaySBC\nPlaySBC | 200 OK | Core",
            sipmsg,
        )
        self.assertTrue(any("invented=['INVITE']" in item for item in failures))
        self.assertTrue(any("omitted=['BYE']" in item for item in failures))
        self.assertTrue(any("omitted=['481']" in item for item in failures))

    def test_kubernetes_evidence_validation_requires_srtp_two_way_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            write_test_pcap(bundle / "capture.pcap", 1.0, b"packet", linktype=1)
            (bundle / "sipmsg.log").write_text("INVITE sip:1002@example.test SIP/2.0\n", encoding="utf-8")
            (bundle / "log.media").write_text(
                (
                    "RTPENGINE MEDIA SECURITY\n"
                    "offer_transport=RTP/AVP answer_transport=RTP/SAVP\n"
                    "RTPENGINE PACKET VERDICT\n"
                    "caller_to_callee=observed callee_to_caller=observed total_rtp_packets=20\n"
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                run_k8s_regression.validate_k8s_profile_evidence("tls-srtp-to-udp-rtp", bundle),
                [],
            )

            (bundle / "log.media").write_text(
                (
                    "RTPENGINE MEDIA SECURITY\n"
                    "crypto negotiation failed\n"
                    "RTPENGINE PACKET VERDICT\n"
                    "caller_to_callee=observed callee_to_caller=not_observed total_rtp_packets=7\n"
                ),
                encoding="utf-8",
            )

            failures = run_k8s_regression.validate_k8s_profile_evidence("tls-srtp-to-udp-rtp", bundle)

            self.assertTrue(any("crypto" in failure for failure in failures))
            self.assertTrue(any("both RTP directions" in failure for failure in failures))

    def test_kubernetes_srtp_validation_ignores_prior_call_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            write_test_pcap(bundle / "capture.pcap", 1.0, b"packet", linktype=1)
            (bundle / "sipmsg.log").write_text(
                "INVITE sip:1002@example.test SIP/2.0\nCall-ID: current-call\nCSeq: 1 INVITE\n",
                encoding="utf-8",
            )
            (bundle / "log.media").write_text(
                (
                    "RTPENGINE MEDIA SECURITY | call_id=current-call\n"
                    "RTPENGINE PACKET VERDICT | call_id=current-call | "
                    "caller_to_callee=observed callee_to_caller=observed total_rtp_packets=20\n"
                ),
                encoding="utf-8",
            )
            (bundle / "rtpengine.log").write_text(
                "ERR: [prior-call port 30000]: SRTP output wanted, but no crypto suite was negotiated\n",
                encoding="utf-8",
            )

            self.assertEqual(
                run_k8s_regression.validate_k8s_profile_evidence("udp-rtp-to-tls-srtp", bundle),
                [],
            )

    def test_kubernetes_options_validation_ignores_allow_header_methods(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            write_test_pcap(bundle / "capture.pcap", 1.0, b"options", linktype=1)
            (bundle / "sipmsg.log").write_text(
                (
                    "OPTIONS sip:playsbc@example.test SIP/2.0\n"
                    "Call-ID: options-call\n"
                    "CSeq: 1 OPTIONS\n\n"
                    "SIP/2.0 200 OK\n"
                    "Call-ID: options-call\n"
                    "CSeq: 1 OPTIONS\n"
                    "Allow: REGISTER, OPTIONS, INVITE, ACK, BYE, CANCEL\n"
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                run_k8s_regression.validate_k8s_profile_evidence("esbc-options-keepalive", bundle),
                [],
            )

    def test_rfc5359_profiles_and_evidence_cover_hold_resume_transports(self):
        expected = {
            "rfc5359-call-hold-resume",
            "rfc5359-call-hold-resume-rtpengine",
            "rfc5359-call-hold-resume-tcp",
            "rfc5359-call-hold-resume-tls",
        }
        self.assertTrue(expected.issubset(run_regression_suite.ALL_B2BUA_PROFILES))
        self.assertIn("rfc5359-call-hold-resume-rtpengine", run_regression_suite.RTPENGINE_B2BUA_PROFILES)
        for profile_name in expected:
            profile = run_k8s_regression.profile_values(profile_name, "unit-hold")
            self.assertTrue(profile.media_enabled)
            self.assertEqual(profile.media_pcap, "pcap/g711u_hold_burst.pcap")
            self.assertEqual(profile.uas_media_pcap, "pcap/g711u_hold_burst.pcap")
            self.assertIn("play_pcap_audio", run_k8s_regression.rendered_scenario(profile, "uac"))
            self.assertIn("play_pcap_audio", run_k8s_regression.rendered_scenario(profile, "uas"))

        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            write_test_pcap(bundle / "capture.pcap", 1.0, b"hold", linktype=1)
            (bundle / "sipmsg.log").write_text(
                (
                    "INVITE sip:hold-b@example.test SIP/2.0\nCSeq: 1 INVITE\n"
                    "INVITE sip:hold-b@example.test SIP/2.0\nCSeq: 2 INVITE\na=sendonly\n"
                    "ACK sip:hold-b@example.test SIP/2.0\nCSeq: 2 ACK\n"
                    "INVITE sip:hold-b@example.test SIP/2.0\nCSeq: 3 INVITE\na=sendrecv\n"
                    "ACK sip:hold-b@example.test SIP/2.0\nCSeq: 3 ACK\n"
                ),
                encoding="utf-8",
            )

            rtp_packets = []
            for burst_start in (1.0, 2.5):
                flows = (
                    ("10.0.0.1", 6000, "10.0.0.3", 25100),
                    ("10.0.0.3", 25102, "10.0.0.2", 6000),
                    ("10.0.0.2", 6000, "10.0.0.1", 6000),
                )
                for src_ip, src_port, dst_ip, dst_port in flows:
                    for index in range(25):
                        rtp_packets.append(
                            SimpleNamespace(
                                timestamp=burst_start + (index * 0.02),
                                transport="udp",
                                src_ip=src_ip,
                                dst_ip=dst_ip,
                                src_port=src_port,
                                dst_port=dst_port,
                                payload=b"\x80\x00" + (b"\x00" * 170),
                            )
                        )
            with mock.patch.object(run_k8s_regression, "read_pcap", return_value=rtp_packets):
                self.assertEqual(
                    run_k8s_regression.validate_k8s_profile_evidence("rfc5359-call-hold-resume", bundle),
                    [],
                )

            with mock.patch.object(run_k8s_regression, "read_pcap", return_value=[]):
                failures = run_k8s_regression.validate_k8s_profile_evidence(
                    "rfc5359-call-hold-resume",
                    bundle,
                )
            self.assertTrue(any("bidirectional RTP before hold" in failure for failure in failures))
            self.assertTrue(any("held-media gap" in failure for failure in failures))

    def test_kubernetes_mock_rasa_evidence_rejects_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            write_test_pcap(bundle / "capture.pcap", 1.0, b"rasa", linktype=1)
            (bundle / "sipmsg.log").write_text(
                "INVITE sip:rasa@example.test SIP/2.0\nCSeq: 1 INVITE\n",
                encoding="utf-8",
            )
            (bundle / "log.ai").write_text(
                "RASA REST RESPONSE fallback_used=true\nRASA REST ERROR connection refused\n",
                encoding="utf-8",
            )

            failures = run_k8s_regression.validate_k8s_profile_evidence("ai-rasa-lab", bundle)

            self.assertTrue(any("fallback" in failure for failure in failures))

            (bundle / "log.ai").write_text(
                "RASA REST RESPONSE fallback_used=false\n",
                encoding="utf-8",
            )
            self.assertEqual(
                run_k8s_regression.validate_k8s_profile_evidence("ai-rasa-lab", bundle),
                [],
            )

    def test_kubernetes_evidence_validation_rejects_options_noise_and_split_pcaps(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            write_test_pcap(bundle / "capture.pcap", 1.0, b"options", linktype=1)
            write_test_pcap(bundle / "capture-core.pcap", 2.0, b"stale", linktype=1)
            (bundle / "sipmsg.log").write_text(
                (
                    "OPTIONS sip:playsbc@example.test SIP/2.0\nCSeq: 1 OPTIONS\n"
                    "INVITE sip:1002@example.test SIP/2.0\nCSeq: 2 INVITE\n"
                ),
                encoding="utf-8",
            )

            failures = run_k8s_regression.validate_k8s_profile_evidence("esbc-options-keepalive", bundle)

            self.assertTrue(any("non-OPTIONS" in failure for failure in failures))
            self.assertTrue(any("stale split capture" in failure for failure in failures))

    def test_real_device_capture_filter_is_sip_and_media_only(self):
        args = run_real_device_capture.parse_args(["--duration", "1"])

        capture_filter = run_real_device_capture.capture_filter(args)

        self.assertIn("portrange 5060-5079", capture_filter)
        self.assertIn("port 2223", capture_filter)
        self.assertIn("portrange 30000-30049", capture_filter)
        self.assertIn("portrange 30000-32767", capture_filter)
        self.assertNotIn("port 53", capture_filter)

    def test_real_device_capture_pins_explicit_kube_context(self):
        args = run_real_device_capture.parse_args(
            ["--context", "kind-playsbc-real-device", "--duration", "1"]
        )

        command = run_real_device_capture.kubectl_command(args, "-n", "playsbc", "get", "pods")

        self.assertEqual(
            command,
            [
                "kubectl",
                "--context",
                "kind-playsbc-real-device",
                "-n",
                "playsbc",
                "get",
                "pods",
            ],
        )

    def test_kind_real_device_profile_is_isolated_and_maps_all_ports(self):
        cluster = (ROOT / "configs" / "kubernetes" / "kind-real-device-cluster.yaml").read_text(
            encoding="utf-8"
        )
        values = (ROOT / "configs" / "kubernetes" / "kind-real-device-values.yaml").read_text(
            encoding="utf-8"
        )
        deployment = (ROOT / "charts" / "playsbc" / "templates" / "deployment.yaml").read_text(
            encoding="utf-8"
        )
        validation = (ROOT / "charts" / "playsbc" / "templates" / "validation.yaml").read_text(
            encoding="utf-8"
        )

        self.assertEqual(cluster.count("hostPort:"), 53)
        self.assertEqual(len(check_kind_real_device_lab.required_bindings(30000, 30049)), 53)
        self.assertIn("localRealDevice:\n  enabled: true", values)
        self.assertIn("activeActive:\n    enabled: false", values)
        self.assertIn("hostNetwork: true", values)
        self.assertNotIn("provider: azure", values)
        self.assertIn('$localRealDeviceEnabled := get $localRealDevice "enabled"', deployment)
        self.assertIn("else if $localRealDeviceEnabled", deployment)
        self.assertIn("type: Recreate", deployment)
        self.assertIn("cannot enable Azure cloud exposure", validation)
        self.assertIn("requires exactly one PlaySBC and one RTPengine replica", validation)

    def test_kind_real_device_binding_parser_preserves_protocol(self):
        published = check_kind_real_device_lab.published_bindings(
            {
                "5062/tcp": [{"HostIp": "0.0.0.0", "HostPort": "5062"}],
                "5062/udp": [{"HostIp": "0.0.0.0", "HostPort": "5062"}],
                "30000/udp": [{"HostIp": "0.0.0.0", "HostPort": "30000"}],
            }
        )

        self.assertEqual(published, {(5062, "tcp"), (5062, "udp"), (30000, "udp")})

    def test_real_device_capture_uses_host_network_tcpdump_pod(self):
        args = run_real_device_capture.parse_args(
            ["--duration", "1", "--capture-image", "example.test/netshoot:lab"]
        )

        manifest = run_real_device_capture.capture_pod_manifest(args, "real-device-capture-unit-pod")
        container = manifest["spec"]["containers"][0]

        self.assertTrue(manifest["spec"]["hostNetwork"])
        self.assertEqual(container["name"], "capture")
        self.assertEqual(container["image"], "example.test/netshoot:lab")
        self.assertTrue(container["securityContext"]["privileged"])
        self.assertIn("NET_RAW", container["securityContext"]["capabilities"]["add"])

    def test_real_device_capture_copy_uses_binary_fallback(self):
        args = run_real_device_capture.parse_args(["--duration", "1"])
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            capture = run_real_device_capture.Capture(
                pod="capture-pod",
                container="capture",
                remote_path="/tmp/capture.pcap",
                local_path=bundle / "capture.pcap",
                process=mock.Mock(),
            )
            failed_cp = subprocess.CompletedProcess(["kubectl", "cp"], 1, "", "tar failed")
            fallback_cp = subprocess.CompletedProcess(
                ["kubectl", "exec"],
                0,
                write_test_pcap_bytes(1.0, b"\x00\xffbinary"),
                b"",
            )

            with (
                mock.patch.object(run_real_device_capture, "run_command", return_value=failed_cp),
                mock.patch.object(run_real_device_capture, "run_binary_command", return_value=fallback_cp),
            ):
                copied = run_real_device_capture.copy_capture(args, capture, bundle)

            self.assertTrue(copied)
            self.assertTrue((bundle / "capture.pcap").exists())
            self.assertIn(b"\x00\xffbinary", (bundle / "capture.pcap").read_bytes())

    def test_real_device_capture_creates_tgz_archive(self):
        args = run_real_device_capture.parse_args(["--duration", "1"])
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "real-device-capture-unit"
            bundle.mkdir()
            (bundle / "capture.pcap").write_bytes(write_test_pcap_bytes(1.0, b"rtp"))
            (bundle / "sipmsg.log").write_text("INVITE sip:1001@example.test SIP/2.0\n", encoding="utf-8")

            archive_path = run_real_device_capture.create_evidence_archive(args, bundle)

            self.assertEqual(archive_path, bundle.with_suffix(".tgz"))
            self.assertTrue(archive_path.exists())
            with tarfile.open(archive_path, "r:gz") as archive:
                names = set(archive.getnames())
            self.assertIn("real-device-capture-unit/capture.pcap", names)
            self.assertIn("real-device-capture-unit/sipmsg.log", names)

    def test_real_device_evidence_collapses_sip_retransmissions_and_classifies_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            call_id = "real-device-unit@example.test"

            def sip(start_line: str, cseq: str) -> bytes:
                return (
                    f"{start_line}\r\nCall-ID: {call_id}\r\nCSeq: {cseq}\r\nContent-Length: 0\r\n\r\n"
                ).encode("ISO-8859-1")

            def rtp(payload_type: int, media_bytes: int, sequence: int) -> bytes:
                return struct.pack("!BBHII", 0x80, payload_type, sequence, 160 * sequence, 0x12345678) + bytes(media_bytes)

            def rtcp(packet_type: int) -> bytes:
                return struct.pack("!BBH", 0x80, packet_type, 1) + b"\x00\x00\x00\x01"

            packets = [
                run_b2bua_sipp_smoke.PcapPacket(1.0, "198.51.100.10", 5060, "203.0.113.20", 5062, sip("INVITE sip:1002@example.test SIP/2.0", "1 INVITE")),
                run_b2bua_sipp_smoke.PcapPacket(1.001, "198.51.100.10", 5060, "10.244.0.20", 5062, sip("INVITE sip:1002@example.test SIP/2.0", "1 INVITE")),
                run_b2bua_sipp_smoke.PcapPacket(1.1, "203.0.113.20", 5062, "198.51.100.10", 5060, sip("SIP/2.0 100 Trying", "1 INVITE")),
                run_b2bua_sipp_smoke.PcapPacket(1.2, "203.0.113.20", 5062, "198.51.100.10", 5060, sip("SIP/2.0 180 Ringing", "1 INVITE")),
                run_b2bua_sipp_smoke.PcapPacket(1.3, "203.0.113.20", 5062, "198.51.100.10", 5060, sip("SIP/2.0 200 OK", "1 INVITE")),
                run_b2bua_sipp_smoke.PcapPacket(1.4, "198.51.100.10", 5060, "203.0.113.20", 5062, sip("ACK sip:1002@example.test SIP/2.0", "1 ACK")),
                run_b2bua_sipp_smoke.PcapPacket(1.5, "203.0.113.20", 5062, "198.51.100.10", 5060, sip("SIP/2.0 200 OK", "1 INVITE")),
                run_b2bua_sipp_smoke.PcapPacket(1.25, "198.51.100.10", 30000, "203.0.113.20", 30002, rtp(0, 1, 1)),
                run_b2bua_sipp_smoke.PcapPacket(1.6, "198.51.100.10", 30000, "203.0.113.20", 30002, rtp(0, 160, 2)),
                run_b2bua_sipp_smoke.PcapPacket(1.7, "203.0.113.20", 30002, "198.51.100.10", 30000, rtp(8, 160, 3)),
                run_b2bua_sipp_smoke.PcapPacket(1.8, "198.51.100.10", 30001, "203.0.113.20", 30003, rtcp(201)),
            ]
            run_b2bua_sipp_smoke.write_udp_pcap(bundle / "capture.pcap", packets)
            (bundle / "rtpengine-verdict.log").write_text(
                "caller_to_callee=observed callee_to_caller=observed\n", encoding="utf-8"
            )

            evidence = real_device_evidence.write_evidence_bundle(bundle)

            self.assertEqual(evidence["sip"]["canonical_events"], 5)
            self.assertEqual(evidence["sip"]["capture_mirror_packets"], 1)
            self.assertEqual(evidence["sip"]["retransmitted_packets"], 1)
            self.assertEqual(
                evidence["sip"]["retransmissions"][0]["classification"],
                "expected_200_ok_retransmission_after_ack",
            )
            self.assertEqual(evidence["media"]["packet_counts"]["voice_rtp"], 2)
            self.assertEqual(evidence["media"]["packet_counts"]["nat_probe_rtp"], 1)
            self.assertEqual(evidence["media"]["rtcp_status"], "endpoint-limited")
            self.assertTrue(evidence["media"]["bidirectional_rtp_proven"])
            self.assertIn("CANONICAL SIP FLOW", (bundle / "sipmsg.log").read_text(encoding="utf-8"))
            self.assertFalse((bundle / "canonical-sip.log").exists())
            self.assertTrue((bundle / "media-evidence.json").exists())
            self.assertTrue((bundle / "latest.html").exists())

    def test_real_device_log_collection_uses_exact_capture_window(self):
        args = run_real_device_capture.parse_args(
            ["--duration", "120", "--context", "kind-playsbc-real-device"]
        )
        started = run_real_device_capture.dt.datetime(2026, 8, 23, 10, 30, tzinfo=run_real_device_capture.dt.timezone.utc)
        completed = subprocess.CompletedProcess(["kubectl"], 0, "", "")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            run_real_device_capture, "run_command", return_value=completed
        ) as command:
            run_real_device_capture.collect_logs(args, Path(tmp), started)

        flattened = [part for call in command.call_args_list for part in call.args[0]]
        self.assertIn("--since-time=2026-08-23T10:30:00Z", flattened)
        self.assertIn("kind-playsbc-real-device", flattened)
        self.assertNotIn("--since=210s", flattened)

    def test_tcp_tls_registration_profiles_are_registration_only_and_selectable(self):
        for name, transport in (("register-auth-tcp", "tcp"), ("register-auth-tls", "tls")):
            profile = run_b2bua_sipp_smoke.B2BUA_PROFILES[name]
            self.assertEqual(profile["sip_transport"], transport)
            self.assertFalse(profile["run_call"])
            self.assertFalse(profile["start_uas"])
            self.assertEqual(profile["registration_auth_expected"], "success")
            self.assertIn(name, run_regression_suite.ALL_B2BUA_PROFILES)
            self.assertIn(name, run_k8s_regression.AKS_PROFILES)

    def test_kubernetes_real_rasa_profile_is_selectable_and_rewrites_webhook(self):
        self.assertIn("ai-rasa-real-lab", run_k8s_regression.SELECTABLE_PROFILES)
        self.assertIn("ai-rasa-real-lab", run_k8s_regression.ALL_PROFILES)

        args = run_k8s_regression.parse_args(["--profile", "ai-rasa-real-lab"])
        runner = run_k8s_regression.K8sRegressionRunner(args, "unit-k8s")
        profile = run_k8s_regression.profile_values("ai-rasa-real-lab", "unit-k8s")
        profile.ai_voice_gateway = {
            **profile.ai_voice_gateway,
            "rasa_webhook_url": f"http://{args.service}-rasa:5005/webhooks/rest/webhook",
        }

        self.assertTrue(run_k8s_regression.profile_uses_real_rasa(profile))
        self.assertEqual(
            runner.profile_config(profile)["ai_voice_gateway"]["rasa_webhook_url"],
            "http://playsbc-playsbc-rasa:5005/webhooks/rest/webhook",
        )

    def test_kubernetes_speech_profile_uses_speech_pcap_and_real_engine_boundaries(self):
        profile = run_k8s_regression.profile_values("ai-rasa-rtpengine-speech", "unit-rasa")

        self.assertEqual(run_k8s_regression.media_pcap_path(profile, "uac"), "/scenarios/pcap/ai_rasa_speech_g711u.pcap")
        self.assertTrue(run_k8s_regression.profile_uses_real_rasa(profile))
        self.assertEqual(profile.ai_voice_gateway["stt_provider"], "vosk")
        self.assertIn("vosk_stt_wrapper.py", profile.ai_voice_gateway["stt_command"])
        self.assertEqual(profile.ai_voice_gateway["tts_provider"], "piper")
        self.assertIn("piper_tts_wrapper.py", profile.ai_voice_gateway["tts_command"])

    def test_kubernetes_contact_center_sales_profile_uses_bot_agent_b_side(self):
        profile = run_k8s_regression.profile_values("ai-rasa-contact-center-sales", "unit-rasa")

        self.assertEqual(run_k8s_regression.media_pcap_path(profile, "uac"), "/scenarios/pcap/ai_contact_center_sales_g711u.pcap")
        self.assertTrue(run_k8s_regression.profile_uses_real_rasa(profile))
        self.assertFalse(profile.start_uas)
        self.assertEqual(profile.ai_voice_gateway["agent_label"], "SIPp B Bot Agent")
        self.assertEqual(profile.ai_voice_gateway["contact_center_skill"], "sales-support")
        self.assertEqual(profile.ai_voice_gateway["stt_provider"], "vosk")
        self.assertEqual(profile.ai_voice_gateway["tts_provider"], "piper")

    def test_kubernetes_whisper_profile_uses_whisper_stt_adapter(self):
        profile = run_k8s_regression.profile_values("ai-rasa-rtpengine-speech-whisper", "unit-rasa")

        self.assertEqual(run_k8s_regression.media_pcap_path(profile, "uac"), "/scenarios/pcap/ai_rasa_speech_g711u.pcap")
        self.assertTrue(run_k8s_regression.profile_uses_real_rasa(profile))
        self.assertEqual(profile.media_backend, "rtpengine")
        self.assertEqual(profile.ai_voice_gateway["stt_provider"], "whisper")
        self.assertIn("whisper_stt_wrapper.py", profile.ai_voice_gateway["stt_command"])
        self.assertEqual(profile.ai_voice_gateway["tts_provider"], "piper")

    def test_kubernetes_streaming_profile_uses_mock_multi_reply_and_chunked_tts(self):
        profile = run_k8s_regression.profile_values("ai-rasa-long-response-streaming", "unit-rasa")

        self.assertEqual(run_k8s_regression.media_pcap_path(profile, "uac"), "/scenarios/pcap/ai_rasa_speech_g711u.pcap")
        self.assertTrue(run_k8s_regression.profile_uses_real_rasa(profile))
        self.assertEqual(profile.media_backend, "rtpengine")
        self.assertEqual(profile.ai_voice_gateway["response_mode"], "streaming")
        self.assertEqual(profile.ai_voice_gateway["tts_chunk_chars"], 120)
        self.assertEqual(profile.ai_voice_gateway["tts_provider"], "piper")

    def test_kubernetes_contact_center_coqui_profile_uses_coqui_tts_adapter(self):
        profile = run_k8s_regression.profile_values("ai-rasa-contact-center-sales-coqui", "unit-rasa")

        self.assertEqual(run_k8s_regression.media_pcap_path(profile, "uac"), "/scenarios/pcap/ai_contact_center_sales_g711u.pcap")
        self.assertTrue(run_k8s_regression.profile_uses_real_rasa(profile))
        self.assertEqual(profile.media_backend, "rtpengine")
        self.assertEqual(profile.ai_voice_gateway["agent_label"], "SIPp B Bot Agent")
        self.assertEqual(profile.ai_voice_gateway["contact_center_skill"], "sales-support")
        self.assertEqual(profile.ai_voice_gateway["stt_provider"], "vosk")
        self.assertEqual(profile.ai_voice_gateway["tts_provider"], "coqui")
        self.assertIn("coqui_tts_wrapper.py", profile.ai_voice_gateway["tts_command"])

    def test_kubernetes_real_rasa_profiles_inject_current_repo_project(self):
        project = run_k8s_regression.rasa_project_values()

        self.assertIn("Sales support agent is ready", project["domain"])
        self.assertIn("Streaming support update is ready", project["domain"])
        self.assertIn("long_response", project["domain"])
        self.assertIn("I need sales support", project["nlu"])
        self.assertIn("give me a detailed support update", project["nlu"])
        self.assertIn("rest:", project["credentials"])

    def test_kubernetes_rasa_profiles_have_distinct_report_names_and_ladders(self):
        args = run_k8s_regression.parse_args(["--rasa-profiles"])
        runner = run_k8s_regression.K8sRegressionRunner(args, "unit-rasa")

        cases = {
            "ai-rasa-lab": ("AI Voice Gateway - Mock Rasa REST", "Mock Rasa REST", "internal PlaySBC media"),
            "ai-rasa-rtpengine": (
                "AI Voice Gateway - Mock Rasa + RTPengine",
                "Mock Rasa + Action",
                "RTPengine RTP/RTCP anchor",
            ),
            "ai-rasa-real-lab": (
                "AI Voice Gateway - Real Rasa Pod + RTPengine",
                "Real Rasa Pod",
                "real Rasa deployment",
            ),
            "ai-rasa-rtpengine-speech": (
                "AI Voice Gateway - Speech STT/TTS + Real Rasa",
                "Real Rasa Pod",
                "SIPp plays real G.711 speech",
            ),
            "ai-rasa-rtpengine-speech-whisper": (
                "AI Voice Gateway - Whisper STT + Real Rasa",
                "Whisper STT",
                "Whisper transcribes through the adapter boundary",
            ),
            "ai-rasa-long-response-streaming": (
                "AI Voice Gateway - Long Response Streaming",
                "Real Rasa Pod",
                "real Rasa returns a long support response",
            ),
            "ai-rasa-contact-center-sales": (
                "AI Contact Center - SIPp B Sales Bot Agent",
                "SIPp B Bot Agent",
                "virtual SIPp B sales agent",
            ),
            "ai-rasa-contact-center-sales-coqui": (
                "AI Contact Center - Sales Bot Agent + Coqui",
                "Coqui TTS",
                "Coqui generates the bot-agent prompt",
            ),
        }

        for profile_name, (title, node, mode) in cases.items():
            with self.subTest(profile=profile_name):
                profile = run_k8s_regression.profile_values(profile_name, "unit-rasa")
                ladder = runner.dual_realm_ladder(profile)

                self.assertEqual(run_k8s_regression.profile_display_title(profile_name), title)
                self.assertIn(title, run_k8s_regression.profile_execution_label(profile_name))
                self.assertIn(f"case={title}", ladder)
                self.assertIn(mode, ladder)
                self.assertIn(node, ladder)
                if profile_name == "ai-rasa-rtpengine-speech":
                    self.assertIn("Vosk STT", ladder)
                    self.assertIn("Piper TTS", ladder)
                    self.assertIn("decode WAV", ladder)
                    self.assertIn("Piper WAV", ladder)
                    self.assertNotIn("STT Adapter", ladder)
                    self.assertNotIn("TTS Adapter", ladder)
                if profile_name == "ai-rasa-contact-center-sales":
                    self.assertIn("connect me to sales", ladder)
                    self.assertIn("REST 200 sales workflow", ladder)
                    self.assertIn("SIPp B Bot Agent", ladder)
                if profile_name == "ai-rasa-rtpengine-speech-whisper":
                    self.assertIn("Whisper STT", ladder)
                    self.assertIn("Piper WAV", ladder)
                if profile_name == "ai-rasa-long-response-streaming":
                    self.assertIn("text: detailed support up", ladder)
                    self.assertIn("REST 200 long response", ladder)
                    self.assertIn("bot text chunks", ladder)
                    self.assertIn("Piper WAV chunks", ladder)
                if profile_name == "ai-rasa-contact-center-sales-coqui":
                    self.assertIn("connect me to sales", ladder)
                    self.assertIn("REST 200 sales workflow", ladder)
                    self.assertIn("Coqui WAV", ladder)

    def test_kubernetes_rasa_chat_nlu_profiles_are_selectable(self):
        self.assertIn("ai-rasa-chat-nlu", run_k8s_regression.ALL_PROFILES)
        self.assertIn("ai-rasa-chat-negative", run_k8s_regression.ALL_PROFILES)
        self.assertIn("ai-rasa-chat-nlu", run_k8s_regression.SELECTABLE_PROFILES)
        self.assertIn("ai-rasa-chat-negative", run_k8s_regression.SELECTABLE_PROFILES)
        self.assertIn("ai-rasa-chat-nlu", run_k8s_regression.RASA_PROFILES)
        self.assertIn("ai-rasa-chat-negative", run_k8s_regression.RASA_PROFILES)
        self.assertEqual(run_k8s_regression.RASA_NLU_CASE_FILES["ai-rasa-chat-nlu"].name, "chat_nlu_cases.yml")
        self.assertEqual(run_k8s_regression.RASA_NLU_CASE_FILES["ai-rasa-chat-negative"].name, "chat_negative_cases.yml")
        self.assertEqual(run_k8s_regression.profile_display_title("ai-rasa-chat-nlu"), "AI Rasa Chat NLU - Intent Matrix")
        self.assertEqual(run_k8s_regression.profile_display_title("ai-rasa-chat-negative"), "AI Rasa Negative Chat - Guardrails")
        self.assertIn("CHAT-NLU-001", run_k8s_regression.profile_mode_detail("ai-rasa-chat-nlu"))
        self.assertIn("CHAT-NEG-001", run_k8s_regression.profile_mode_detail("ai-rasa-chat-negative"))

    def test_kubernetes_rasa_chat_nlu_ladder_is_not_sipp_call_ladder(self):
        args = run_k8s_regression.parse_args(["--profile", "ai-rasa-chat-nlu"])
        runner = run_k8s_regression.K8sRegressionRunner(args, "unit-rasa")

        ladder = runner.rasa_nlu_ladder("ai-rasa-chat-nlu")

        self.assertIn("NLP CHAT / RASA LADDER", ladder)
        self.assertIn("Chat YAML", ladder)
        self.assertIn("PlaySBC Guard", ladder)
        self.assertIn("Rasa NLU", ladder)
        self.assertIn("Rasa Bot", ladder)
        self.assertIn("HTML Report", ladder)
        self.assertIn("render chat + ladder", ladder)
        self.assertIn("POST /webhook", ladder)
        self.assertNotIn("INVITE", ladder)
        self.assertNotIn("RTPengine", ladder)

    def test_kubernetes_rasa_profile_shortcut_uses_dedicated_outputs(self):
        args = run_k8s_regression.parse_args(["--rasa-profiles"])
        self.assertEqual(run_k8s_regression.selected_profiles(args), run_k8s_regression.RASA_PROFILES)
        self.assertEqual(args.output_root, str(ROOT / "logs" / "RASA-Regression"))
        self.assertEqual(args.report_dir, str(ROOT / "logs" / "RASA-Regression" / "reports"))
        self.assertEqual(args.rollout_timeout, 600)

        job_args = run_k8s_regression_job.parse_args(["--rasa-profiles"])
        command = run_k8s_regression_job.runner_command_args(job_args)

        self.assertEqual(job_args.output_dir, str(ROOT / "logs" / "RASA-Regression"))
        self.assertEqual(job_args.remote_output_root_name, "RASA-Regression")
        self.assertEqual(job_args.remote_report_dir_name, "RASA-reports")
        self.assertTrue(job_args.run_id.startswith("rasa-regression-"))
        self.assertEqual(job_args.rollout_timeout, 600)
        self.assertIn("--rasa-profiles", command)
        self.assertNotIn("--all-profiles", command)
        self.assertIn("--rollout-timeout", command)
        self.assertIn("600", command)
        self.assertIn("/workspace/logs/RASA-Regression", command)
        self.assertIn("/workspace/logs/RASA-reports", command)
        self.assertEqual(len(run_k8s_regression.RASA_PROFILES), 10)
        self.assertIn("ai-rasa-rtpengine-speech-whisper", run_k8s_regression.RASA_PROFILES)
        self.assertIn("ai-rasa-long-response-streaming", run_k8s_regression.RASA_PROFILES)
        self.assertIn("ai-rasa-contact-center-sales-coqui", run_k8s_regression.RASA_PROFILES)

    def test_kubernetes_aks_profile_shortcut_uses_dedicated_outputs_and_validation(self):
        args = run_k8s_regression.parse_args(["--aks-profiles"])
        self.assertEqual(run_k8s_regression.selected_profiles(args), run_k8s_regression.AKS_PROFILES)
        self.assertEqual(args.output_root, str(ROOT / "logs" / "AKS-Regression"))
        self.assertEqual(args.report_dir, str(ROOT / "logs" / "AKS-Regression" / "reports"))
        self.assertTrue(args.aks_mode)
        self.assertTrue(args.aks_require_azure_services)
        self.assertTrue(args.aks_require_static_sip)
        self.assertTrue(args.aks_require_public_sip_ingress)
        self.assertTrue(args.aks_require_public_rtp_ingress)
        self.assertTrue(args.aks_require_rtp_port_range)
        self.assertEqual(args.aks_rtp_port_min, 30000)
        self.assertEqual(args.aks_rtp_port_max, 30049)
        self.assertFalse(args.active_active_topology)
        self.assertIn("tls-transport-policy", run_k8s_regression.AKS_PROFILES)
        self.assertIn("rtpengine-transcoding", run_k8s_regression.AKS_PROFILES)

        job_args = run_k8s_regression_job.parse_args(["--aks-profiles"])
        command = run_k8s_regression_job.runner_command_args(job_args)

        self.assertEqual(job_args.output_dir, str(ROOT / "logs" / "AKS-Regression"))
        self.assertEqual(job_args.remote_output_root_name, "AKS-Regression")
        self.assertEqual(job_args.remote_report_dir_name, "AKS-reports")
        self.assertTrue(job_args.run_id.startswith("aks-regression-"))
        self.assertTrue(job_args.aks_wait_load_balancers)
        self.assertEqual(job_args.aks_load_balancer_wait_timeout, 1200)
        self.assertTrue(job_args.aks_require_public_sip_ingress)
        self.assertTrue(job_args.aks_require_public_rtp_ingress)
        self.assertTrue(job_args.aks_require_rtp_port_range)
        self.assertFalse(job_args.active_active_topology)
        self.assertEqual(job_args.runner_image_pull_policy, "Always")
        self.assertEqual(job_args.sipp_image_pull_policy, "Always")
        manifest = run_k8s_regression_job.job_manifest(job_args)
        containers = manifest["spec"]["template"]["spec"]["containers"]
        self.assertEqual([container["imagePullPolicy"] for container in containers], ["Always", "Always"])
        runner_env = {entry["name"]: entry for entry in containers[0]["env"]}
        self.assertEqual(
            runner_env["PLAYSBC_REGRESSION_RUNNER_IP"]["valueFrom"]["fieldRef"]["fieldPath"],
            "status.podIP",
        )
        self.assertIn("--aks-profiles", command)
        self.assertIn("--aks-mode", command)
        self.assertIn("--no-active-active-topology", command)
        self.assertNotIn("--active-active-topology", command)
        self.assertIn("--aks-require-azure-services", command)
        self.assertIn("--aks-require-static-sip", command)
        self.assertIn("--aks-require-public-sip-ingress", command)
        self.assertIn("--aks-require-public-rtp-ingress", command)
        self.assertIn("--aks-require-rtp-port-range", command)
        self.assertIn("--aks-rtp-port-min", command)
        self.assertIn("30000", command)
        self.assertIn("--aks-rtp-port-max", command)
        self.assertIn("30049", command)
        self.assertNotIn("--all-profiles", command)
        self.assertIn("/workspace/logs/AKS-Regression", command)
        self.assertIn("/workspace/logs/AKS-reports", command)
        self.assertTrue(run_k8s_regression_job.should_cleanup_local_logs(job_args))

    def test_kubernetes_aks_profile_shortcut_can_explicitly_use_active_active(self):
        args = run_k8s_regression.parse_args(["--aks-profiles", "--active-active-topology"])
        job_args = run_k8s_regression_job.parse_args(["--aks-profiles", "--active-active-topology"])
        command = run_k8s_regression_job.runner_command_args(job_args)

        self.assertTrue(args.active_active_topology)
        self.assertTrue(job_args.active_active_topology)
        self.assertIn("--active-active-topology", command)
        self.assertNotIn("--no-active-active-topology", command)

    def test_kubernetes_aks_image_pull_policy_can_be_explicitly_overridden(self):
        args = run_k8s_regression_job.parse_args(
            [
                "--aks-profiles",
                "--runner-image-pull-policy",
                "IfNotPresent",
                "--sipp-image-pull-policy",
                "Never",
            ]
        )

        self.assertEqual(args.runner_image_pull_policy, "IfNotPresent")
        self.assertEqual(args.sipp_image_pull_policy, "Never")

    def test_aks_evidence_archive_is_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_root = root / "aks-regression-unit"
            reports = output_root / "AKS-reports"
            bundles = output_root / "AKS-Regression" / "aks-regression-unit-basic-signalling"
            reports.mkdir(parents=True)
            bundles.mkdir(parents=True)
            (output_root / "runner.log").write_text("runner evidence\n", encoding="utf-8")
            (reports / "latest.html").write_text("<html>AKS report</html>\n", encoding="utf-8")
            (bundles / "log.sip").write_text("SIP evidence\n", encoding="utf-8")

            args = run_k8s_regression_job.parse_args(
                ["--aks-profiles", "--run-id", "aks-regression-unit", "--output-dir", str(root)]
            )
            archive_result = run_k8s_regression_job.create_aks_evidence_archive(args, output_root)

            self.assertIsNotNone(archive_result)
            archive_path, member_count = archive_result or (Path(), 0)
            self.assertEqual(archive_path, root / "latest-aks-regression.tgz")
            self.assertEqual(member_count, 4)
            self.assertTrue((output_root / "archive-manifest.txt").exists())
            manifest = (output_root / "archive-manifest.txt").read_text(encoding="utf-8")
            self.assertIn("files_before_manifest=3", manifest)
            self.assertIn("files_in_archive=4", manifest)
            self.assertIn("path_listing=preview", manifest)
            self.assertIn("listed_paths=3", manifest)
            self.assertIn("omitted_paths=0", manifest)
            with tarfile.open(archive_path, "r:gz") as archive:
                names = archive.getnames()
            self.assertIn("aks-regression-unit/AKS-reports/latest.html", names)
            self.assertIn("aks-regression-unit/AKS-Regression/aks-regression-unit-basic-signalling/log.sip", names)

    def test_aks_load_balancer_wait_polls_until_ingress_ready(self):
        pending = {
            "items": [
                {
                    "metadata": {"name": "playsbc-sip", "labels": {"playsbc.io/exposure": "sip-public"}},
                    "spec": {"type": "LoadBalancer"},
                    "status": {"loadBalancer": {}},
                },
                {
                    "metadata": {"name": "playsbc-rtp", "labels": {"playsbc.io/exposure": "rtp-public"}},
                    "spec": {"type": "LoadBalancer"},
                    "status": {"loadBalancer": {}},
                },
            ]
        }
        ready = {
            "items": [
                {
                    "metadata": {"name": "playsbc-sip", "labels": {"playsbc.io/exposure": "sip-public"}},
                    "spec": {"type": "LoadBalancer"},
                    "status": {"loadBalancer": {"ingress": [{"ip": "20.30.40.50"}]}},
                },
                {
                    "metadata": {"name": "playsbc-rtp", "labels": {"playsbc.io/exposure": "rtp-public"}},
                    "spec": {"type": "LoadBalancer"},
                    "status": {"loadBalancer": {"ingress": [{"ip": "20.30.40.60"}]}},
                },
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            args = run_k8s_regression_job.parse_args(
                [
                    "--aks-profiles",
                    "--run-id",
                    "aks-regression-unit",
                    "--output-dir",
                    str(tmp),
                    "--aks-require-public-sip-ingress",
                    "--aks-load-balancer-wait-timeout",
                    "2",
                    "--aks-load-balancer-poll-interval",
                    "0",
                ]
            )
            responses = [
                run_k8s_regression_job.CommandResult([], 0, 0.0, json.dumps(pending), ""),
                run_k8s_regression_job.CommandResult([], 0, 0.0, json.dumps(ready), ""),
            ]
            with mock.patch.object(run_k8s_regression_job, "run_command", side_effect=responses):
                detail = run_k8s_regression_job.wait_for_aks_load_balancers(args)

            self.assertIn("aks_loadbalancer_wait=ready", detail)
            self.assertIn("20.30.40.50", detail)
            self.assertIn("20.30.40.60", detail)
            preflight_log = Path(tmp) / "aks-regression-unit" / "aks-loadbalancer-preflight.log"
            self.assertIn("pending=playsbc-sip", preflight_log.read_text(encoding="utf-8"))

    def test_aks_load_balancer_wait_requires_rtp_public_service(self):
        sip_only = {
            "items": [
                {
                    "metadata": {"name": "playsbc-sip", "labels": {"playsbc.io/exposure": "sip-public"}},
                    "spec": {"type": "LoadBalancer"},
                    "status": {"loadBalancer": {"ingress": [{"ip": "20.30.40.50"}]}},
                }
            ]
        }
        args = run_k8s_regression_job.parse_args(["--aks-profiles", "--run-id", "aks-regression-unit"])

        with mock.patch.object(
            run_k8s_regression_job,
            "run_command",
            return_value=run_k8s_regression_job.CommandResult([], 0, 0.0, json.dumps(sip_only), ""),
        ):
            ready, detail = run_k8s_regression_job.azure_load_balancer_readiness(args)

        self.assertFalse(ready)
        self.assertIn("missing-rtp-public", detail)

    def test_kubernetes_job_dry_run_does_not_mutate_cluster(self):
        args = run_k8s_regression_job.parse_args(["--aks-profiles", "--dry-run", "--set-playsbc-image"])

        with (
            mock.patch.object(run_k8s_regression_job, "ensure_binary"),
            mock.patch.object(run_k8s_regression_job, "prepare_playsbc_image_values") as prepare,
            mock.patch.object(run_k8s_regression_job, "run_command") as run_command,
            mock.patch("sys.stdout", new_callable=io.StringIO),
        ):
            self.assertEqual(run_k8s_regression_job.run_job(args), 0)

        prepare.assert_not_called()
        run_command.assert_not_called()

    def test_kubernetes_job_uses_macos_sleep_inhibitor(self):
        process = mock.Mock()
        process.poll.return_value = None
        with (
            mock.patch.object(run_k8s_regression_job.sys, "platform", "darwin"),
            mock.patch.object(run_k8s_regression_job.shutil, "which", return_value="/usr/bin/caffeinate"),
            mock.patch.object(run_k8s_regression_job.os, "getpid", return_value=1234),
            mock.patch.object(run_k8s_regression_job.subprocess, "Popen", return_value=process) as popen,
        ):
            inhibitor = run_k8s_regression_job.start_host_sleep_inhibitor()
            run_k8s_regression_job.stop_host_sleep_inhibitor(inhibitor)

        popen.assert_called_once_with(
            ["caffeinate", "-dimsu", "-w", "1234"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=3)

    def test_kubernetes_job_rejects_empty_acr_image_before_cluster_mutation(self):
        args = run_k8s_regression_job.parse_args(
            [
                "--aks-profiles",
                "--playsbc-image",
                ".azurecr.io/playsbc:2.5.0",
                "--set-playsbc-image",
                "--runner-image",
                ".azurecr.io/playsbc-k8s-regression:2.5.0",
                "--sipp-image",
                ".azurecr.io/playsbc-sipp:2.5.0",
            ]
        )

        with (
            mock.patch.object(run_k8s_regression_job, "ensure_binary") as ensure_binary,
            mock.patch.object(run_k8s_regression_job, "prepare_playsbc_image_values") as prepare,
            mock.patch.object(run_k8s_regression_job, "run_command") as run_command,
            self.assertRaisesRegex(SystemExit, "Re-export ACR_NAME/ACR_LOGIN_SERVER"),
        ):
            run_k8s_regression_job.run_job(args)

        ensure_binary.assert_not_called()
        prepare.assert_not_called()
        run_command.assert_not_called()

    def test_kubernetes_job_accepts_registry_qualified_aks_images(self):
        args = run_k8s_regression_job.parse_args(
            [
                "--aks-profiles",
                "--runner-image",
                "playsbcacr.example.azurecr.io/playsbc-k8s-regression:2.5.0",
                "--sipp-image",
                "playsbcacr.example.azurecr.io/playsbc-sipp:2.5.0",
                "--playsbc-image",
                "playsbcacr.example.azurecr.io/playsbc:2.5.0",
                "--rtpengine-image",
                "playsbcacr.example.azurecr.io/playsbc-rtpengine:2.5.0",
                "--set-playsbc-image",
                "--set-rtpengine-image",
            ]
        )

        run_k8s_regression_job.validate_requested_images(args)

    def test_kubernetes_full_suite_keeps_existing_output_layout(self):
        args = run_k8s_regression_job.parse_args(["--all-profiles"])
        command = run_k8s_regression_job.runner_command_args(args)

        self.assertEqual(args.output_dir, str(ROOT / "logs" / "k8s-job"))
        self.assertEqual(args.remote_output_root_name, "k8s-Regression")
        self.assertEqual(args.remote_report_dir_name, "k8s-reports")
        self.assertTrue(args.run_id.startswith("k8s-regression-"))
        self.assertEqual(args.runner_image_pull_policy, "IfNotPresent")
        self.assertEqual(args.sipp_image_pull_policy, "IfNotPresent")
        self.assertIn("--all-profiles", command)
        self.assertNotIn("--rasa-profiles", command)
        self.assertIn("--active-active-topology", command)
        self.assertIn("--playsbc-replicas", command)
        self.assertIn("2", command)
        self.assertIn("--rtpengine-replicas", command)
        self.assertIn("--no-multus-enabled", command)
        self.assertIn("/workspace/logs/k8s-Regression", command)
        self.assertIn("/workspace/logs/k8s-reports", command)
        self.assertTrue(run_k8s_regression_job.should_cleanup_local_logs(args))

    def test_kubernetes_specific_profile_keeps_existing_job_output(self):
        args = run_k8s_regression_job.parse_args(["--profile", "basic-signalling"])
        command = run_k8s_regression_job.runner_command_args(args)

        self.assertEqual(args.output_dir, str(ROOT / "logs" / "k8s-job"))
        self.assertEqual(args.rollout_timeout, 120)
        self.assertIn("--profile", command)
        self.assertIn("basic-signalling", command)
        self.assertFalse(run_k8s_regression_job.should_cleanup_local_logs(args))

    def test_kubernetes_auth_failure_ladder_matches_second_401(self):
        args = run_k8s_regression.parse_args(["--all-profiles"])
        runner = run_k8s_regression.K8sRegressionRunner(args, "unit-k8s")
        route = server.RouteResult(
            target=server.SipUri("1001", "peer.example", 5060, "udp"),
            source="unit",
            policy_name="unit",
            original_user="1001",
            routed_user="1001",
        )
        flow = server.B2BUAFlowLog(
            None,
            "unit-call",
            "1001",
            route,
            participants=("Core SIPp A", "PlaySBC", "Peer SIPp B"),
        )

        runner.add_registration_flow(flow, SimpleNamespace(name="register-auth-failure"), "Peer SIPp B", "failure")
        ladder = flow.render_ladder_text()

        self.assertIn("REGISTER + bad digest", ladder)
        self.assertIn("401 Unauthorized", ladder)
        self.assertNotIn("403 Forbidden", ladder)

    def test_kubernetes_detects_statefulset_immutable_helm_migration_error(self):
        detail = (
            'Error: UPGRADE FAILED: cannot patch "playsbc-playsbc" with kind StatefulSet: '
            'StatefulSet.apps "playsbc-playsbc" is invalid: spec: Forbidden: updates to '
            "statefulset spec for fields other than 'replicas' are forbidden"
        )

        self.assertTrue(run_k8s_regression.is_statefulset_immutable_upgrade_error(detail))
        self.assertFalse(run_k8s_regression.is_statefulset_immutable_upgrade_error("Error: service unavailable"))

    def test_kubernetes_extracts_rtcp_target_from_received_sdp(self):
        trace = """
----------------------------------------------- 2026-07-14T10:45:49Z
UDP message received [603] bytes:

SIP/2.0 200 OK
Content-Type: application/sdp

v=0
c=IN IP4 10.244.0.12
m=audio 25100 RTP/AVP 0 101
a=rtcp:25101
"""

        target = run_k8s_regression.extract_received_sdp_rtcp_target(trace, sip_start="SIP/2.0 200")

        self.assertIsNotNone(target)
        self.assertEqual(target.target_ip, "10.244.0.12")
        self.assertEqual(target.target_port, 25101)

    def test_kubernetes_sipp_tls_retry_noise_is_removed_from_final_stderr(self):
        filtered, count = run_k8s_regression.normalize_sipp_stderr(
            "first line\nSSL_ERROR_WANT_READ temporary retry\n"
            "Overload warning: the minor watchdog timer\n"
            "There were more errors, see '/tmp/errors.log'\nlast line\n"
        )

        self.assertEqual(count, 3)
        self.assertIn("first line", filtered)
        self.assertIn("last line", filtered)
        self.assertNotIn("suppressed", filtered)
        self.assertNotIn("temporary retry", filtered)
        self.assertNotIn("watchdog", filtered)
        self.assertNotIn("more errors", filtered)

    def test_kubernetes_evidence_scopes_playsbc_logs_to_profile_call_ids(self):
        text = (
            "SERVER CONFIG media_backend=rtpengine\n"
            "SIP INVITE call_id=profile-call@example.test\n"
            "SIP ACK call_id=profile-call@example.test..\n"
            "SIP INVITE call_id=unrelated-call@example.test\n"
        )

        scoped, removed = run_k8s_regression.scope_playsbc_log(
            text, {"profile-call@example.test"}
        )

        self.assertEqual(removed, 1)
        self.assertIn("SERVER CONFIG", scoped)
        self.assertIn("profile-call", scoped)
        self.assertNotIn("unrelated-call", scoped)

    def test_kubernetes_previous_logs_require_a_real_restart(self):
        fresh = {"status": {"containerStatuses": [{"name": "playsbc", "restartCount": 0}]}}
        restarted = {"status": {"containerStatuses": [{"name": "playsbc", "restartCount": 1}]}}
        terminated = {
            "status": {
                "containerStatuses": [
                    {
                        "name": "playsbc",
                        "restartCount": 0,
                        "lastState": {"terminated": {"exitCode": 1}},
                    }
                ]
            }
        }

        self.assertFalse(run_k8s_regression.pod_has_previous_container_logs(fresh, "playsbc"))
        self.assertTrue(run_k8s_regression.pod_has_previous_container_logs(restarted, "playsbc"))
        self.assertTrue(run_k8s_regression.pod_has_previous_container_logs(terminated, "playsbc"))

    def test_topology_pcaps_merge_in_timestamp_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            later = root / "later.pcap"
            earlier = root / "earlier.pcap"
            merged = root / "capture.pcap"
            write_test_pcap(later, 20.0, b"later")
            write_test_pcap(earlier, 10.0, b"earlier")

            count = run_real_topology.merge_pcaps([later, earlier], merged)
            _major, _minor, _linktype, records = run_real_topology.pcap_records(merged)

            self.assertEqual(count, 2)
            self.assertEqual([record.data for record in records], [b"earlier", b"later"])

    def test_topology_pcap_reads_rtp_payload_type_by_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rtp.pcap"
            rtp = bytes([0x80, 0x08]) + bytes(10)
            packet = run_b2bua_sipp_smoke.PcapPacket(
                timestamp=10.0,
                src_ip="192.168.28.40",
                src_port=30000,
                dst_ip="192.168.28.30",
                dst_port=6000,
                payload=rtp,
            )
            frame = run_b2bua_sipp_smoke.ethernet_ipv4_udp_packet(packet, 1)
            write_test_pcap(path, 10.0, frame)

            payloads = run_real_topology.rtp_payload_types(path)

            self.assertEqual(payloads[("192.168.28.40", "192.168.28.30")], {8})

    def test_topology_combines_sipp_summaries_and_removes_leg_folders(self):
        header = "TotalCallCreated;SuccessfulCall(C);FailedCall(C);Retransmissions(C);Warnings(C);FatalErrors(C);CallLength(C);\n"
        row = "1;1;0;0;0;0;00:01:00;\n"
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            for folder_name in ("sipp-a", "sipp-b"):
                folder = bundle / folder_name
                folder.mkdir()
                (folder / "stats.csv").write_text(header + row, encoding="utf-8")
                (folder / "messages.log").write_text("raw per-leg trace", encoding="utf-8")

            run_real_topology.consolidate_sipp_evidence(bundle)

            combined = (bundle / "log.sipp").read_text(encoding="utf-8")
            self.assertIn("CORE LEG SIPP RESULT", combined)
            self.assertIn("PEER LEG SIPP RESULT", combined)
            self.assertEqual(combined.count("calls_created=1 successful=1 failed=0"), 2)
            self.assertFalse((bundle / "sipp-a").exists())
            self.assertFalse((bundle / "sipp-b").exists())


def argparse_namespace(**values):
    class Namespace:
        pass

    namespace = Namespace()
    for key, value in values.items():
        setattr(namespace, key, value)
    return namespace


if __name__ == "__main__":
    unittest.main()
