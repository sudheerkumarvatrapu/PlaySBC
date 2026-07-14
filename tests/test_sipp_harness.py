import json
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

import mini_call_server as server
from tools import run_sipp_regression
from tools import run_b2bua_sipp_smoke
from tools import run_regression_suite
from tools import run_real_topology
from tools import run_dual_realm_profile
from tools import run_k8s_regression


ROOT = Path(__file__).resolve().parents[1]


def write_test_pcap(path: Path, timestamp: float, payload: bytes, linktype: int = 1):
    seconds = int(timestamp)
    microseconds = int((timestamp - seconds) * 1_000_000)
    path.write_bytes(
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
        self.assertIn("toYaml $config", configmap)
        self.assertIn("/etc/playsbc/server.yaml", deployment)

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
        rows = [
            run_regression_suite.ReportRow("SIPp Smoke", "options", "passed", 0, 0.1, "/tmp/logs", "cmd"),
            run_regression_suite.ReportRow("B2BUA", "media", "failed", 1, 0.2, "/tmp/logs", "cmd"),
            run_regression_suite.ReportRow("B2BUA", "rtpengine-preflight", "blocked", None, 0.01, "/tmp/logs", "cmd"),
        ]

        report = run_regression_suite.render_html(rows, "2026-06-13 10:00:00 IST", "unit-report")

        self.assertIn("PlaySBC Regression Report", report)
        self.assertIn("PASSED", report)
        self.assertIn("FAILED", report)
        self.assertIn("BLOCKED", report)
        self.assertIn("Blocked: 1", report)
        self.assertIn("badge pass", report)
        self.assertIn("badge fail", report)
        self.assertIn("badge blocked", report)
        self.assertIn("Robot-style execution log", report)
        self.assertIn("Keyword / Phase", report)

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
            self.assertIn("<h2>SIP Ladders</h2>", report)
            self.assertIn("SIPp A", report)

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
        self.assertIn("real-topology-rtpengine-transcoding", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("esbc-trunk-failover", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("ha-shared-state-rtpengine", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("ha-options-health-recovery", run_regression_suite.ALL_B2BUA_PROFILES)
        self.assertIn("ha-node-draining", run_regression_suite.ALL_B2BUA_PROFILES)
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
        self.assertEqual(run_b2bua_sipp_smoke.rtcp_expected_sender_names(args), ("rtcp-a",))

    def test_dual_realm_ai_rtpengine_profile_anchors_media_with_rtpengine(self):
        args = run_dual_realm_profile.profile_args("ai-rasa-rtpengine", "ai-rtpengine", "b2bua-Regression")

        self.assertTrue(run_dual_realm_profile.needs_ai_mock(args))
        self.assertEqual(args.media_backend, "rtpengine")
        self.assertFalse(args.start_uas)
        self.assertEqual(args.rasa_mock_response_count, 2)
        self.assertEqual(args.rasa_mock_action, "transfer")
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
        self.assertFalse(probe.run_call)
        self.assertFalse(probe.start_uas)
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
        self.assertIn("cryptosuiteaescm128sha1801audio", secure)
        self.assertIn("[rtpstream_audio_port]", secure)
        self.assertNotIn("play_pcap_audio", secure)
        self.assertIn('rtp_echo="startaudio,0,PCMU/8000"', secure)
        self.assertIn("RTP/AVP", plain)
        self.assertIn("play_pcap_audio", plain)

    def test_sipp_docker_image_is_built_with_tls_and_pcap(self):
        dockerfile = (ROOT / "docker" / "sipp.Dockerfile").read_text(encoding="utf-8")

        self.assertIn("SIPP_VERSION=v3.7.7", dockerfile)
        self.assertIn("-DUSE_SSL=1", dockerfile)
        self.assertIn("-DUSE_PCAP=1", dockerfile)
        self.assertIn("python3", dockerfile)
        self.assertIn("send_rtcp_reports.py", dockerfile)

    def test_kubernetes_chart_has_health_secret_rtpengine_and_affinity_lab(self):
        chart = ROOT / "charts" / "playsbc"
        deployment = (chart / "templates" / "deployment.yaml").read_text(encoding="utf-8")
        configmap = (chart / "templates" / "configmap.yaml").read_text(encoding="utf-8")
        rtpengine = (chart / "templates" / "rtpengine.yaml").read_text(encoding="utf-8")
        kind_values = (ROOT / "configs" / "kubernetes" / "kind-values.yaml").read_text(encoding="utf-8")

        self.assertIn("readinessProbe:", deployment)
        self.assertIn("livenessProbe:", deployment)
        self.assertIn("users_file", configmap)
        self.assertIn("rtpengine_url", configmap)
        self.assertIn("status.hostIP", rtpengine)
        self.assertIn("sessionAffinity", rtpengine)
        self.assertIn("rtpengine:\n  enabled: true", kind_values)

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

    def test_kubernetes_pcap_capture_roles_follow_expected_traffic(self):
        cases = {
            "basic-media": ("core", "peer"),
            "register-auth-failure": ("peer",),
            "ai-rasa-lab": ("core",),
            "unknown-route": ("core",),
            "ha-options-health-recovery": (),
            "load-5cps-60s": (),
        }

        for profile_name, expected in cases.items():
            with self.subTest(profile=profile_name):
                profile = run_k8s_regression.profile_values(profile_name, "unit-k8s")
                self.assertEqual(run_k8s_regression.k8s_pcap_capture_roles(profile), expected)

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

        runner.add_registration_flow(flow, "Peer SIPp B", "failure")
        ladder = flow.render_ladder_text()

        self.assertIn("REGISTER + bad digest", ladder)
        self.assertIn("401 Unauthorized", ladder)
        self.assertNotIn("403 Forbidden", ladder)

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

    def test_kubernetes_sipp_tls_retry_noise_is_summarized(self):
        filtered, count = run_k8s_regression.normalize_sipp_stderr(
            "first line\nSSL_ERROR_WANT_READ temporary retry\nlast line\n"
        )

        self.assertEqual(count, 1)
        self.assertIn("first line", filtered)
        self.assertIn("last line", filtered)
        self.assertIn("suppressed 1 non-fatal", filtered)
        self.assertNotIn("temporary retry", filtered)

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
