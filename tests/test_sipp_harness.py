import json
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from tools import run_sipp_regression
from tools import run_b2bua_sipp_smoke


ROOT = Path(__file__).resolve().parents[1]


class SippScenarioTests(unittest.TestCase):
    def test_all_xml_scenarios_are_well_formed(self):
        scenarios = ROOT / "sipp" / "scenarios"
        for scenario in sorted(scenarios.glob("*.xml")):
            with self.subTest(scenario=scenario.name):
                ET.parse(scenario)

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
                self.assertTrue((run_dir / scenario / "command.txt").exists())

    def test_b2bua_sipp_commands_support_load_and_hold_time(self):
        args = argparse_namespace(
            host="127.0.0.1",
            server_port=25062,
            uac_port=25081,
            uas_port=25082,
            register_port=25083,
            server_rtp_min=25100,
            server_rtp_max=25400,
            uac_rtp_min=26000,
            uac_rtp_max=26200,
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

    def test_b2bua_sipp_commands_can_enable_g711_pcap_media(self):
        args = argparse_namespace(
            host="127.0.0.1",
            server_port=25062,
            uac_port=25081,
            uas_port=25082,
            register_port=25083,
            server_rtp_min=25100,
            server_rtp_max=25400,
            uac_rtp_min=26000,
            uac_rtp_max=26200,
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
            uac_scenario=ROOT / "sipp" / "scenarios" / "b2bua_uac_a_media.xml",
            uas_scenario=ROOT / "sipp" / "scenarios" / "b2bua_uas_b_media.xml",
        )

        uac = run_b2bua_sipp_smoke.build_uac_command(args, "sipp")
        uas = run_b2bua_sipp_smoke.build_uas_command(args, "sipp")

        self.assertIn("b2bua_uac_a_media.xml", " ".join(uac))
        self.assertIn("b2bua_uas_b_media.xml", " ".join(uas))
        self.assertNotIn("-key", uac)
        self.assertNotIn("-key", uas)

    def test_b2bua_media_scenarios_resolve_pcap_path_per_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "media-run"
            (run_dir / "sipp-a-uac").mkdir(parents=True)
            (run_dir / "sipp-b-uas").mkdir(parents=True)
            args = argparse_namespace(
                media_enabled=True,
                media_pcap="pcap/g711u_60s.pcap",
                media_driver="sipp-pcap",
            )

            run_b2bua_sipp_smoke.prepare_media_scenarios(args, run_dir)

            self.assertTrue(args.uac_scenario.exists())
            self.assertTrue(args.uas_scenario.exists())
            self.assertIn(str(ROOT / "sipp" / "scenarios" / "pcap" / "g711u_60s.pcap"), args.uac_scenario.read_text(encoding="ISO-8859-1"))
            self.assertNotIn("[media_pcap]", args.uac_scenario.read_text(encoding="ISO-8859-1"))

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

    def test_b2bua_sipp_dry_run_writes_summary_and_commands(self):
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
            run_dir = Path(tmp) / "b2bua-dry-run"
            summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["callee"], "drycallee")
            self.assertEqual(summary["rate"], 5)
            self.assertEqual(summary["hold_ms"], 60000)
            self.assertFalse(summary["ladder_enabled"])
            server_config = json.loads((run_dir / "server-config.json").read_text(encoding="utf-8"))
            self.assertFalse(server_config["b2bua_ladder_logs"])
            self.assertEqual(server_config["media_backend"], "internal")
            self.assertTrue((run_dir / "server-command.txt").exists())
            self.assertTrue((run_dir / "uac-command.txt").exists())
            self.assertTrue((run_dir / "uas-command.txt").exists())

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
            run_dir = Path(tmp) / "b2bua-media-dry-run"
            summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
            server_config = json.loads((run_dir / "server-config.json").read_text(encoding="utf-8"))
            self.assertTrue(summary["media_enabled"])
            self.assertEqual(summary["media_codec"], "PCMU")
            self.assertEqual(summary["media_driver"], "python")
            self.assertEqual(summary["media_pcap"], str(ROOT / "sipp" / "scenarios" / "pcap" / "g711u_60s.pcap"))
            self.assertEqual(server_config["default_codec"], "PCMU")
            self.assertIn("b2bua_uac_a.xml", (run_dir / "uac-command.txt").read_text(encoding="utf-8"))
            self.assertTrue((run_dir / "media-a-to-b2bua-command.txt").exists())
            self.assertTrue((run_dir / "media-b-to-b2bua-command.txt").exists())

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
            run_dir = Path(tmp) / "b2bua-basic-dry-run"
            summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
            server_config = json.loads((run_dir / "server-config.json").read_text(encoding="utf-8"))
            self.assertTrue(summary["ladder_enabled"])
            self.assertTrue(server_config["b2bua_ladder_logs"])

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
            run_dir = Path(tmp) / "b2bua-rtpengine-dry-run"
            summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
            server_config = json.loads((run_dir / "server-config.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["media_backend"], "rtpengine")
            self.assertEqual(server_config["media_backend"], "rtpengine")
            self.assertEqual(server_config["rtpengine_url"], "udp://127.0.0.1:2223")


def argparse_namespace(**values):
    class Namespace:
        pass

    namespace = Namespace()
    for key, value in values.items():
        setattr(namespace, key, value)
    return namespace


if __name__ == "__main__":
    unittest.main()
