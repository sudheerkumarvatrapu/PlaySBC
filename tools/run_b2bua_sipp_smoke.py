#!/usr/bin/env python3
"""Run a registrar-backed SIPp B2BUA smoke or small load test."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from rtp.rtcp import build_compound_sender_report
SCENARIO_DIR = ROOT / "sipp" / "scenarios"
MEDIA_PCAPS = {
    "PCMU": "pcap/g711u_60s.pcap",
    "PCMA": "pcap/g711a_60s.pcap",
}
MEDIA_PAYLOAD_TYPES = {
    "PCMU": 0,
    "PCMA": 8,
}
MEDIA_RTPMAP_LINES = {
    "PCMU": "a=rtpmap:0 PCMU/8000",
    "PCMA": "a=rtpmap:8 PCMA/8000",
}
DEFAULT_ROUTE_POLICIES = [
    {
        "name": "registered-endpoints",
        "match": "*",
        "target": "registration",
        "priority": 10,
    }
]
STATIC_TRUNK_ROUTE_POLICY = [
    {
        "name": "esbc-static-trunk",
        "match": "*",
        "target": "sip:{user}@{host}:{uas_port}",
        "priority": 20,
    }
]
RTPENGINE_SDES_AES_CM_128_ONLY = [
    "no-AEAD_AES_256_GCM",
    "no-AEAD_AES_128_GCM",
    "no-AES_256_CM_HMAC_SHA1_80",
    "no-AES_256_CM_HMAC_SHA1_32",
    "no-AES_192_CM_HMAC_SHA1_80",
    "no-AES_192_CM_HMAC_SHA1_32",
    "no-AES_CM_128_HMAC_SHA1_32",
    "no-F8_128_HMAC_SHA1_80",
    "no-F8_128_HMAC_SHA1_32",
    "no-NULL_HMAC_SHA1_80",
    "no-NULL_HMAC_SHA1_32",
]
CRLF = "\r\n"
DIAGNOSTIC_PCAP_PORT = 65530
LOG_FILES = (
    "log.sip",
    "log.media",
    "log.transcoding",
    "log.ai",
    "log.platform",
    "log.networking",
    "log.udp",
    "log.tcp",
    "log.tls",
    "log.call",
    "log.sipp",
)
DEFAULT_LOG_FOLDER = "b2bua-Regression"
SIP_TRACE_LEG_LABELS = {
    "registration-caller": {
        "sent": ("CORE SIPp A", "PlaySBC CORE"),
        "received": ("PlaySBC CORE", "CORE SIPp A"),
    },
    "sipp-a-uac": {
        "sent": ("CORE SIPp A", "PlaySBC CORE"),
        "received": ("PlaySBC CORE", "CORE SIPp A"),
    },
    "registration-callee": {
        "sent": ("PEER SIPp B", "PlaySBC PEER"),
        "received": ("PlaySBC PEER", "PEER SIPp B"),
    },
    "sipp-b-uas": {
        "sent": ("PEER SIPp B", "PlaySBC PEER"),
        "received": ("PlaySBC PEER", "PEER SIPp B"),
    },
}
SIP_TRACE_LEG_ORDER = {
    "registration-caller": 10,
    "sipp-a-uac": 20,
    "sipp-b-uas": 30,
    "registration-callee": 40,
}
SIPP_PCAP_SUDO_BLOCKED_DETAIL = (
    "SIPp PCAP sudo mode requires cached sudo credentials. "
    "Run `sudo -v` in your terminal, then retry with `--sipp-pcap-sudo`."
)
BASE_DEFAULTS = {
    "host": "127.0.0.1",
    "server_host": "",
    "server_port": 25062,
    "sip_transport": "udp",
    "uac_transport": "",
    "uas_transport": "",
    "uac_port": 25081,
    "uas_port": 25082,
    "register_port": 25083,
    "caller_register_port": 25084,
    "server_rtp_min": 25100,
    "server_rtp_max": 25400,
    "uac_rtp_min": 36000,
    "uac_rtp_max": 36200,
    "uas_rtp_min": 27000,
    "uas_rtp_max": 27200,
    "caller": "sipp-a",
    "callee": "callee",
    "register_callee": True,
    "register_caller": False,
    "start_uas": True,
    "reject_unknown_routes": False,
    "calls": 1,
    "rate": 1,
    "hold_ms": 1000,
    "media_codec": None,
    "media_pcap": None,
    "media_driver": "python",
    "sipp_pcap_sudo": False,
    "media_start_delay": 1.0,
    "media_delivery_threshold_percent": 100.0,
    "media_per_call_threshold_percent": 100.0,
    "server_codec": None,
    "media_backend": "internal",
    "rtpengine_url": "udp://127.0.0.1:2223",
    "rtpengine_timeout": 3.0,
    "rtpengine_directions": ["core", "peer"],
    "rtpengine_interfaces": ["core", "peer"],
    "rtpengine_max_sessions": -1,
    "rtpengine_offer_transport_protocol": "",
    "rtpengine_answer_transport_protocol": "",
    "rtpengine_sdes": [],
    "rtpengine_dtls": "",
    "uac_srtp": False,
    "uas_srtp": False,
    "trunk_groups": [],
    "hunt_groups": [],
    "number_normalization": [],
    "header_normalization": {},
    "transport_policies": [],
    "call_admission": {},
    "media_quality": {},
    "ai_voice_gateway": {},
    "ha": {},
    "rasa_mock_response_count": 1,
    "rasa_mock_action": "",
    "rasa_mock_action_target": "",
    "rtcp_receiver_reports": False,
    "rtcp_enabled": True,
    "expected_log_markers": {},
    "tls_certfile": "",
    "tls_keyfile": "",
    "tls_cafile": "",
    "tls_verify_peer": False,
    "registration_driver": "sipp",
    "registration_scenario": "register_contact.xml",
    "registration_auth_expected": "",
    "registration_username": "",
    "registration_password": "",
    "users": {},
    "run_call": True,
    "dtmf_expected": False,
    "uac_scenario": "",
    "uas_scenario": "",
    "ladder": None,
    "output_root": "",
    "log_folder": DEFAULT_LOG_FOLDER,
    "run_id": "",
    "sipp_bin": "sipp",
    "helm_bin": "helm",
    "pcap_topology": "logical",
    "pcap_uac_ip": "10.10.10.10",
    "pcap_server_ip": "10.10.10.20",
    "pcap_uas_ip": "10.10.10.30",
    "pcap_rtpengine_ip": "10.10.10.40",
    "dry_run": False,
}
B2BUA_PROFILES = {
    "basic-signalling": {
        "callee": "basic-sig",
    },
    "basic-media": {
        "callee": "basic-media",
        "media_codec": "PCMU",
        "hold_ms": 60000,
    },
    "transcoding": {
        "callee": "transcode-user",
        "media_codec": "PCMU",
        "server_codec": "PCMA",
        "hold_ms": 60000,
    },
    "rtpengine": {
        "callee": "rtpengine-user",
        "media_backend": "rtpengine",
    },
    "rtpengine-media": {
        "callee": "rtpengine-media-user",
        "media_backend": "rtpengine",
        "media_codec": "PCMU",
        "media_driver": "sipp-pcap",
        "hold_ms": 60000,
    },
    "rtpengine-transcoding": {
        "callee": "rtpengine-transcode-user",
        "media_backend": "rtpengine",
        "media_codec": "PCMU",
        "media_driver": "sipp-pcap",
        "server_codec": "PCMA",
        "hold_ms": 60000,
    },
    "tcp-rtpengine-transcoding": {
        "caller": "tcp-rtpengine-a",
        "callee": "tcp-rtpengine-b",
        "sip_transport": "tcp",
        "media_backend": "rtpengine",
        "media_codec": "PCMU",
        "media_driver": "sipp-pcap",
        "server_codec": "PCMA",
        "hold_ms": 60000,
    },
    "registered-inbound": {
        "caller": "reg-inbound-a",
        "callee": "registered-b",
        "uac_scenario": "uac-reg-inbound.xml",
        "uas_scenario": "uas-reg-inbound.xml",
    },
    "registered-outbound": {
        "caller": "registered-a",
        "callee": "registered-b",
        "register_caller": True,
        "uac_scenario": "uac-reg-outbound.xml",
        "uas_scenario": "uas-reg-outbound.xml",
    },
    "register-auth-success": {
        "caller": "auth-a",
        "callee": "1001",
        "registration_scenario": "register_digest.xml",
        "registration_auth_expected": "success",
        "registration_username": "1001",
        "registration_password": "secret-password",
        "users": {"1001": "secret-password"},
    },
    "register-auth-failure": {
        "callee": "1001",
        "registration_scenario": "register_digest_failure.xml",
        "registration_auth_expected": "failure",
        "registration_username": "1001",
        "registration_password": "wrong-password",
        "users": {"1001": "secret-password"},
        "register_callee": True,
        "start_uas": False,
        "run_call": False,
    },
    "dtmf-rfc4733": {
        "callee": "dtmf-b",
        "media_codec": "PCMU",
        "media_driver": "sipp-pcap",
        "hold_ms": 10000,
        "dtmf_expected": True,
    },
    "ai-rasa-lab": {
        "caller": "ai-core-a",
        "callee": "ai-bot",
        "register_callee": False,
        "start_uas": False,
        "media_codec": "PCMU",
        "media_driver": "sipp-pcap",
        "hold_ms": 10000,
        "route_policies": [
            {
                "name": "ai-rasa-gateway",
                "match": "ai-bot",
                "target": "ai-gateway:rasa-support",
                "priority": 5,
            }
        ],
        "ai_voice_gateway": {
            "enabled": True,
            "provider": "rasa",
            "bot_name": "rasa-support",
            "rasa_webhook_url": "http://172.28.0.60:5005/webhooks/rest/webhook",
            "rasa_timeout": 3.0,
            "input_mode": "scripted",
            "initial_message": "hello from playsbc voice",
            "fallback_text": "Rasa lab bot is unavailable",
        },
        "expected_log_markers": {
            "log.ai": [
                "AI VOICE CALL START",
                "AI STT INPUT",
                "audio_decoded=false",
                "RASA REST REQUEST",
                "RASA REST RESPONSE",
                "AI TTS OUTPUT",
                "rtp_prompt_generated=false",
            ],
            "log.media": ["AI RTP INPUT ONLY"],
            "log.sip": ["AI VOICE CALL LADDER"],
        },
    },
    "ai-rasa-rtpengine": {
        "caller": "ai-core-a",
        "callee": "ai-bot",
        "register_callee": False,
        "start_uas": False,
        "media_backend": "rtpengine",
        "media_codec": "PCMU",
        "media_driver": "sipp-pcap",
        "hold_ms": 10000,
        "route_policies": [
            {
                "name": "ai-rasa-rtpengine-gateway",
                "match": "ai-bot",
                "target": "ai-gateway:rasa-support",
                "priority": 5,
            }
        ],
        "ai_voice_gateway": {
            "enabled": True,
            "provider": "rasa",
            "bot_name": "rasa-support",
            "rasa_webhook_url": "http://172.28.0.60:5005/webhooks/rest/webhook",
            "rasa_timeout": 3.0,
            "input_mode": "scripted",
            "initial_message": "hello from playsbc rtpengine voice",
            "fallback_text": "Rasa lab bot is unavailable",
            "stt_provider": "lab-scripted",
            "tts_provider": "text-only",
            "response_mode": "streaming",
            "bot_actions_enabled": True,
        },
        "rasa_mock_response_count": 2,
        "rasa_mock_action": "transfer",
        "rasa_mock_action_target": "sip:agent@peer.example",
        "expected_log_markers": {
            "log.ai": [
                "AI VOICE CALL START",
                "media_backend=rtpengine",
                "AI STT RESULT",
                "response_mode=streaming",
                "AI BOT ACTION",
                "AI BOT TRANSFER",
            ],
            "log.media": ["AI RTPENGINE MEDIA", "AI RTPENGINE ANSWER", "B2BUA RTPENGINE QUERY"],
            "log.sip": ["AI VOICE CALL LADDER", "RTPengine"],
        },
    },
    "invalid-bye": {
        "callee": "invalid-bye-b",
        "uac_scenario": "invalid_bye.xml",
        "start_uas": False,
        "register_callee": False,
        "reject_unknown_routes": False,
    },
    "unknown-route": {
        "callee": "unknown-route-user",
        "uac_scenario": "b2bua_uac_unknown_route.xml",
        "start_uas": False,
        "register_callee": False,
        "reject_unknown_routes": True,
    },
    "failed-outbound": {
        "callee": "failed-outbound-b",
        "uac_scenario": "b2bua_uac_failed_outbound.xml",
        "uas_scenario": "b2bua_uas_failed_outbound.xml",
    },
    "cancel": {
        "callee": "cancel-b",
        "uac_scenario": "b2bua_uac_cancel.xml",
        "uas_scenario": "b2bua_uas_cancel.xml",
    },
    "retransmission": {
        "callee": "retransmit-b",
        "uac_scenario": "b2bua_uac_retransmit_invite.xml",
    },
    "small-load-2cps-10s": {
        "callee": "small-load-user",
        "calls": 20,
        "rate": 2,
        "hold_ms": 10000,
        "server_rtp_max": 25600,
        "ladder": False,
    },
    "soak-1cps-30s": {
        "callee": "soak-user",
        "calls": 30,
        "rate": 1,
        "hold_ms": 30000,
        "server_rtp_max": 25600,
        "ladder": False,
    },
    "load-5cps-60s": {
        "callee": "load-user",
        "calls": 300,
        "rate": 5,
        "hold_ms": 60000,
        "server_rtp_max": 26500,
        "ladder": False,
    },
    "load-5cps-60s-rtpengine-transcoding": {
        "callee": "load-rtpengine-transcode",
        "calls": 300,
        "rate": 5,
        "hold_ms": 60000,
        "media_codec": "PCMU",
        "media_driver": "sipp-pcap",
        "server_codec": "PCMA",
        "media_backend": "rtpengine",
        "rtpengine_timeout": 8.0,
        "media_delivery_threshold_percent": 99.5,
        "media_per_call_threshold_percent": 99.0,
        "ladder": False,
    },
    "esbc-options-keepalive": {
        "callee": "esbc-options",
        "uac_scenario": "options.xml",
        "register_callee": False,
        "start_uas": False,
    },
    "esbc-static-trunk-route": {
        "caller": "enterprise-a",
        "callee": "trunk-b",
        "register_callee": False,
        "route_policies": STATIC_TRUNK_ROUTE_POLICY,
    },
    "esbc-e164-route-policy": {
        "caller": "enterprise-a",
        "callee": "+18005550100",
        "register_callee": False,
        "route_policies": [
            {
                "name": "esbc-e164-outbound",
                "match": "+1800*",
                "target": "sip:{user}@{host}:{uas_port}",
                "priority": 20,
            }
        ],
    },
    "esbc-trunk-failure": {
        "caller": "enterprise-a",
        "callee": "trunk-failure-b",
        "register_callee": False,
        "uac_scenario": "b2bua_uac_failed_outbound.xml",
        "uas_scenario": "b2bua_uas_failed_outbound.xml",
        "route_policies": STATIC_TRUNK_ROUTE_POLICY,
    },
    "esbc-trunk-failover": {
        "caller": "enterprise-a",
        "callee": "failover-b",
        "register_callee": False,
        "route_policies": [
            {"name": "enterprise-trunks", "match": "*", "target": "trunk-group:enterprise", "priority": 10}
        ],
        "trunk_groups": [
            {
                "name": "enterprise",
                "strategy": "priority",
                "members": [
                    {
                        "name": "primary",
                        "uri": "sip:{user}@{host}:{uas_port}",
                        "priority": 10,
                        "state": "down",
                    },
                    {
                        "name": "secondary",
                        "uri": "sip:{user}@{host}:{uas_port}",
                        "priority": 20,
                    },
                ],
            }
        ],
        "expected_log_markers": {"log.call": ["trunk=secondary", "playsbc_trunk_secondary_successes_total=1"]},
    },
    "esbc-header-normalization": {
        "caller": "enterprise-a",
        "callee": "header-policy-b",
        "register_callee": False,
        "route_policies": STATIC_TRUNK_ROUTE_POLICY,
        "header_normalization": {
            "remove": ["Subject"],
            "set": {"X-PlaySBC-Trunk": "{trunk}", "X-PlaySBC-Original-User": "{original_user}"},
        },
        "expected_log_markers": {"log.sip": ["HEADER NORMALIZATION", "X-PlaySBC-Original-User", "Subject"]},
    },
    "esbc-e164-normalization": {
        "caller": "enterprise-a",
        "callee": "0018005550100",
        "register_callee": False,
        "number_normalization": [
            {"name": "international-access-to-plus", "pattern": "^00", "replacement": "+"}
        ],
        "route_policies": [
            {
                "name": "normalized-e164",
                "match": "+1800*",
                "target": "sip:{user}@{host}:{uas_port}",
                "priority": 10,
            }
        ],
        "expected_log_markers": {"log.sip": ["NUMBER NORMALIZED", "normalized=+18005550100"]},
    },
    "esbc-hunt-group": {
        "caller": "enterprise-a",
        "callee": "support",
        "register_callee": False,
        "calls": 2,
        "rate": 1,
        "ladder": False,
        "route_policies": [
            {"name": "support-hunt", "match": "support", "target": "hunt-group:support", "priority": 10}
        ],
        "hunt_groups": [
            {
                "name": "support",
                "strategy": "round-robin",
                "members": [
                    {"name": "support-1", "uri": "sip:{user}@{host}:{uas_port}"},
                    {"name": "support-2", "uri": "sip:{user}@{host}:{uas_port}"},
                ],
            }
        ],
        "expected_log_markers": {
            "log.call": ["playsbc_trunk_support_1_attempts_total=1", "playsbc_trunk_support_2_attempts_total=1"]
        },
    },
    "esbc-call-admission": {
        "caller": "enterprise-a",
        "callee": "capacity-b",
        "register_callee": False,
        "start_uas": False,
        "uac_scenario": "b2bua_uac_failed_outbound.xml",
        "route_policies": STATIC_TRUNK_ROUTE_POLICY,
        "call_admission": {"enabled": True, "max_concurrent_calls": 0},
        "expected_log_markers": {"log.call": ["CALL ADMISSION REJECTED"]},
    },
    "esbc-trunk-metrics": {
        "caller": "enterprise-a",
        "callee": "metrics-b",
        "register_callee": False,
        "route_policies": [
            {"name": "metrics-trunk", "match": "*", "target": "trunk-group:metrics", "priority": 10}
        ],
        "trunk_groups": [
            {
                "name": "metrics",
                "members": [{"name": "carrier-a", "uri": "sip:{user}@{host}:{uas_port}"}],
            }
        ],
        "expected_log_markers": {
            "log.call": ["playsbc_trunk_carrier_a_attempts_total=1", "playsbc_trunk_carrier_a_successes_total=1"]
        },
    },
    "ha-shared-state-rtpengine": {
        "caller": "ha-core-a",
        "callee": "ha-peer-b",
        "media_backend": "rtpengine",
        "ha": {
            "enabled": True,
            "cluster_id": "playsbc-aa-lab",
            "node_id": "playsbc-a",
            "shared_state_path": "/tmp/playsbc-{run_id}-shared-state.sqlite3",
            "rtpengine_pairs": [
                {
                    "name": "core-pair-a",
                    "node_id": "playsbc-a",
                    "rtpengine_url": "{rtpengine_url}",
                },
                {
                    "name": "core-pair-b",
                    "node_id": "playsbc-b",
                    "rtpengine_url": "{rtpengine_url}",
                },
            ],
        },
        "expected_log_markers": {
            "log.platform": ["HA RTPENGINE PAIR SELECTED", "HA NODE STARTED", "HA REGISTRATION SYNC", "HA DIALOG SYNC"],
            "log.media": ["RTPENGINE OFFER", "RTPENGINE ANSWER"],
        },
    },
    "ha-options-health-recovery": {
        "caller": "ha-probe-a",
        "callee": "ha-probe-b",
        "register_callee": False,
        "start_uas": False,
        "run_call": False,
        "route_policies": [
            {"name": "ha-probed-trunk", "match": "*", "target": "trunk-group:ha-probe", "priority": 10}
        ],
        "trunk_groups": [
            {
                "name": "ha-probe",
                "members": [
                    {
                        "name": "recovering-peer",
                        "uri": "sip:options@{server_host}:{server_port}",
                        "state": "down",
                        "options_probe": {
                            "enabled": True,
                            "interval_seconds": 0.2,
                            "timeout_seconds": 0.5,
                            "failure_threshold": 1,
                            "recovery_successes": 1,
                        },
                    }
                ],
            }
        ],
        "ha": {
            "enabled": True,
            "cluster_id": "playsbc-aa-lab",
            "node_id": "playsbc-a",
            "shared_state_path": "/tmp/playsbc-{run_id}-probe-state.sqlite3",
        },
        "expected_log_markers": {
            "log.platform": ["TRUNK OPTIONS PROBING STARTED", "HA NODE STARTED"],
            "log.call": ["TRUNK OPTIONS PROBE", "trunk=recovering-peer", "health=up"],
        },
    },
    "ha-node-draining": {
        "caller": "ha-drain-a",
        "callee": "ha-drain-b",
        "register_callee": False,
        "start_uas": False,
        "uac_scenario": "b2bua_uac_failed_outbound.xml",
        "ha": {
            "enabled": True,
            "cluster_id": "playsbc-aa-lab",
            "node_id": "playsbc-a",
            "shared_state_path": "/tmp/playsbc-{run_id}-drain-state.sqlite3",
            "nodes": [
                {"node_id": "playsbc-a", "state": "draining", "weight": 0, "draining": True},
                {"node_id": "playsbc-b", "state": "active", "weight": 100},
            ],
            "load_balancing": {"enabled": True, "policy": "external-lb", "drain_new_calls": True},
        },
        "expected_log_markers": {
            "log.platform": ["HA NODE STARTED", "HA LOAD BALANCING MODEL", "HA NODE DRAINING"],
            "log.call": ["HA NODE DRAINING REJECT", "reason=draining"],
        },
    },
    "tls-transport-policy": {
        "caller": "tls-a",
        "callee": "tls-b",
        "sip_transport": "tls",
        "transport_policies": [{"name": "secure-peer", "match": "*", "transport": "tls"}],
        "expected_log_markers": {"log.tls": ["TLS CONNECTED", "TLS TX"]},
    },
    "tcp-connection-reuse": {
        "caller": "tcp-a",
        "callee": "tcp-b",
        "sip_transport": "tcp",
        "expected_log_markers": {"log.tcp": ["TCP CONNECTION REUSED"]},
    },
    "tcp-connection-failure": {
        "caller": "tcp-a",
        "callee": "unreachable-b",
        "sip_transport": "tcp",
        "register_callee": False,
        "start_uas": False,
        "uac_scenario": "b2bua_uac_expect_480.xml",
        "route_policies": [
            {
                "name": "unreachable-tcp-peer",
                "match": "*",
                "target": "sip:{user}@192.168.28.99:5060;transport=tcp",
                "priority": 10,
            }
        ],
        "expected_log_markers": {"log.networking": ["TCP TX FAILED"]},
    },
    "rtpengine-control-failure": {
        "caller": "fault-a",
        "callee": "fault-b",
        "media_backend": "rtpengine",
        "rtpengine_url": "udp://192.168.28.99:2223",
        "rtpengine_timeout": 0.2,
        "register_callee": False,
        "start_uas": False,
        "uac_scenario": "b2bua_uac_expect_488.xml",
        "route_policies": STATIC_TRUNK_ROUTE_POLICY,
        "expected_log_markers": {"log.media": ["RTPENGINE OFFER FAILED"]},
    },
    "rtpengine-port-exhaustion": {
        "caller": "fault-a",
        "callee": "fault-b",
        "media_backend": "rtpengine",
        "rtpengine_max_sessions": 0,
        "register_callee": False,
        "start_uas": False,
        "uac_scenario": "b2bua_uac_failed_outbound.xml",
        "route_policies": STATIC_TRUNK_ROUTE_POLICY,
        "expected_log_markers": {"log.media": ["RTPENGINE PORT POOL EXHAUSTED"]},
    },
    "rtpengine-interface-failure": {
        "caller": "fault-a",
        "callee": "fault-b",
        "media_backend": "rtpengine",
        "rtpengine_directions": ["missing-core", "missing-peer"],
        "register_callee": False,
        "start_uas": False,
        "uac_scenario": "b2bua_uac_expect_488.xml",
        "route_policies": STATIC_TRUNK_ROUTE_POLICY,
        "expected_log_markers": {
            "log.media": ["RTPENGINE INTERFACE UNAVAILABLE", "RTPENGINE OFFER FAILED"]
        },
    },
    "rtcp-receiver-quality": {
        "caller": "quality-a",
        "callee": "quality-b",
        "media_codec": "PCMU",
        "hold_ms": 10000,
        "rtcp_receiver_reports": True,
        "media_quality": {"loss_warn_percent": 1.0, "jitter_warn_ms": 30.0},
        "expected_log_markers": {"log.platform": ["RTCP RECEIVER QUALITY", "jitter_ms=1.000", "quality=good"]},
    },
    "tls-srtp-to-udp-rtp": {
        "caller": "secure-core-a",
        "callee": "plain-udp-b",
        "sip_transport": "udp,tls",
        "uac_transport": "tls",
        "uas_transport": "udp",
        "media_backend": "rtpengine",
        "media_codec": "PCMU",
        "hold_ms": 10000,
        "uac_srtp": True,
        "rtcp_enabled": False,
        "rtpengine_offer_transport_protocol": "RTP/AVP",
        "rtpengine_answer_transport_protocol": "RTP/SAVP",
        "rtpengine_sdes": RTPENGINE_SDES_AES_CM_128_ONLY,
        "rtpengine_dtls": "disable",
        "transport_policies": [{"name": "plain-udp-peer", "match": "*", "transport": "udp"}],
        "expected_log_markers": {
            "log.tls": ["TLS CONNECTED"],
            "log.sip": ["a=crypto:", "RTP/SAVP", "RTP/AVP"],
            "log.media": ["RTPENGINE MEDIA SECURITY", "offer_transport=RTP/AVP", "answer_transport=RTP/SAVP"],
        },
    },
    "tls-srtp-to-tcp-rtp": {
        "caller": "secure-core-a",
        "callee": "plain-tcp-b",
        "sip_transport": "tcp,tls",
        "uac_transport": "tls",
        "uas_transport": "tcp",
        "media_backend": "rtpengine",
        "media_codec": "PCMU",
        "hold_ms": 10000,
        "uac_srtp": True,
        "rtcp_enabled": False,
        "rtpengine_offer_transport_protocol": "RTP/AVP",
        "rtpengine_answer_transport_protocol": "RTP/SAVP",
        "rtpengine_sdes": RTPENGINE_SDES_AES_CM_128_ONLY,
        "rtpengine_dtls": "disable",
        "transport_policies": [{"name": "plain-tcp-peer", "match": "*", "transport": "tcp"}],
        "expected_log_markers": {
            "log.tls": ["TLS CONNECTED"],
            "log.tcp": ["TCP CONNECTED"],
            "log.sip": ["a=crypto:", "RTP/SAVP", "RTP/AVP"],
            "log.media": ["RTPENGINE MEDIA SECURITY", "offer_transport=RTP/AVP", "answer_transport=RTP/SAVP"],
        },
    },
    "udp-rtp-to-tls-srtp": {
        "caller": "plain-udp-a",
        "callee": "secure-peer-b",
        "sip_transport": "udp,tls",
        "uac_transport": "udp",
        "uas_transport": "tls",
        "media_backend": "rtpengine",
        "media_codec": "PCMU",
        "hold_ms": 10000,
        "uas_srtp": True,
        "rtcp_enabled": False,
        "rtpengine_offer_transport_protocol": "RTP/SAVP",
        "rtpengine_answer_transport_protocol": "RTP/AVP",
        "rtpengine_sdes": RTPENGINE_SDES_AES_CM_128_ONLY,
        "rtpengine_dtls": "disable",
        "transport_policies": [{"name": "secure-tls-peer", "match": "*", "transport": "tls"}],
        "expected_log_markers": {
            "log.tls": ["TLS CONNECTED"],
            "log.sip": ["RTP/SAVP", "RTP/AVP"],
            "log.media": ["RTPENGINE MEDIA SECURITY", "offer_transport=RTP/SAVP", "answer_transport=RTP/AVP"],
        },
    },
}
PROFILE_DESCRIPTIONS = {
    "basic-signalling": "One SIPp A -> B2BUA -> registered SIPp B call without RTP replay.",
    "basic-media": "One registered 60 second B2BUA call with PCMU RTP replay.",
    "transcoding": "One registered 60 second B2BUA media call with PCMU media and PCMA server codec preference.",
    "rtpengine": "One registered B2BUA signalling call using RTPengine as the media backend.",
    "rtpengine-media": "One registered 60 second B2BUA G.711u media call anchored by RTPengine.",
    "rtpengine-transcoding": "One registered 60 second B2BUA call with PCMU on A leg, PCMA on B leg, and RTPengine transcoding intent.",
    "tcp-rtpengine-transcoding": "One TCP REGISTER plus TCP B2BUA call with PCMU-to-PCMA transcoding and RTPengine media anchoring.",
    "registered-inbound": "Register SIPp B, then call that registered number through the B2BUA.",
    "registered-outbound": "Register SIPp A and SIPp B, then originate from the registered SIPp A user.",
    "register-auth-success": "Complete a REGISTER digest challenge, register SIPp B, then place a B2BUA call to it.",
    "register-auth-failure": "Attempt REGISTER digest authentication with a wrong password and require a second 401 response.",
    "dtmf-rfc4733": "Send and relay an RFC 4733 telephone-event during an established B2BUA media call.",
    "ai-rasa-lab": "Terminate one SIPp media call into the PlaySBC AI Voice Gateway and verify a Rasa REST turn.",
    "ai-rasa-rtpengine": "Terminate one SIPp media call into the PlaySBC AI Voice Gateway while RTPengine anchors RTP/RTCP and Rasa returns a bot action.",
    "invalid-bye": "Send a BYE outside any dialog and expect PlaySBC to reject it.",
    "unknown-route": "Call an unregistered user with unknown-route rejection enabled and expect 404.",
    "failed-outbound": "Register SIPp B, have the outbound leg reject INVITE, and verify PlaySBC propagates failure.",
    "cancel": "Cancel an in-progress B2BUA INVITE and verify CANCEL/487 handling across both legs.",
    "retransmission": "Replay the same inbound INVITE branch/CSeq and verify transaction cache behavior.",
    "small-load-2cps-10s": "Small B2BUA load profile at 2 cps with 10 second CHT.",
    "soak-1cps-30s": "Short soak-style B2BUA profile at 1 cps with 30 second CHT.",
    "load-5cps-60s": "Basic 5 cps for 60 seconds with 60 second CHT and ladder disabled.",
    "load-5cps-60s-rtpengine-transcoding": "5 cps for 60 seconds with 60 second CHT, RTPengine backend, and PCMU-to-PCMA transcoding intent.",
    "esbc-options-keepalive": "Enterprise SBC-style OPTIONS keepalive check against the PlaySBC SIP listener.",
    "esbc-static-trunk-route": "Enterprise SBC-style static trunk route from SIPp A through B2BUA to SIPp B without registrar lookup.",
    "esbc-e164-route-policy": "Enterprise SBC-style E.164 prefix route policy from SIPp A through B2BUA to SIPp B.",
    "esbc-trunk-failure": "Enterprise SBC-style trunk failure propagation when the outbound trunk returns 503.",
    "esbc-trunk-failover": "Route through a healthy secondary trunk when the primary trunk is administratively down.",
    "esbc-header-normalization": "Remove and add configured outbound SIP headers before sending the peer-leg INVITE.",
    "esbc-e164-normalization": "Normalize an international access prefix to E.164 before route-policy evaluation.",
    "esbc-hunt-group": "Distribute two calls across a round-robin hunt group and verify member counters.",
    "esbc-call-admission": "Reject a call with 503 when the configured concurrent-call admission limit is exhausted.",
    "esbc-trunk-metrics": "Complete one trunk-group call and verify per-trunk attempt and success counters.",
    "ha-shared-state-rtpengine": "Run an RTPengine-backed B2BUA call with HA shared registrar/dialog state and node-to-RTPengine pairing enabled.",
    "ha-options-health-recovery": "Start active OPTIONS probing against a down trunk and verify timed health recovery marks it up.",
    "ha-node-draining": "Mark the local PlaySBC node as draining and verify new INVITEs are rejected with 503 while the node stays alive.",
    "tls-transport-policy": "Register and complete a B2BUA call over TLS on both realms using a transport policy.",
    "tcp-connection-reuse": "Complete a TCP B2BUA call and verify PlaySBC reuses its peer transport connection.",
    "tcp-connection-failure": "Route to an unreachable TCP peer and verify transport failure plus 480 propagation.",
    "rtpengine-control-failure": "Use an unreachable RTPengine control endpoint and require a deterministic 488 response.",
    "rtpengine-port-exhaustion": "Set media-session capacity to zero and require deterministic 503 admission rejection.",
    "rtpengine-interface-failure": "Request missing RTPengine logical interfaces and require deterministic 488 failure.",
    "rtcp-receiver-quality": "Send RTCP receiver reports and validate parsed loss and jitter quality analytics.",
    "tls-srtp-to-udp-rtp": "Bridge a core TLS/SRTP leg to a peer SIP-over-UDP/RTP leg through RTPengine.",
    "tls-srtp-to-tcp-rtp": "Bridge a core TLS/SRTP leg to a peer SIP-over-TCP/RTP leg through RTPengine.",
    "udp-rtp-to-tls-srtp": "Bridge a core SIP-over-UDP/RTP leg to a peer TLS/SRTP leg through RTPengine.",
}


@dataclass
class SmokeResult:
    name: str
    command: List[str]
    returncode: Optional[int]
    status: str
    duration_seconds: float


@dataclass
class SippWireTraceEntry:
    timestamp: float
    order: int
    leg: str
    protocol: str
    direction: str
    source: str
    destination: str
    payload: bytes
    trace_name: str


@dataclass
class PcapPacket:
    timestamp: float
    src_ip: str
    src_port: int
    dst_ip: str
    dst_port: int
    payload: bytes
    protocol: str = "udp"


@dataclass(frozen=True)
class PcapFrameSpec:
    timestamp: float
    packet: PcapPacket
    tcp_sequence: int = 0
    tcp_acknowledgment: int = 0
    tcp_flags: int = 0


def make_run_id(prefix: str = "b2bua") -> str:
    return time.strftime(f"{prefix}-%Y%m%d-%H%M%S", time.localtime())


def resolve_binary(candidate: str) -> Optional[str]:
    if os.sep in candidate:
        return candidate if Path(candidate).exists() else None
    return shutil.which(candidate)


def call_limit(calls: int, rate: int, hold_ms: int) -> int:
    estimated_concurrent = int((rate * max(hold_ms, 1)) / 1000) + rate + 2
    return max(calls, estimated_concurrent, 3)


def sipp_timeout_seconds(calls: int, rate: int, hold_ms: int) -> int:
    safe_rate = max(rate, 1)
    traffic_seconds = (max(calls, 1) + safe_rate - 1) // safe_rate
    hold_seconds = max(hold_ms, 0) // 1000
    return max(30, traffic_seconds + hold_seconds + 60)


def resolve_scenario_path(value: str, fallback: Path) -> Path:
    if not value:
        return fallback
    path = Path(value)
    return path if path.is_absolute() else SCENARIO_DIR / path


def should_sudo_sipp_pcap(args: argparse.Namespace) -> bool:
    return bool(
        getattr(args, "sipp_pcap_sudo", False)
        and getattr(args, "media_enabled", False)
        and getattr(args, "media_driver", "") == "sipp-pcap"
    )


def should_capture_live_pcap(args: argparse.Namespace) -> bool:
    return bool(
        getattr(args, "sipp_pcap_sudo", False)
        and (is_load_like_run(args) or str(getattr(args, "sip_transport", "udp")).lower() == "tcp")
    )


def maybe_sudo_sipp_pcap(args: argparse.Namespace, command: List[str]) -> List[str]:
    if should_sudo_sipp_pcap(args):
        return ["sudo", "-n", *command]
    return command


def check_sudo_ready_for_sipp_pcap(args: argparse.Namespace) -> Tuple[bool, str]:
    if not (should_sudo_sipp_pcap(args) or should_capture_live_pcap(args)) or args.dry_run:
        return True, ""
    completed = subprocess.run(["sudo", "-n", "-v"], text=True, capture_output=True)
    detail = (completed.stderr.strip() or completed.stdout.strip() or f"returncode={completed.returncode}").strip()
    if completed.returncode == 0:
        return True, "sudo credentials are cached"
    return False, f"{SIPP_PCAP_SUDO_BLOCKED_DETAIL} sudo_check={detail}"


def check_rtpengine_preflight(url: str, timeout: float) -> Tuple[bool, str]:
    command = [
        sys.executable,
        str(ROOT / "tools" / "check_rtpengine.py"),
        "--url",
        url,
        "--timeout",
        str(timeout),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=max(timeout + 1.0, 2.0),
        )
    except subprocess.TimeoutExpired:
        return False, "tools/check_rtpengine.py timed out"

    detail = (completed.stdout.strip() or completed.stderr.strip() or f"returncode={completed.returncode}").strip()
    return completed.returncode == 0, detail


def is_load_like_run(args: argparse.Namespace) -> bool:
    return int(getattr(args, "calls", BASE_DEFAULTS["calls"])) > 1 or int(getattr(args, "rate", BASE_DEFAULTS["rate"])) > 1


def is_ai_gateway_profile(args: argparse.Namespace) -> bool:
    config = getattr(args, "ai_voice_gateway", {}) or {}
    return isinstance(config, dict) and bool(config.get("enabled"))


def sipp_trace_args(args: argparse.Namespace) -> List[str]:
    trace_args = ["-trace_err", "-trace_stat", "-trace_counts"]
    needs_rtpengine_rtcp_discovery = bool(
        is_load_like_run(args)
        and getattr(args, "media_enabled", False)
        and getattr(args, "media_backend", "internal") == "rtpengine"
    )
    if not is_load_like_run(args) or needs_rtpengine_rtcp_discovery:
        trace_args.extend(["-trace_msg", "-trace_logs"])
    return trace_args


def append_rtpengine_blocked_observations(log_dir: Path, args: argparse.Namespace, detail: str, duration: float) -> None:
    append_log_section(
        log_dir,
        "log.platform",
        "RTPENGINE PREFLIGHT BLOCKED",
        f"rtpengine_url={args.rtpengine_url}\nreason={detail}\nduration_seconds={duration:.3f}",
    )
    append_log_section(
        log_dir,
        "log.media",
        "MEDIA OBSERVATION",
        "\n".join(
            [
                f"expected_rtp={bool(args.media_enabled)} status=blocked",
                "media_backend=rtpengine",
                f"rtpengine_url={args.rtpengine_url}",
                f"reason={detail}",
            ]
        ),
    )
    transcoding_expected = bool(args.media_codec and args.server_codec and args.media_codec != args.server_codec)
    append_log_section(
        log_dir,
        "log.transcoding",
        "TRANSCODING OBSERVATION",
        "\n".join(
            [
                f"expected={transcoding_expected} status=blocked",
                "owner=rtpengine",
                f"reason={detail}",
            ]
        ),
    )


def append_sipp_pcap_sudo_blocked_observations(log_dir: Path, args: argparse.Namespace, detail: str, duration: float) -> None:
    append_log_section(
        log_dir,
        "log.platform",
        "SIPP PCAP SUDO PREFLIGHT BLOCKED",
        "\n".join(
            [
                f"reason={detail}",
                f"duration_seconds={duration:.3f}",
                "no_sipp_traffic_attempted=true",
                "next_step=run sudo -v in the same terminal before rerunning --sipp-pcap-sudo profiles",
            ]
        ),
    )
    append_log_section(
        log_dir,
        "log.media",
        "MEDIA OBSERVATION",
        "\n".join(
            [
                f"expected_rtp={bool(args.media_enabled)} status=blocked",
                f"media_backend={args.media_backend}",
                "reason=sipp_pcap_sudo_credentials_not_cached",
                "no_sipp_or_rtpengine_traffic_attempted=true",
            ]
        ),
    )
    transcoding_expected = bool(args.media_codec and args.server_codec and args.media_codec != args.server_codec)
    append_log_section(
        log_dir,
        "log.transcoding",
        "TRANSCODING OBSERVATION",
        "\n".join(
            [
                f"expected={transcoding_expected} status=blocked",
                f"owner={'rtpengine' if args.media_backend == 'rtpengine' else 'internal'}",
                "reason=sipp_pcap_sudo_credentials_not_cached",
            ]
        ),
    )


def is_transcoding_profile(args: argparse.Namespace) -> bool:
    media_codec = str(getattr(args, "media_codec", "") or "").upper()
    server_codec = str(getattr(args, "server_codec", "") or "").upper()
    return bool(media_codec and server_codec and media_codec != server_codec)


def uas_media_codec(args: argparse.Namespace) -> str:
    media_codec = str(getattr(args, "media_codec", "") or "PCMU").upper()
    server_codec = str(getattr(args, "server_codec", "") or media_codec).upper()
    return server_codec if is_transcoding_profile(args) else media_codec


def uac_sdp_payloads(args: argparse.Namespace) -> Tuple[str, str]:
    if is_transcoding_profile(args):
        codec = str(getattr(args, "media_codec", "") or "PCMU").upper()
        payload_type = MEDIA_PAYLOAD_TYPES[codec]
        return f"{payload_type} 101", MEDIA_RTPMAP_LINES[codec]
    return "0 8 101", "\n      ".join(MEDIA_RTPMAP_LINES[codec] for codec in ("PCMU", "PCMA"))


def uas_sdp_payloads(args: argparse.Namespace) -> Tuple[str, str]:
    if is_transcoding_profile(args):
        codec = uas_media_codec(args)
        payload_type = MEDIA_PAYLOAD_TYPES[codec]
        return f"{payload_type} 101", MEDIA_RTPMAP_LINES[codec]
    return "0 8 101", "\n      ".join(MEDIA_RTPMAP_LINES[codec] for codec in ("PCMU", "PCMA"))


def build_uas_command(args: argparse.Namespace, sipp_binary: str) -> List[str]:
    scenario = getattr(args, "uas_scenario", SCENARIO_DIR / ("b2bua_uas_b_media.xml" if args.media_enabled else "b2bua_uas_b.xml"))
    command = [
        sipp_binary,
        "-sf",
        str(scenario),
        "-s",
        args.callee,
        "-i",
        args.host,
        "-mi",
        args.host,
        "-p",
        str(args.uas_port),
        "-m",
        str(args.calls),
        "-l",
        str(call_limit(args.calls, args.rate, args.hold_ms)),
        "-timeout",
        str(sipp_timeout_seconds(args.calls, args.rate, args.hold_ms)),
        "-timeout_error",
        "-nostdin",
        "-min_rtp_port",
        str(args.uas_rtp_min),
        "-max_rtp_port",
        str(args.uas_rtp_max),
    ]
    command.extend(sipp_trace_args(args))
    command.extend(sipp_transport_args(args, role="server"))
    return maybe_sudo_sipp_pcap(args, command)


def build_uac_command(args: argparse.Namespace, sipp_binary: str) -> List[str]:
    scenario = getattr(args, "uac_scenario", SCENARIO_DIR / ("b2bua_uac_a_media.xml" if args.media_enabled else "b2bua_uac_a.xml"))
    command = [
        sipp_binary,
        f"{args.host}:{args.server_port}",
        "-sf",
        str(scenario),
        "-s",
        args.callee,
        "-key",
        "caller",
        getattr(args, "caller", "sipp-a"),
        "-i",
        args.host,
        "-mi",
        args.host,
        "-p",
        str(args.uac_port),
        "-m",
        str(args.calls),
        "-r",
        str(args.rate),
        "-d",
        str(args.hold_ms),
        "-l",
        str(call_limit(args.calls, args.rate, args.hold_ms)),
        "-timeout",
        str(sipp_timeout_seconds(args.calls, args.rate, args.hold_ms)),
        "-timeout_error",
        "-nostdin",
        "-min_rtp_port",
        str(args.uac_rtp_min),
        "-max_rtp_port",
        str(args.uac_rtp_max),
    ]
    command.extend(sipp_trace_args(args))
    command.extend(sipp_transport_args(args))
    return maybe_sudo_sipp_pcap(args, command)


def sipp_transport_args(args: argparse.Namespace, role: str = "client") -> List[str]:
    transport = str(getattr(args, "sip_transport", "udp")).lower()
    if transport == "tls":
        return ["-t", "l1"] if role == "server" else ["-t", "ln"]
    if transport != "tcp":
        return []
    if role == "server":
        return ["-t", "t1"]
    return ["-t", "tn", "-max_socket", str(sipp_max_socket_limit(args))]


def sipp_max_socket_limit(args: argparse.Namespace) -> int:
    calls = int(getattr(args, "calls", BASE_DEFAULTS["calls"]))
    rate = int(getattr(args, "rate", BASE_DEFAULTS["rate"]))
    hold_ms = int(getattr(args, "hold_ms", BASE_DEFAULTS["hold_ms"]))
    return min(max(call_limit(calls, rate, hold_ms) + 16, 128), 1024)


def build_server_command(args: argparse.Namespace, work_dir: Path, log_dir: Path) -> List[str]:
    config_path = write_dynamic_config(args, work_dir, log_dir)
    return [
        sys.executable,
        str(ROOT / "mini_call_server.py"),
        "--config",
        str(config_path),
        "--debug",
    ]


def render_harness_config_templates(value: object, args: argparse.Namespace) -> object:
    if isinstance(value, dict):
        return {key: render_harness_config_templates(item, args) for key, item in value.items()}
    if isinstance(value, list):
        return [render_harness_config_templates(item, args) for item in value]
    if isinstance(value, str):
        server_host = str(getattr(args, "server_host", "") or getattr(args, "host", BASE_DEFAULTS["host"]))
        return (
            value.replace("{host}", str(getattr(args, "host", BASE_DEFAULTS["host"])))
            .replace("{server_host}", server_host)
            .replace("{server_port}", str(getattr(args, "server_port", BASE_DEFAULTS["server_port"])))
            .replace("{uas_port}", str(getattr(args, "uas_port", BASE_DEFAULTS["uas_port"])))
            .replace("{uac_port}", str(getattr(args, "uac_port", BASE_DEFAULTS["uac_port"])))
            .replace("{rtpengine_url}", str(getattr(args, "rtpengine_url", BASE_DEFAULTS["rtpengine_url"])))
            .replace("{run_id}", str(getattr(args, "resolved_run_id", getattr(args, "run_id", ""))))
        )
    return value


def effective_route_policies(args: argparse.Namespace) -> List[dict]:
    policies = getattr(args, "route_policies", None) or DEFAULT_ROUTE_POLICIES
    rendered = render_harness_config_templates(policies, args)
    return rendered if isinstance(rendered, list) else DEFAULT_ROUTE_POLICIES


def effective_b2bua_routes(args: argparse.Namespace) -> dict:
    routes = getattr(args, "b2bua_routes", None) or {}
    rendered = render_harness_config_templates(routes, args)
    return rendered if isinstance(rendered, dict) else {}


def write_dynamic_config(args: argparse.Namespace, work_dir: Path, log_dir: Path) -> Path:
    config = {
        "sip_ip": args.host,
        "sip_port": args.server_port,
        "tls_port": args.server_port,
        "sip_transport": args.sip_transport,
        "rtp_min": args.server_rtp_min,
        "rtp_max": args.server_rtp_max,
        "log_dir": str(log_dir),
        "default_codec": args.server_codec,
        "auth_realm": "playsbc",
        "users": getattr(args, "users", {}),
        "bridge_rooms": ["bridge"],
        "b2bua_routes": effective_b2bua_routes(args),
        "route_policies": effective_route_policies(args),
        "trunk_groups": render_harness_config_templates(getattr(args, "trunk_groups", []), args),
        "hunt_groups": render_harness_config_templates(getattr(args, "hunt_groups", []), args),
        "number_normalization": getattr(args, "number_normalization", []),
        "header_normalization": getattr(args, "header_normalization", {}),
        "transport_policies": getattr(args, "transport_policies", []),
        "call_admission": getattr(args, "call_admission", {}),
        "b2bua_ladder_logs": args.ladder_enabled,
        "media_backend": args.media_backend,
        "rtpengine_url": args.rtpengine_url,
        "rtpengine_timeout": args.rtpengine_timeout,
        "rtpengine_directions": getattr(args, "rtpengine_directions", []),
        "rtpengine_interfaces": getattr(args, "rtpengine_interfaces", []),
        "rtpengine_max_sessions": getattr(args, "rtpengine_max_sessions", -1),
        "rtpengine_offer_transport_protocol": getattr(args, "rtpengine_offer_transport_protocol", ""),
        "rtpengine_answer_transport_protocol": getattr(args, "rtpengine_answer_transport_protocol", ""),
        "rtpengine_sdes": getattr(args, "rtpengine_sdes", []),
        "rtpengine_dtls": getattr(args, "rtpengine_dtls", ""),
        "media_quality": getattr(args, "media_quality", {}),
        "ai_voice_gateway": getattr(args, "ai_voice_gateway", {}),
        "ha": render_harness_config_templates(getattr(args, "ha", {}), args),
        "reject_unknown_routes": args.reject_unknown_routes,
        "tls_certfile": getattr(args, "tls_certfile", ""),
        "tls_keyfile": getattr(args, "tls_keyfile", ""),
        "tls_cafile": getattr(args, "tls_cafile", ""),
        "tls_verify_peer": getattr(args, "tls_verify_peer", False),
        "debug": True,
    }
    values_path = work_dir / "helm-values.yaml"
    values_path.write_text(dump_simple_yaml({"playsbc": {"config": config}}), encoding="utf-8")
    helm = resolve_binary(args.helm_bin)
    if not helm:
        raise SystemExit(
            "Helm executable not found. Install Helm and retry; PlaySBC regression uses Helm-rendered YAML config."
        )
    rendered = subprocess.run(
        [
            helm,
            "template",
            "playsbc",
            str(ROOT / "charts" / "playsbc"),
            "-f",
            str(values_path),
            "--show-only",
            "templates/configmap.yaml",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if rendered.returncode != 0:
        raise SystemExit(f"Helm config render failed:\n{rendered.stderr.strip() or rendered.stdout.strip()}")
    config_path = work_dir / "server-config.yaml"
    config_path.write_text(extract_helm_server_yaml(rendered.stdout), encoding="utf-8")
    return config_path


def extract_helm_server_yaml(rendered: str) -> str:
    lines = rendered.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != "server.yaml: |":
            continue
        content_indent: Optional[int] = None
        collected: List[str] = []
        for content_line in lines[index + 1 :]:
            if not content_line.strip():
                collected.append("")
                continue
            indent = len(content_line) - len(content_line.lstrip(" "))
            if content_indent is None:
                content_indent = indent
            if indent < content_indent:
                break
            collected.append(content_line[content_indent:])
        if not collected:
            raise SystemExit("Helm ConfigMap did not include server.yaml content")
        return "\n".join(collected).rstrip() + "\n"
    raise SystemExit("Helm ConfigMap did not include server.yaml")


def dump_simple_yaml(value: object, indent: int = 0) -> str:
    lines = list(iter_simple_yaml_lines(value, indent))
    return "\n".join(lines) + "\n"


def iter_simple_yaml_lines(value: object, indent: int = 0):
    prefix = " " * indent
    if isinstance(value, dict):
        for key, item in value.items():
            if item == {}:
                yield f"{prefix}{key}: {{}}"
                continue
            if item == []:
                yield f"{prefix}{key}: []"
                continue
            if isinstance(item, (dict, list)):
                yield f"{prefix}{key}:"
                yield from iter_simple_yaml_lines(item, indent + 2)
            else:
                yield f"{prefix}{key}: {format_simple_yaml_scalar(item)}"
        return
    if isinstance(value, list):
        for item in value:
            if item == {}:
                yield f"{prefix}- {{}}"
                continue
            if item == []:
                yield f"{prefix}- []"
                continue
            if isinstance(item, dict):
                yield f"{prefix}-"
                yield from iter_simple_yaml_lines(item, indent + 2)
            elif isinstance(item, list):
                yield f"{prefix}-"
                yield from iter_simple_yaml_lines(item, indent + 2)
            else:
                yield f"{prefix}- {format_simple_yaml_scalar(item)}"
        return
    yield f"{prefix}{format_simple_yaml_scalar(value)}"


def format_simple_yaml_scalar(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text == "":
        return '""'
    if re.fullmatch(r"[A-Za-z0-9_./:@+-]+", text) and text.lower() not in {"true", "false", "null"}:
        return text
    return json.dumps(text)


def prepare_media_scenarios(args: argparse.Namespace, run_dir: Path) -> None:
    if not args.media_enabled:
        args.uac_scenario = resolve_scenario_path(args.uac_scenario, SCENARIO_DIR / "b2bua_uac_a.xml")
        args.uas_scenario = resolve_scenario_path(args.uas_scenario, SCENARIO_DIR / "b2bua_uas_b.xml")
        args.media_pcap_resolved = ""
        return

    pcap_path = Path(args.media_pcap)
    if not pcap_path.is_absolute():
        pcap_path = SCENARIO_DIR / pcap_path
    if not pcap_path.exists():
        raise SystemExit(f"Media PCAP not found: {pcap_path}")
    args.media_pcap_resolved = str(pcap_path)
    uas_pcap_path = media_pcap_for_codec(uas_media_codec(args), pcap_path)
    args.uas_media_pcap_resolved = str(uas_pcap_path)

    if args.media_driver == "python":
        args.uac_scenario = SCENARIO_DIR / "b2bua_uac_a.xml"
        args.uas_scenario = SCENARIO_DIR / "b2bua_uas_b.xml"
        return

    replacements = {
        "uac_scenario": (SCENARIO_DIR / "b2bua_uac_a_media.xml", run_dir / "sipp-a-uac" / "b2bua_uac_a_media_resolved.xml"),
        "uas_scenario": (SCENARIO_DIR / "b2bua_uas_b_media.xml", run_dir / "sipp-b-uas" / "b2bua_uas_b_media_resolved.xml"),
    }
    for attr_name, (template, destination) in replacements.items():
        scenario_pcap = uas_pcap_path if attr_name == "uas_scenario" else pcap_path
        text = template.read_text(encoding="ISO-8859-1").replace("[media_pcap]", str(scenario_pcap))
        if attr_name == "uac_scenario":
            uac_payloads, uac_rtpmaps = uac_sdp_payloads(args)
            text = text.replace("[uac_sdp_payloads]", uac_payloads)
            text = text.replace("[uac_sdp_rtpmaps]", uac_rtpmaps)
            if str(getattr(args, "uac_transport", "") or getattr(args, "sip_transport", "udp")).lower() in {"tcp", "tls"}:
                text = re.sub(
                    r"(?m)^(\s+(?:ACK|BYE) sip:\[service\]@\[remote_ip\]:\[remote_port\]) SIP/2\.0$",
                    r"\1;transport=[transport] SIP/2.0",
                    text,
                )
        if attr_name == "uas_scenario":
            uas_payloads, uas_rtpmaps = uas_sdp_payloads(args)
            text = text.replace("[uas_sdp_payloads]", uas_payloads)
            text = text.replace("[uas_sdp_rtpmaps]", uas_rtpmaps)
        secure_leg = bool(
            getattr(args, "uac_srtp", False) if attr_name == "uac_scenario" else getattr(args, "uas_srtp", False)
        )
        if secure_leg:
            text = text.replace("[media_port]", "[rtpstream_audio_port]")
            text = text.replace(" RTP/AVP ", " RTP/SAVP ")
            text = text.replace(
                "      a=ptime:20",
                (
                    "      a=crypto:[cryptotag1audio] "
                    "[cryptosuiteaescm128sha1801audio] inline:[cryptokeyparams1audio]\n"
                    "      a=rtcp:[rtpstream_audio_port+1]\n"
                    "      a=ptime:20"
                ),
            )
            # SIPp's SRTP echo path selects the negotiated remote SDES key for
            # transmit. rtp_stream uses the locally offered key, which causes
            # RTPengine to reject the packets as authentication failures.
            secure_action = '<exec rtp_echo="startaudio,0,PCMU/8000"/>'
            text = re.sub(
                r"\n\s*<nop>\s*<action>\s*<exec play_pcap_audio=\"[^\"]+\"/>\s*</action>\s*</nop>\s*",
                (
                    "\n  <nop>\n"
                    "    <action>\n"
                    f"      {secure_action}\n"
                    "    </action>\n"
                    "  </nop>\n\n"
                ),
                text,
            )
        destination.write_text(text, encoding="ISO-8859-1")
        setattr(args, attr_name, destination.resolve())


def prepare_registration_scenario(args: argparse.Namespace, run_dir: Path) -> None:
    if not str(getattr(args, "registration_auth_expected", "") or ""):
        return
    source = resolve_scenario_path(
        str(getattr(args, "registration_scenario", "register_contact.xml")),
        SCENARIO_DIR / "register_contact.xml",
    )
    text = source.read_text(encoding="ISO-8859-1")
    username = str(getattr(args, "registration_username", "") or args.callee)
    password = str(getattr(args, "registration_password", ""))
    text = text.replace("__AUTH_USERNAME__", username).replace("__AUTH_PASSWORD__", password)
    destination = run_dir / "registration-callee" / f"{source.stem}_resolved.xml"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="ISO-8859-1")
    args.registration_scenario = str(destination.resolve())


def prepare_transport_scenario(args: argparse.Namespace, run_dir: Path) -> None:
    if str(getattr(args, "uac_transport", "") or getattr(args, "sip_transport", "udp")).lower() not in {"tcp", "tls"}:
        return
    source = Path(args.uac_scenario)
    text = source.read_text(encoding="ISO-8859-1")

    def add_transport(match: re.Match[str]) -> str:
        request_uri = match.group(1)
        if ";transport=" in request_uri.lower():
            return match.group(0)
        return f"{request_uri};transport=[transport] SIP/2.0"

    resolved = re.sub(
        r"(?m)^(\s+(?:ACK|BYE)\s+sip:\S+)\s+SIP/2\.0$",
        add_transport,
        text,
    )
    destination = run_dir / "sipp-a-uac" / f"{source.stem}_transport_resolved.xml"
    destination.write_text(resolved, encoding="ISO-8859-1")
    args.uac_scenario = destination.resolve()


def build_media_player_commands(args: argparse.Namespace) -> List[Tuple[str, List[str]]]:
    if not args.media_enabled or args.media_driver != "python":
        return []

    player = str(ROOT / "tools" / "play_g711_pcap_rtp.py")
    base = [
        sys.executable,
        player,
        "--pcap",
        args.media_pcap_resolved,
        "--host",
        args.host,
        "--duration-ms",
        str(args.hold_ms),
    ]
    return [
        ("media-a-to-b2bua", base + ["--port", str(args.server_rtp_min)]),
        ("media-b-to-b2bua", base + ["--port", str(args.server_rtp_min + 2)]),
    ]


def should_run_rtcp(args: argparse.Namespace) -> bool:
    single_call = args.calls == 1 and args.rate == 1
    load_canary = str(getattr(args, "profile", "")) == "load-5cps-60s-rtpengine-transcoding"
    return bool(
        getattr(args, "rtcp_enabled", True)
        and args.media_enabled
        and args.hold_ms >= 5000
        and (single_call or load_canary)
    )


def should_expect_rtcp_reply(args: argparse.Namespace) -> bool:
    return not (is_ai_gateway_profile(args) and getattr(args, "media_backend", "") == "rtpengine")


def rtcp_expected_sender_names(args: argparse.Namespace) -> Tuple[str, ...]:
    if not bool(getattr(args, "start_uas", True)):
        return ("rtcp-a",)
    return ("rtcp-a", "rtcp-b")


def wait_for_rtcp_anchor_ports(work_dir: Path, args: argparse.Namespace, timeout: float = 8.0) -> Tuple[int, int]:
    if args.media_backend != "rtpengine":
        return int(args.server_rtp_min) + 1, int(args.server_rtp_min) + 3
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        a_rtp, b_rtp = rtpengine_anchor_ports(work_dir)
        if a_rtp and "rtcp-b" not in rtcp_expected_sender_names(args):
            return a_rtp + 1, a_rtp + 1
        if a_rtp and b_rtp:
            return a_rtp + 1, b_rtp + 1
        time.sleep(0.050)
    raise RuntimeError("Could not discover both RTPengine RTCP anchor ports from SIPp traces")


def build_rtcp_sender_commands(args: argparse.Namespace, work_dir: Path) -> List[Tuple[str, List[str]]]:
    if not should_run_rtcp(args):
        return []
    a_target_port, b_target_port = wait_for_rtcp_anchor_ports(work_dir, args)
    duration = max(1.0, (args.hold_ms / 1000.0) - args.media_start_delay)
    sender = str(ROOT / "tools" / "send_rtcp_reports.py")

    def command(source_port: int, target_port: int, ssrc: str, cname: str) -> List[str]:
        values = [
            sys.executable,
            sender,
            "--local-ip",
            args.host,
            "--source-port",
            str(source_port),
            "--target-ip",
            args.host,
            "--target-port",
            str(target_port),
            "--ssrc",
            ssrc,
            "--cname",
            cname,
            "--duration-seconds",
            f"{duration:.3f}",
            "--interval-seconds",
            "5",
        ]
        if should_expect_rtcp_reply(args):
            values.append("--expect-reply")
        return values

    commands = [("rtcp-a", command(args.uac_rtp_min + 1, a_target_port, "0xC0DEC0DE", "sipp-a@playsbc"))]
    if "rtcp-b" in rtcp_expected_sender_names(args):
        commands.append(("rtcp-b", command(args.uas_rtp_min + 1, b_target_port, "0xC0DEC0DE", "sipp-b@playsbc")))
    return commands


def build_register_command(
    args: argparse.Namespace,
    sipp_binary: str,
    user: str,
    contact_port: int,
    local_port: Optional[int] = None,
) -> List[str]:
    bind_port = contact_port if local_port is None else local_port
    scenario_name = str(getattr(args, "registration_scenario", "register_contact.xml") or "register_contact.xml")
    scenario = resolve_scenario_path(scenario_name, SCENARIO_DIR / "register_contact.xml")
    command = [
        sipp_binary,
        f"{args.host}:{args.server_port}",
        "-sf",
        str(scenario),
        "-s",
        user,
        "-i",
        args.host,
        "-mi",
        args.host,
        "-p",
        str(bind_port),
        "-key",
        "contact_port",
        str(contact_port),
        "-m",
        "1",
        "-r",
        "1",
        "-timeout",
        "10",
        "-timeout_error",
        "-nostdin",
        "-trace_err",
        "-trace_msg",
        "-trace_stat",
        "-trace_counts",
        "-trace_logs",
    ]
    command.extend(sipp_transport_args(args))
    return command


def start_process(command: List[str], cwd: Path, stdout_path: Path) -> subprocess.Popen:
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout = stdout_path.open("w", encoding="utf-8")
    process = subprocess.Popen(command, cwd=cwd, stdout=stdout, stderr=subprocess.STDOUT)
    process.stdout_file = stdout  # type: ignore[attr-defined]
    return process


def stop_process(process: Optional[subprocess.Popen]) -> None:
    if not process:
        return
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    stdout = getattr(process, "stdout_file", None)
    if stdout:
        stdout.close()


def rtpengine_control_port(url: str) -> int:
    match = re.search(r":(\d+)$", str(url or ""))
    return int(match.group(1)) if match else 2223


def live_capture_commands(args: argparse.Namespace, work_dir: Path) -> List[Tuple[str, List[str]]]:
    if not should_capture_live_pcap(args):
        return []
    tcpdump = resolve_binary("tcpdump")
    if not tcpdump:
        raise RuntimeError("tcpdump is required for live TCP/load regression evidence")
    interface = "lo0" if sys.platform == "darwin" else "lo"
    base = ["sudo", "-n", tcpdump, "-i", interface, "-U", "-n", "-s", "0"]
    sip_port = str(args.server_port)
    control_port = str(rtpengine_control_port(args.rtpengine_url))

    if not is_load_like_run(args):
        path = work_dir / "live-full.pcap"
        packet_filter = [
            "(", "tcp", "port", sip_port, ")", "or",
            "(", "udp", "port", control_port, ")", "or",
            "(", "udp", "portrange", f"{min(args.server_rtp_min, args.uas_rtp_min)}-{max(args.uac_rtp_max, args.uas_rtp_max)}", ")",
        ]
        return [("live-pcap", [*base, "-w", str(path), *packet_filter])]

    control_path = work_dir / "live-control.pcap"
    control_filter = ["udp", "port", sip_port]
    if args.media_backend == "rtpengine":
        control_filter.extend(
            [
                "or", "udp", "port", control_port,
                "or", "udp", "port", str(args.uac_rtp_min + 1),
                "or", "udp", "port", str(args.uas_rtp_min + 1),
            ]
        )
    commands = [("live-pcap-control", [*base, "-w", str(control_path), *control_filter])]
    if args.media_enabled:
        media_path = work_dir / "live-media.pcap"
        media_filter = [
            "udp", "portrange", f"{min(args.server_rtp_min, args.uas_rtp_min)}-{max(args.uac_rtp_max, args.uas_rtp_max)}",
            "and", "not", "udp", "port", sip_port,
            "and", "not", "udp", "port", control_port,
            "and", "not", "udp", "port", str(args.uac_rtp_min + 1),
            "and", "not", "udp", "port", str(args.uas_rtp_min + 1),
        ]
        commands.append(
            ("live-pcap-media-ring", [*base, "-C", "8", "-W", "4", "-w", str(media_path), *media_filter])
        )
    return commands


def start_live_captures(
    args: argparse.Namespace,
    work_dir: Path,
) -> List[Tuple[str, List[str], subprocess.Popen]]:
    captures = []
    for name, command in live_capture_commands(args, work_dir):
        process = start_process(command, ROOT, work_dir / f"{name}.log")
        captures.append((name, command, process))
    if captures:
        time.sleep(0.300)
        failed = [name for name, _command, process in captures if process.poll() is not None]
        if failed:
            for _name, _command, process in captures:
                stop_process(process)
            raise RuntimeError(f"Live packet capture exited early: {', '.join(failed)}")
    return captures


def stop_live_captures(captures: List[Tuple[str, List[str], subprocess.Popen]]) -> None:
    for _name, _command, process in captures:
        stop_process(process)


def live_capture_files(work_dir: Path) -> List[Path]:
    paths = []
    for pattern in ("live-full.pcap*", "live-control.pcap*", "live-media.pcap*"):
        paths.extend(path for path in work_dir.glob(pattern) if path.is_file() and path.stat().st_size > 24)
    return sorted(set(paths))


def pcap_file_records(path: Path) -> Tuple[bytes, int, List[Tuple[float, bytes]]]:
    data = path.read_bytes()
    if len(data) < 24:
        raise ValueError(f"PCAP header is missing: {path}")
    magic = data[:4]
    formats = {
        b"\xd4\xc3\xb2\xa1": ("<", 1_000_000),
        b"\xa1\xb2\xc3\xd4": (">", 1_000_000),
        b"\x4d\x3c\xb2\xa1": ("<", 1_000_000_000),
        b"\xa1\xb2\x3c\x4d": (">", 1_000_000_000),
    }
    if magic not in formats:
        raise ValueError(f"Unsupported PCAP byte order: {path}")
    endian, precision = formats[magic]
    linktype = struct.unpack(f"{endian}I", data[20:24])[0]
    records = []
    offset = 24
    while offset + 16 <= len(data):
        ts_sec, ts_fraction, included_len, _original_len = struct.unpack(f"{endian}IIII", data[offset : offset + 16])
        offset += 16
        frame = data[offset : offset + included_len]
        if len(frame) != included_len:
            raise ValueError(f"Truncated PCAP frame: {path}")
        offset += included_len
        records.append((ts_sec + (ts_fraction / precision), frame))
    return data[:24], linktype, records


def merge_live_capture_files(paths: List[Path], destination: Path) -> int:
    headers = []
    records = []
    linktype = None
    for path in paths:
        header, current_linktype, current_records = pcap_file_records(path)
        if linktype is not None and current_linktype != linktype:
            raise ValueError("Live capture segments use different link-layer types")
        linktype = current_linktype
        headers.append(header)
        records.extend(current_records)
    if not headers:
        return 0
    records.sort(key=lambda record: record[0])
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as output:
        output.write(struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, int(linktype or 0)))
        for timestamp, frame in records:
            seconds = int(timestamp)
            microseconds = int(round((timestamp - seconds) * 1_000_000))
            if microseconds >= 1_000_000:
                seconds += 1
                microseconds -= 1_000_000
            output.write(struct.pack("<IIII", seconds, microseconds, len(frame), len(frame)))
            output.write(frame)
    return len(records)


def initialize_log_dir(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    for filename in LOG_FILES:
        path = log_dir / filename
        if not path.exists():
            path.write_text(f"{timestamp} | LOG START | file={filename}\n", encoding="utf-8")


def append_log_section(log_dir: Path, filename: str, title: str, body: str = "") -> None:
    initialize_log_dir(log_dir)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    with (log_dir / filename).open("a", encoding="utf-8") as log_file:
        log_file.write(f"{timestamp} | {title}\n")
        if body:
            log_file.write(body.rstrip() + "\n")


def append_file_section(log_dir: Path, filename: str, title: str, path: Path) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    if text.strip():
        append_log_section(log_dir, filename, title, text)


def append_commands(log_dir: Path, commands: List[Tuple[str, List[str]]]) -> None:
    lines = []
    for name, command in commands:
        lines.append(f"{name}: {shlex.join(command)}")
    append_log_section(log_dir, "log.sipp", "B2BUA SIPP COMMANDS", "\n".join(lines))


def checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    total = (total >> 16) + (total & 0xFFFF)
    total += total >> 16
    return (~total) & 0xFFFF


def ethernet_ipv4_udp_packet(packet: PcapPacket, packet_id: int) -> bytes:
    payload = packet.payload
    src_ip = socket.inet_aton(packet.src_ip)
    dst_ip = socket.inet_aton(packet.dst_ip)
    udp_length = 8 + len(payload)
    total_length = 20 + udp_length
    ip_header = struct.pack("!BBHHHBBH4s4s", 0x45, 0, total_length, packet_id & 0xFFFF, 0, 64, 17, 0, src_ip, dst_ip)
    ip_header = ip_header[:10] + struct.pack("!H", checksum(ip_header)) + ip_header[12:]
    udp_header = struct.pack("!HHHH", packet.src_port & 0xFFFF, packet.dst_port & 0xFFFF, udp_length, 0)
    ethernet_header = b"\x02\x00\x00\x00\x00\x02" + b"\x02\x00\x00\x00\x00\x01" + struct.pack("!H", 0x0800)
    return ethernet_header + ip_header + udp_header + payload


def tcp_checksum(src_ip: bytes, dst_ip: bytes, tcp_segment: bytes) -> int:
    pseudo_header = src_ip + dst_ip + struct.pack("!BBH", 0, 6, len(tcp_segment))
    return checksum(pseudo_header + tcp_segment)


def ethernet_ipv4_tcp_packet(packet: PcapPacket, packet_id: int, seq: int, ack: int, flags: int = 0x18) -> bytes:
    payload = packet.payload
    src_ip = socket.inet_aton(packet.src_ip)
    dst_ip = socket.inet_aton(packet.dst_ip)
    tcp_offset_words = 5
    tcp_header = struct.pack(
        "!HHIIBBHHH",
        packet.src_port & 0xFFFF,
        packet.dst_port & 0xFFFF,
        seq & 0xFFFFFFFF,
        ack & 0xFFFFFFFF,
        tcp_offset_words << 4,
        flags,
        65535,
        0,
        0,
    )
    tcp_header = tcp_header[:16] + struct.pack("!H", tcp_checksum(src_ip, dst_ip, tcp_header + payload)) + tcp_header[18:]
    total_length = 20 + len(tcp_header) + len(payload)
    ip_header = struct.pack("!BBHHHBBH4s4s", 0x45, 0, total_length, packet_id & 0xFFFF, 0, 64, 6, 0, src_ip, dst_ip)
    ip_header = ip_header[:10] + struct.pack("!H", checksum(ip_header)) + ip_header[12:]
    ethernet_header = b"\x02\x00\x00\x00\x00\x02" + b"\x02\x00\x00\x00\x00\x01" + struct.pack("!H", 0x0800)
    return ethernet_header + ip_header + tcp_header + payload


def tcp_connection_frame_specs(packets: List[PcapPacket]) -> List[PcapFrameSpec]:
    groups: dict[Tuple[Tuple[str, int], Tuple[str, int]], List[PcapPacket]] = {}
    for packet in packets:
        source = (packet.src_ip, packet.src_port)
        destination = (packet.dst_ip, packet.dst_port)
        key = tuple(sorted((source, destination)))
        groups.setdefault(key, []).append(packet)

    frames: List[PcapFrameSpec] = []
    for flow_index, flow_packets in enumerate(
        sorted(groups.values(), key=lambda values: min(packet.timestamp for packet in values)),
        start=1,
    ):
        ordered = sorted(flow_packets, key=lambda packet: packet.timestamp)
        first = ordered[0]
        first_source = (first.src_ip, first.src_port)
        first_destination = (first.dst_ip, first.dst_port)
        if first.payload.startswith(b"SIP/2.0 "):
            initiator, responder = first_destination, first_source
        else:
            initiator, responder = first_source, first_destination
        initiator_isn = 1_000_000 + (flow_index * 1_000_000)
        responder_isn = initiator_isn + 500_000
        next_sequence = {initiator: initiator_isn + 1, responder: responder_isn + 1}

        def control(timestamp: float, source: Tuple[str, int], destination: Tuple[str, int], seq: int, ack: int, flags: int) -> None:
            frames.append(
                PcapFrameSpec(
                    timestamp,
                    PcapPacket(timestamp, source[0], source[1], destination[0], destination[1], b"", protocol="tcp"),
                    seq,
                    ack,
                    flags,
                )
            )

        start = first.timestamp
        control(start - 0.003, initiator, responder, initiator_isn, 0, 0x02)
        control(start - 0.002, responder, initiator, responder_isn, initiator_isn + 1, 0x12)
        control(start - 0.001, initiator, responder, initiator_isn + 1, responder_isn + 1, 0x10)

        last_timestamp = start
        for packet in ordered:
            source = (packet.src_ip, packet.src_port)
            destination = (packet.dst_ip, packet.dst_port)
            sequence = next_sequence[source]
            acknowledgment = next_sequence[destination]
            frames.append(PcapFrameSpec(packet.timestamp, packet, sequence, acknowledgment, 0x18))
            next_sequence[source] += len(packet.payload)
            control(
                packet.timestamp + 0.000001,
                destination,
                source,
                next_sequence[destination],
                next_sequence[source],
                0x10,
            )
            last_timestamp = max(last_timestamp, packet.timestamp)

        control(last_timestamp + 0.001, initiator, responder, next_sequence[initiator], next_sequence[responder], 0x11)
        next_sequence[initiator] += 1
        control(last_timestamp + 0.002, responder, initiator, next_sequence[responder], next_sequence[initiator], 0x10)
        control(last_timestamp + 0.003, responder, initiator, next_sequence[responder], next_sequence[initiator], 0x11)
        next_sequence[responder] += 1
        control(last_timestamp + 0.004, initiator, responder, next_sequence[initiator], next_sequence[responder], 0x10)
    return frames


def write_udp_pcap(path: Path, packets: List[PcapPacket]) -> dict[str, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    udp_packets = [packet for packet in packets if packet.protocol.lower() != "tcp"]
    tcp_packets = [packet for packet in packets if packet.protocol.lower() == "tcp"]
    frames = [PcapFrameSpec(packet.timestamp, packet) for packet in udp_packets]
    frames.extend(tcp_connection_frame_specs(tcp_packets))
    with path.open("wb") as fh:
        fh.write(struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))
        for index, spec in enumerate(sorted(frames, key=lambda item: item.timestamp), start=1):
            packet = spec.packet
            if packet.protocol.lower() == "tcp":
                frame = ethernet_ipv4_tcp_packet(
                    packet,
                    index,
                    spec.tcp_sequence,
                    spec.tcp_acknowledgment,
                    spec.tcp_flags,
                )
            else:
                frame = ethernet_ipv4_udp_packet(packet, index)
            timestamp_seconds = int(spec.timestamp)
            timestamp_microseconds = int((spec.timestamp - timestamp_seconds) * 1_000_000)
            fh.write(struct.pack("<IIII", timestamp_seconds, timestamp_microseconds, len(frame), len(frame)))
            fh.write(frame)
    return {
        "udp_packets": len(udp_packets),
        "tcp_packets": len(frames) - len(udp_packets),
        "packet_count": len(frames),
    }


def extract_rtp_payload(frame: bytes) -> bytes:
    if len(frame) < 14:
        return b""
    ether_type = struct.unpack("!H", frame[12:14])[0]
    if ether_type != 0x0800:
        return b""

    ip_offset = 14
    if len(frame) < ip_offset + 20:
        return b""
    version_ihl = frame[ip_offset]
    if version_ihl >> 4 != 4:
        return b""
    ihl = (version_ihl & 0x0F) * 4
    if frame[ip_offset + 9] != 17:
        return b""

    udp_offset = ip_offset + ihl
    rtp_offset = udp_offset + 8
    if len(frame) < rtp_offset + 12:
        return b""
    return frame[rtp_offset:]


def rtp_packets_from_pcap(path: Path, max_seconds: float) -> List[Tuple[float, bytes]]:
    if not path.exists():
        return []

    data = path.read_bytes()
    if len(data) < 24:
        return []

    magic = data[:4]
    if magic == b"\xd4\xc3\xb2\xa1":
        endian = "<"
    elif magic == b"\xa1\xb2\xc3\xd4":
        endian = ">"
    else:
        return []

    packets = []
    first_timestamp: Optional[float] = None
    offset = 24
    while offset + 16 <= len(data):
        ts_sec, ts_usec, included_len, _original_len = struct.unpack(f"{endian}IIII", data[offset : offset + 16])
        offset += 16
        frame = data[offset : offset + included_len]
        offset += included_len
        rtp = extract_rtp_payload(frame)
        if not rtp:
            continue

        timestamp = ts_sec + (ts_usec / 1_000_000)
        if first_timestamp is None:
            first_timestamp = timestamp
        relative_timestamp = timestamp - first_timestamp
        if max_seconds > 0 and relative_timestamp > max_seconds:
            break
        packets.append((relative_timestamp, rtp))
    return packets


def media_pcap_for_codec(codec: str, fallback: Path) -> Path:
    relative = MEDIA_PCAPS.get(codec.upper())
    if not relative:
        return fallback
    path = SCENARIO_DIR / relative
    return path if path.exists() else fallback


def media_capture_start_timestamp(log_dir: Path) -> float:
    media_log = log_dir / "log.media"
    if not media_log.exists():
        return time.time()
    for line in media_log.read_text(encoding="utf-8", errors="replace").splitlines():
        if "RTP PACKET RX" in line or "B2BUA ANSWERED" in line:
            return parse_log_timestamp(line)
    return time.time()


def is_invite_ack_payload(payload: bytes) -> bool:
    start_line = payload.split(b"\r\n", 1)[0].upper()
    if not start_line.startswith(b"ACK "):
        return False
    return re.search(rb"(?im)^CSeq\s*:\s*\d+\s+ACK\s*$", payload) is not None


def sip_ack_media_start_timestamp(work_dir: Path) -> Optional[float]:
    ack_timestamps = []
    for leg in ("sipp-a-uac", "sipp-b-uas"):
        for trace in sorted((work_dir / leg).glob("*_messages.log")):
            for timestamp, _direction, payload in sipp_trace_messages(trace):
                if is_invite_ack_payload(payload):
                    ack_timestamps.append(timestamp)
    if not ack_timestamps:
        return None
    return max(ack_timestamps) + 0.001


def with_rtp_payload_type(rtp: bytes, codec: str) -> bytes:
    payload_type = {"PCMU": 0, "PCMA": 8}.get(codec.upper())
    if payload_type is None or len(rtp) < 2:
        return rtp
    if rtp[1] & 0x7F == 101:
        return rtp
    rewritten = bytearray(rtp)
    rewritten[1] = (rewritten[1] & 0x80) | payload_type
    return bytes(rewritten)


def with_rtp_stream_identity(rtp: bytes, codec: str, sequence: int, timestamp: int, ssrc: int) -> bytes:
    rewritten = bytearray(with_rtp_payload_type(rtp, codec))
    if len(rewritten) < 12:
        return bytes(rewritten)
    rewritten[2:4] = struct.pack("!H", sequence & 0xFFFF)
    rewritten[4:8] = struct.pack("!I", timestamp & 0xFFFFFFFF)
    rewritten[8:12] = struct.pack("!I", ssrc & 0xFFFFFFFF)
    return bytes(rewritten)


def sdp_audio_port(payload: bytes) -> Optional[int]:
    match = re.search(rb"(?im)^m=audio\s+(\d+)\s+RTP/AVP\b", payload)
    if not match:
        return None
    port = int(match.group(1))
    return port if 0 < port <= 65535 else None


def rtpengine_anchor_ports(work_dir: Path) -> Tuple[Optional[int], Optional[int]]:
    a_leg_port = None
    b_leg_port = None
    for trace in sorted((work_dir / "sipp-a-uac").glob("*_messages.log")):
        for _timestamp, direction, payload in sipp_trace_messages(trace):
            if a_leg_port is None and direction == "received" and payload.startswith(b"SIP/2.0 200"):
                a_leg_port = sdp_audio_port(payload)
    for trace in sorted((work_dir / "sipp-b-uas").glob("*_messages.log")):
        for _timestamp, direction, payload in sipp_trace_messages(trace):
            if b_leg_port is None and direction == "received" and payload.startswith(b"INVITE "):
                b_leg_port = sdp_audio_port(payload)
    return a_leg_port, b_leg_port


def rtpengine_anchor_port_set(work_dir: Path) -> Tuple[int, ...]:
    return tuple(port for port in rtpengine_anchor_ports(work_dir) if port is not None)


def rtp_media_packets(log_dir: Path, work_dir: Path, args: argparse.Namespace) -> List[PcapPacket]:
    if not getattr(args, "media_enabled", False):
        return []

    media_backend = str(getattr(args, "media_backend", BASE_DEFAULTS["media_backend"]))
    if media_backend != "rtpengine" and total_logged_rtp_packets(log_dir) <= 0:
        return []
    if media_backend == "rtpengine":
        media_text = (log_dir / "log.media").read_text(encoding="utf-8", errors="replace") if (log_dir / "log.media").exists() else ""
        if "RTPENGINE ANSWER" not in media_text:
            return []

    media_pcap = Path(str(getattr(args, "media_pcap_resolved", "") or ""))
    if not media_pcap.exists():
        return []

    media_codec = str(getattr(args, "media_codec", "") or "PCMU").upper()
    server_codec = str(getattr(args, "server_codec", "") or media_codec).upper()
    b_leg_codec = uas_media_codec(args)
    max_seconds = max(float(getattr(args, "hold_ms", 0) or 0) / 1000.0, 0.0) + 0.100
    endpoint_rtp = rtp_packets_from_pcap(media_pcap, max_seconds)
    if not endpoint_rtp:
        return []

    rtp_by_codec = {media_codec: endpoint_rtp}
    a_anchor_port = int(getattr(args, "server_rtp_min", BASE_DEFAULTS["server_rtp_min"]))
    b_anchor_port = a_anchor_port + 2
    if media_backend == "rtpengine":
        parsed_a_anchor, parsed_b_anchor = rtpengine_anchor_ports(work_dir)
        a_anchor_port = parsed_a_anchor or a_anchor_port
        b_anchor_port = parsed_b_anchor or b_anchor_port

    def samples_for_codec(codec: str) -> List[Tuple[float, bytes]]:
        normalized_codec = codec.upper()
        if normalized_codec not in rtp_by_codec:
            codec_samples = rtp_packets_from_pcap(media_pcap_for_codec(normalized_codec, media_pcap), max_seconds)
            rtp_by_codec[normalized_codec] = codec_samples or endpoint_rtp
        return rtp_by_codec[normalized_codec]

    uac_ip, server_ip, uas_ip = pcap_topology_ips(args)
    media_anchor_ip = pcap_rtpengine_ip(args) if media_backend == "rtpengine" else server_ip
    endpoint_streams = [
        (media_codec, uac_ip, int(getattr(args, "uac_rtp_min", BASE_DEFAULTS["uac_rtp_min"])), media_anchor_ip, a_anchor_port, 0xA10A0001, 1000, 16000),
        (b_leg_codec, uas_ip, int(getattr(args, "uas_rtp_min", BASE_DEFAULTS["uas_rtp_min"])), media_anchor_ip, b_anchor_port, 0xB10B0002, 3000, 48000),
    ]
    server_streams = [
        (media_codec, media_anchor_ip, a_anchor_port, uac_ip, int(getattr(args, "uac_rtp_min", BASE_DEFAULTS["uac_rtp_min"])), 0xC10C0003, 5000, 80000),
        (server_codec, media_anchor_ip, b_anchor_port, uas_ip, int(getattr(args, "uas_rtp_min", BASE_DEFAULTS["uas_rtp_min"])), 0xD10D0004, 7000, 112000),
    ]
    if is_ai_gateway_profile(args):
        endpoint_streams = [
            (
                media_codec,
                uac_ip,
                int(getattr(args, "uac_rtp_min", BASE_DEFAULTS["uac_rtp_min"])),
                media_anchor_ip,
                a_anchor_port,
                0xA10A0001,
                1000,
                16000,
            )
        ]
        server_streams = []

    base_time = sip_ack_media_start_timestamp(work_dir)
    if base_time is None:
        base_time = media_capture_start_timestamp(log_dir)

    packets = []
    for stream_codec, src_ip, src_port, dst_ip, dst_port, ssrc, sequence_base, timestamp_base in endpoint_streams:
        samples = samples_for_codec(stream_codec)
        source_timestamp_base = struct.unpack("!I", samples[0][1][4:8])[0]
        for index, (relative_timestamp, rtp) in enumerate(samples):
            source_timestamp = struct.unpack("!I", rtp[4:8])[0]
            translated_timestamp = timestamp_base + ((source_timestamp - source_timestamp_base) & 0xFFFFFFFF)
            payload = with_rtp_stream_identity(rtp, stream_codec, sequence_base + index, translated_timestamp, ssrc)
            packets.append(PcapPacket(base_time + relative_timestamp, src_ip, src_port, dst_ip, dst_port, payload))
    for stream_codec, src_ip, src_port, dst_ip, dst_port, ssrc, sequence_base, timestamp_base in server_streams:
        samples = samples_for_codec(stream_codec)
        source_timestamp_base = struct.unpack("!I", samples[0][1][4:8])[0]
        for index, (relative_timestamp, rtp) in enumerate(samples):
            source_timestamp = struct.unpack("!I", rtp[4:8])[0]
            translated_timestamp = timestamp_base + ((source_timestamp - source_timestamp_base) & 0xFFFFFFFF)
            payload = with_rtp_stream_identity(rtp, stream_codec, sequence_base + index, translated_timestamp, ssrc)
            packets.append(PcapPacket(base_time + relative_timestamp, src_ip, src_port, dst_ip, dst_port, payload))
    return packets


def rtcp_media_packets(rtp_packets: List[PcapPacket], interval_seconds: float = 5.0) -> List[PcapPacket]:
    flows: dict[Tuple[str, int, str, int, int], List[PcapPacket]] = {}
    for packet in rtp_packets:
        if len(packet.payload) < 12 or packet.payload[0] >> 6 != 2:
            continue
        ssrc = struct.unpack("!I", packet.payload[8:12])[0]
        key = (packet.src_ip, packet.src_port, packet.dst_ip, packet.dst_port, ssrc)
        flows.setdefault(key, []).append(packet)

    reports = []
    for (src_ip, src_port, dst_ip, dst_port, ssrc), packets in flows.items():
        ordered = sorted(packets, key=lambda packet: packet.timestamp)
        started = ordered[0].timestamp
        ended = ordered[-1].timestamp
        report_time = started + interval_seconds
        while report_time <= ended:
            media_packets = min(int((report_time - started) * 50), len(ordered))
            report = build_compound_sender_report(
                ssrc=ssrc,
                cname=f"{src_ip}:{src_port}",
                rtp_timestamp=media_packets * 160,
                packet_count=media_packets,
                octet_count=media_packets * 160,
                now=report_time,
            )
            reports.append(
                PcapPacket(
                    report_time,
                    src_ip,
                    src_port + 1,
                    dst_ip,
                    dst_port + 1,
                    report,
                )
            )
            report_time += interval_seconds
    return reports


def parse_iso_timestamp(value: str) -> float:
    value = value.strip().removesuffix("Z")
    for timestamp_format in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(value, timestamp_format).timestamp()
        except ValueError:
            continue
    return time.time()


def parse_log_timestamp(line: str) -> float:
    try:
        return datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        return time.time()


def sipp_trace_protocol_messages(path: Path) -> List[Tuple[float, str, str, bytes]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    pattern = re.compile(
        r"^-{10,}\s+([0-9T :.\-]+Z?)\n"
        r"(UDP|TCP|TLS) message (sent|received) "
        r"(?:\[\d+\]\s*bytes|\(\d+\s*bytes\))\s*:\n\n",
        re.MULTILINE,
    )
    matches = list(pattern.finditer(text))
    messages = []
    for index, match in enumerate(matches):
        payload_start = match.end()
        payload_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        payload = normalize_sip_payload(text[payload_start:payload_end])
        if not payload:
            continue
        messages.append((parse_iso_timestamp(match.group(1)), match.group(2).lower(), match.group(3), payload))
    return messages


def sipp_trace_messages(path: Path) -> List[Tuple[float, str, bytes]]:
    return [(timestamp, direction, payload) for timestamp, _protocol, direction, payload in sipp_trace_protocol_messages(path)]


def sip_start_line(payload: bytes) -> str:
    return payload.split(b"\r\n", 1)[0].decode("utf-8", errors="replace")


def sip_payload_text(payload: bytes) -> str:
    return payload.decode("utf-8", errors="replace").replace("\r\n", "\n").rstrip()


def sipp_trace_leg_labels(leg: str, direction: str) -> Tuple[str, str]:
    return SIP_TRACE_LEG_LABELS.get(leg, {}).get(direction, (leg, "PlaySBC"))


def sipp_wire_trace_entries(work_dir: Path) -> List[SippWireTraceEntry]:
    entries: List[SippWireTraceEntry] = []
    sequence = 0
    for leg in ("registration-caller", "sipp-a-uac", "sipp-b-uas", "registration-callee"):
        leg_dir = work_dir / leg
        for trace in sorted(leg_dir.glob("*_messages.log")):
            for timestamp, protocol, direction, payload in sipp_trace_protocol_messages(trace):
                source, destination = sipp_trace_leg_labels(leg, direction)
                entries.append(
                    SippWireTraceEntry(
                        timestamp=timestamp,
                        order=(SIP_TRACE_LEG_ORDER.get(leg, 100) * 100000) + sequence,
                        leg=leg,
                        protocol=protocol,
                        direction=direction,
                        source=source,
                        destination=destination,
                        payload=payload,
                        trace_name=trace.name,
                    )
                )
                sequence += 1
    return sorted(entries, key=lambda entry: (entry.timestamp, entry.order))


def ordered_sip_trace_text(work_dir: Path) -> str:
    entries = sipp_wire_trace_entries(work_dir)
    if not entries:
        return ""

    lines = [
        "direction_order=CORE SIPp A <-> PlaySBC CORE <-> PlaySBC PEER <-> PEER SIPp B",
        f"message_count={len(entries)}",
    ]
    for index, entry in enumerate(entries, start=1):
        timestamp = datetime.fromtimestamp(entry.timestamp).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        lines.extend(
            [
                "",
                (
                    f"{index:02d} {timestamp} | {entry.protocol.upper()} | "
                    f"{entry.source} -> {entry.destination} | {sip_start_line(entry.payload)} | "
                    f"trace={entry.leg}/{entry.trace_name}"
                ),
            ]
        )
        lines.extend(f"    {line}" if line else "" for line in sip_payload_text(entry.payload).splitlines())
    return "\n".join(lines)


def append_ordered_sip_trace(log_dir: Path, work_dir: Path, args: argparse.Namespace) -> None:
    if is_load_like_run(args):
        return
    trace = ordered_sip_trace_text(work_dir)
    if trace:
        append_log_section(log_dir, "log.sip", "ORDERED SIP MESSAGE TRACE CORE TO PEER", trace)


def normalize_sip_payload(payload_text: str) -> bytes:
    normalized = payload_text.replace("\r\n", "\n").strip("\n")
    if not normalized.strip():
        return b""

    if "\n\n" in normalized:
        headers, body = normalized.split("\n\n", 1)
        body = body.rstrip("\n")
    else:
        headers, body = normalized, ""

    body_bytes = body.replace("\n", "\r\n").encode("utf-8")
    if re.search(r"(?im)^Content-Length\s*:", headers):
        headers = re.sub(
            r"(?im)^Content-Length\s*:\s*\d+",
            f"Content-Length: {len(body_bytes)}",
            headers,
            count=1,
        )
    header_bytes = headers.replace("\n", "\r\n").encode("utf-8")
    return header_bytes + b"\r\n\r\n" + body_bytes


def sipp_leg_port(args: argparse.Namespace, leg: str) -> Optional[int]:
    ports = {
        "registration-callee": getattr(args, "register_port", BASE_DEFAULTS["register_port"]),
        "registration-caller": getattr(args, "caller_register_port", BASE_DEFAULTS["caller_register_port"]),
        "sipp-a-uac": getattr(args, "uac_port", BASE_DEFAULTS["uac_port"]),
        "sipp-b-uas": getattr(args, "uas_port", BASE_DEFAULTS["uas_port"]),
    }
    return ports.get(leg)


def pcap_topology_ips(args: argparse.Namespace) -> Tuple[str, str, str]:
    if getattr(args, "pcap_topology", BASE_DEFAULTS["pcap_topology"]) == "runtime":
        host = getattr(args, "host", BASE_DEFAULTS["host"])
        return host, host, host
    return (
        getattr(args, "pcap_uac_ip", BASE_DEFAULTS["pcap_uac_ip"]),
        getattr(args, "pcap_server_ip", BASE_DEFAULTS["pcap_server_ip"]),
        getattr(args, "pcap_uas_ip", BASE_DEFAULTS["pcap_uas_ip"]),
    )


def pcap_rtpengine_ip(args: argparse.Namespace) -> str:
    if getattr(args, "pcap_topology", BASE_DEFAULTS["pcap_topology"]) == "runtime":
        return getattr(args, "host", BASE_DEFAULTS["host"])
    return getattr(args, "pcap_rtpengine_ip", BASE_DEFAULTS["pcap_rtpengine_ip"])


def pcap_leg_ip(args: argparse.Namespace, leg: str) -> str:
    uac_ip, server_ip, uas_ip = pcap_topology_ips(args)
    leg_ips = {
        "registration-callee": uas_ip,
        "registration-caller": uac_ip,
        "sipp-a-uac": uac_ip,
        "sipp-b-uas": uas_ip,
    }
    return leg_ips.get(leg, server_ip)


def pcap_endpoint_ip(args: argparse.Namespace, endpoint: Optional[Tuple[str, int]]) -> str:
    uac_ip, server_ip, uas_ip = pcap_topology_ips(args)
    if not endpoint:
        return server_ip

    _host, port = endpoint
    uac_port = int(getattr(args, "uac_port", BASE_DEFAULTS["uac_port"]))
    uas_port = int(getattr(args, "uas_port", BASE_DEFAULTS["uas_port"]))
    register_port = int(getattr(args, "register_port", BASE_DEFAULTS["register_port"]))
    caller_register_port = int(getattr(args, "caller_register_port", BASE_DEFAULTS["caller_register_port"]))
    server_port = int(getattr(args, "server_port", BASE_DEFAULTS["server_port"]))
    server_rtp_min = int(getattr(args, "server_rtp_min", BASE_DEFAULTS["server_rtp_min"]))
    server_rtp_max = int(getattr(args, "server_rtp_max", BASE_DEFAULTS["server_rtp_max"]))
    uac_rtp_min = int(getattr(args, "uac_rtp_min", BASE_DEFAULTS["uac_rtp_min"]))
    uac_rtp_max = int(getattr(args, "uac_rtp_max", BASE_DEFAULTS["uac_rtp_max"]))
    uas_rtp_min = int(getattr(args, "uas_rtp_min", BASE_DEFAULTS["uas_rtp_min"]))
    uas_rtp_max = int(getattr(args, "uas_rtp_max", BASE_DEFAULTS["uas_rtp_max"]))

    if port in {uac_port, caller_register_port}:
        return uac_ip
    if port in {uas_port, register_port}:
        return uas_ip
    if uac_rtp_min <= port <= uac_rtp_max:
        return uac_ip
    if uas_rtp_min <= port <= uas_rtp_max:
        return uas_ip
    if port == server_port or server_rtp_min <= port <= server_rtp_max:
        return server_ip
    if getattr(args, "pcap_topology", BASE_DEFAULTS["pcap_topology"]) == "runtime":
        return endpoint[0]
    return server_ip


def pcap_media_ip_for_port(args: argparse.Namespace, port: int, rtpengine_ports: Tuple[int, ...] = ()) -> Optional[str]:
    uac_ip, server_ip, uas_ip = pcap_topology_ips(args)
    server_rtp_min = int(getattr(args, "server_rtp_min", BASE_DEFAULTS["server_rtp_min"]))
    server_rtp_max = int(getattr(args, "server_rtp_max", BASE_DEFAULTS["server_rtp_max"]))
    uac_rtp_min = int(getattr(args, "uac_rtp_min", BASE_DEFAULTS["uac_rtp_min"]))
    uac_rtp_max = int(getattr(args, "uac_rtp_max", BASE_DEFAULTS["uac_rtp_max"]))
    uas_rtp_min = int(getattr(args, "uas_rtp_min", BASE_DEFAULTS["uas_rtp_min"]))
    uas_rtp_max = int(getattr(args, "uas_rtp_max", BASE_DEFAULTS["uas_rtp_max"]))

    if port in rtpengine_ports:
        return pcap_rtpengine_ip(args)
    if uac_rtp_min <= port <= uac_rtp_max:
        return uac_ip
    if uas_rtp_min <= port <= uas_rtp_max:
        return uas_ip
    if server_rtp_min <= port <= server_rtp_max:
        return server_ip
    return None


def rewrite_sdp_topology_ip(body: str, args: argparse.Namespace, rtpengine_ports: Tuple[int, ...] = ()) -> str:
    media_match = re.search(r"(?m)^m=audio\s+(\d+)\s+RTP/AVP\b", body)
    if not media_match:
        return body

    media_ip = pcap_media_ip_for_port(args, int(media_match.group(1)), rtpengine_ports=rtpengine_ports)
    if not media_ip:
        return body

    body = re.sub(r"(?m)^c=IN\s+IP4\s+\S+", f"c=IN IP4 {media_ip}", body)
    return re.sub(
        r"(?m)^(o=\S+\s+\S+\s+\S+\s+IN\s+IP4\s+)\S+",
        lambda match: f"{match.group(1)}{media_ip}",
        body,
    )


def sip_topology_host_port_replacements(args: argparse.Namespace) -> List[Tuple[str, str]]:
    if getattr(args, "pcap_topology", BASE_DEFAULTS["pcap_topology"]) == "runtime":
        return []

    runtime_host = getattr(args, "host", BASE_DEFAULTS["host"])
    uac_ip, server_ip, uas_ip = pcap_topology_ips(args)
    port_ips = {
        int(getattr(args, "uac_port", BASE_DEFAULTS["uac_port"])): uac_ip,
        int(getattr(args, "caller_register_port", BASE_DEFAULTS["caller_register_port"])): uac_ip,
        int(getattr(args, "uas_port", BASE_DEFAULTS["uas_port"])): uas_ip,
        int(getattr(args, "register_port", BASE_DEFAULTS["register_port"])): uas_ip,
        int(getattr(args, "server_port", BASE_DEFAULTS["server_port"])): server_ip,
        int(getattr(args, "server_rtp_min", BASE_DEFAULTS["server_rtp_min"])): server_ip,
        int(getattr(args, "server_rtp_min", BASE_DEFAULTS["server_rtp_min"])) + 2: server_ip,
        int(getattr(args, "uac_rtp_min", BASE_DEFAULTS["uac_rtp_min"])): uac_ip,
        int(getattr(args, "uas_rtp_min", BASE_DEFAULTS["uas_rtp_min"])): uas_ip,
    }
    return [(f"{runtime_host}:{port}", f"{logical_ip}:{port}") for port, logical_ip in sorted(port_ips.items())]


def rewrite_sip_headers_topology(headers: str, args: argparse.Namespace) -> str:
    rewritten = headers
    for runtime_endpoint, logical_endpoint in sip_topology_host_port_replacements(args):
        rewritten = rewritten.replace(runtime_endpoint, logical_endpoint)
    return rewritten


def logical_identity_ip_for_sip_message(args: argparse.Namespace, src_port: int, dst_port: int) -> str:
    uac_ip, server_ip, uas_ip = pcap_topology_ips(args)
    uac_ports = {
        int(getattr(args, "uac_port", BASE_DEFAULTS["uac_port"])),
        int(getattr(args, "caller_register_port", BASE_DEFAULTS["caller_register_port"])),
    }
    uas_ports = {
        int(getattr(args, "uas_port", BASE_DEFAULTS["uas_port"])),
        int(getattr(args, "register_port", BASE_DEFAULTS["register_port"])),
    }
    if src_port in uas_ports or dst_port in uas_ports:
        return uas_ip
    if src_port in uac_ports or dst_port in uac_ports:
        return uac_ip
    return server_ip


def rewrite_bare_sip_identity_hosts(headers: str, args: argparse.Namespace, src_port: int, dst_port: int) -> str:
    if getattr(args, "pcap_topology", BASE_DEFAULTS["pcap_topology"]) == "runtime":
        return headers

    runtime_host = re.escape(str(getattr(args, "host", BASE_DEFAULTS["host"])))
    logical_ip = logical_identity_ip_for_sip_message(args, src_port, dst_port)
    bare_host_pattern = re.compile(rf"@{runtime_host}(?=([>;,\s]|$))")
    rewritten_lines = []
    for line in headers.split(CRLF):
        if line.lower().startswith("call-id:"):
            rewritten_lines.append(line)
        else:
            rewritten_lines.append(bare_host_pattern.sub(f"@{logical_ip}", line))
    return CRLF.join(rewritten_lines)


def rewrite_sip_payload_for_pcap(
    payload: bytes,
    args: argparse.Namespace,
    src_port: int,
    dst_port: int,
    rtpengine_ports: Tuple[int, ...] = (),
) -> bytes:
    separator = b"\r\n\r\n"
    if separator not in payload:
        return payload

    headers_bytes, body_bytes = payload.split(separator, 1)
    headers = headers_bytes.decode("utf-8", errors="replace")
    body = body_bytes.decode("utf-8", errors="replace")
    rewritten_headers = rewrite_sip_headers_topology(headers, args)
    rewritten_headers = rewrite_bare_sip_identity_hosts(rewritten_headers, args, src_port, dst_port)
    rewritten_body = rewrite_sdp_topology_ip(body, args, rtpengine_ports=rtpengine_ports) if "m=audio" in body else body
    if rewritten_headers == headers and rewritten_body == body:
        return payload

    rewritten_body_bytes = rewritten_body.encode("utf-8")
    if re.search(r"(?im)^Content-Length\s*:", rewritten_headers):
        rewritten_headers = re.sub(
            r"(?im)^Content-Length\s*:\s*\d+",
            f"Content-Length: {len(rewritten_body_bytes)}",
            rewritten_headers,
            count=1,
        )
    return rewritten_headers.encode("utf-8") + separator + rewritten_body_bytes


def sipp_trace_packets(work_dir: Path, args: argparse.Namespace) -> List[PcapPacket]:
    packets = []
    _uac_ip, server_ip, _uas_ip = pcap_topology_ips(args)
    rtpengine_ports = rtpengine_anchor_port_set(work_dir)
    for leg in ("registration-callee", "registration-caller", "sipp-a-uac", "sipp-b-uas"):
        local_port = sipp_leg_port(args, leg)
        if local_port is None:
            continue
        local_ip = pcap_leg_ip(args, leg)
        for trace in sorted((work_dir / leg).glob("*_messages.log")):
            for timestamp, protocol, direction, payload in sipp_trace_protocol_messages(trace):
                if direction == "sent":
                    src_port, dst_port = local_port, args.server_port
                    src_ip, dst_ip = local_ip, server_ip
                else:
                    src_port, dst_port = args.server_port, local_port
                    src_ip, dst_ip = server_ip, local_ip
                payload = rewrite_sip_payload_for_pcap(payload, args, src_port, dst_port, rtpengine_ports=rtpengine_ports)
                packets.append(PcapPacket(timestamp, src_ip, src_port, dst_ip, dst_port, payload, protocol=protocol))
    return packets


def normalized_sip_uri(value: bytes) -> str:
    text = value.decode("utf-8", errors="replace").strip().strip("<>")
    return text.lower()


def dialog_remote_target_errors(work_dir: Path) -> List[str]:
    errors = []
    for trace in sorted((work_dir / "sipp-a-uac").glob("*_messages.log")):
        remote_target = ""
        for _timestamp, _protocol, direction, payload in sipp_trace_protocol_messages(trace):
            start_line = payload.split(b"\r\n", 1)[0]
            if direction == "received" and payload.startswith(b"SIP/2.0 200"):
                if re.search(rb"(?im)^CSeq\s*:\s*\d+\s+INVITE\s*$", payload):
                    match = re.search(rb"(?im)^Contact\s*:\s*<?([^>\r\n]+)>?\s*$", payload)
                    if match:
                        remote_target = normalized_sip_uri(match.group(1))
                continue
            if direction != "sent" or not start_line.startswith((b"ACK ", b"BYE ")) or not remote_target:
                continue
            parts = start_line.split()
            request_uri = normalized_sip_uri(parts[1]) if len(parts) >= 2 else ""
            if request_uri != remote_target:
                errors.append(f"{start_line.decode('utf-8', errors='replace')} does not use Contact {remote_target}")
    return errors


def parse_endpoint(text: str, key: str) -> Optional[Tuple[str, int]]:
    match = re.search(rf"{key}=([0-9.]+):(\d+)", text)
    if not match:
        return None
    return match.group(1), int(match.group(2))


def protocol_event_packets(log_dir: Path, args: argparse.Namespace) -> List[PcapPacket]:
    packets = []
    _uac_ip, server_ip, _uas_ip = pcap_topology_ips(args)
    for filename in ("log.udp", "log.networking"):
        path = log_dir / filename
        if not path.exists():
            continue
        protocol = "udp"
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "LOG START" in line or not line.strip():
                continue
            timestamp = parse_log_timestamp(line)
            source = parse_endpoint(line, "source")
            destination = parse_endpoint(line, "destination")
            local = parse_endpoint(line, "local")
            if " RX " in line and source:
                src_ip = pcap_endpoint_ip(args, source)
                dst_ip = server_ip
            elif " TX " in line and destination:
                src_ip = server_ip
                dst_ip = pcap_endpoint_ip(args, destination)
            elif local:
                src_ip = pcap_endpoint_ip(args, local)
                dst_ip = src_ip
            else:
                src_ip = dst_ip = server_ip
            payload = f"PlaySBC diagnostic event | {line}\n".encode("utf-8")
            packets.append(PcapPacket(timestamp, src_ip, DIAGNOSTIC_PCAP_PORT, dst_ip, DIAGNOSTIC_PCAP_PORT, payload, protocol=protocol))
    return packets


def should_generate_pcap_artifacts(args: argparse.Namespace) -> bool:
    profile = str(getattr(args, "profile", "") or "")
    if profile.startswith("load-"):
        return False
    return args.calls == 1 and args.rate == 1


def generate_pcap_artifacts(log_dir: Path, work_dir: Path, args: argparse.Namespace) -> List[Path]:
    if args.dry_run:
        return []

    live_files = live_capture_files(work_dir)
    if live_files:
        pcap_path = log_dir / "capture.pcap"
        packet_count = merge_live_capture_files(live_files, pcap_path)
        append_log_section(
            log_dir,
            "log.platform",
            "PCAP GENERATION",
            "\n".join(
                [
                    "source=live_tcpdump",
                    "scope=runtime_wire_evidence",
                    "file=capture.pcap",
                    f"packet_count={packet_count}",
                    f"segment_count={len(live_files)}",
                    f"capture_bytes={pcap_path.stat().st_size}",
                    "topology=runtime",
                    "bounded_media_ring=true" if is_load_like_run(args) and args.media_enabled else "bounded_media_ring=false",
                    "note=Capture contains live loopback packets; no SIP or TCP packets were reconstructed",
                ]
            ),
        )
        return [pcap_path]

    if not should_generate_pcap_artifacts(args):
        return []

    sip_packets = sipp_trace_packets(work_dir, args)
    diagnostic_packets = protocol_event_packets(log_dir, args)
    rtp_packets = rtp_media_packets(log_dir, work_dir, args)
    rtcp_packets = rtcp_media_packets(rtp_packets)
    packets = sip_packets + diagnostic_packets + rtp_packets + rtcp_packets
    if not packets:
        return []

    uac_ip, server_ip, uas_ip = pcap_topology_ips(args)
    rtpengine_ip = pcap_rtpengine_ip(args)
    pcap_path = log_dir / "capture.pcap"
    pcap_counts = write_udp_pcap(pcap_path, packets)
    append_log_section(
        log_dir,
        "log.platform",
        "PCAP GENERATION",
        "\n".join(
            [
                "source=diagnostic_logs",
                "scope=non_load_b2bua_profile",
                "file=capture.pcap",
                f"packet_count={pcap_counts['packet_count']}",
                f"sip_packets={len(sip_packets)}",
                f"rtp_packets={len(rtp_packets)}",
                f"rtcp_packets={len(rtcp_packets)}",
                f"diagnostic_packets={len(diagnostic_packets)}",
                f"udp_packets={pcap_counts['udp_packets']}",
                f"tcp_packets={pcap_counts['tcp_packets']}",
                f"topology={getattr(args, 'pcap_topology', BASE_DEFAULTS['pcap_topology'])}",
                f"topology_uac_ip={uac_ip}",
                f"topology_server_ip={server_ip}",
                f"topology_uas_ip={uas_ip}",
                f"topology_rtpengine_ip={rtpengine_ip}",
                "note=Single PCAP is generated from SIPp SIP traces, RTP media replay samples, and PlaySBC protocol logs after the call completes",
            ]
        ),
    )
    return [pcap_path]


def registration_ladder_text(participant: str, user: str, auth_outcome: str = "") -> str:
    step_width = 6
    column_width = 28

    def row(step: str = "") -> List[str]:
        text = list(" " * (step_width + (column_width * 2)))
        for offset, char in enumerate(f"{step:<{step_width}}"):
            text[offset] = char
        for position in positions:
            text[position] = "|"
        return text

    def put(text: List[str], start: int, value: str) -> None:
        for offset, char in enumerate(value):
            position = start + offset
            if 0 <= position < len(text):
                text[position] = char

    positions = [step_width + (column_width // 2), step_width + column_width + (column_width // 2)]
    header = f"{'Step':<{step_width}}{participant:^{column_width}}{'B2BUA':^{column_width}}".rstrip()
    separator = "-" * (step_width + (column_width * 2))
    lines = ["REGISTRATION LADDER", f"user={user}", header, separator, "".join(row()).rstrip()]

    exchanges = [("REGISTER", "right")]
    if auth_outcome:
        exchanges.extend(
            [
                ("401 + Digest challenge", "left"),
                ("REGISTER + Authorization", "right"),
                ("200 OK" if auth_outcome == "success" else "401 Unauthorized", "left"),
            ]
        )
    else:
        exchanges.append(("200 OK", "left"))

    for index, (message, direction) in enumerate(exchanges, start=1):
        label = row(f"{index:02d}")
        put(label, positions[0] + 2, message)
        lines.append("".join(label).rstrip())
        arrow = row()
        if direction == "right":
            for position in range(positions[0] + 1, positions[1] - 1):
                arrow[position] = "-"
            arrow[positions[1] - 1] = ">"
        else:
            arrow[positions[0] + 1] = "<"
            for position in range(positions[0] + 2, positions[1]):
                arrow[position] = "-"
        lines.append("".join(arrow).rstrip())
    lines.append("".join(row()).rstrip())
    return "\n".join(lines)


def registration_auth_counts(work_dir: Path, label: str = "registration-callee") -> dict[str, int]:
    counts = {"register": 0, "authorization": 0, "challenge": 0, "success": 0}
    for trace in sorted((work_dir / label).glob("*_messages.log")):
        for _timestamp, direction, payload in sipp_trace_messages(trace):
            if direction == "sent" and payload.startswith(b"REGISTER "):
                counts["register"] += 1
                if re.search(rb"(?im)^Authorization\s*:\s*Digest\s+", payload):
                    counts["authorization"] += 1
            elif direction == "received" and payload.startswith(b"SIP/2.0 401"):
                counts["challenge"] += 1
            elif direction == "received" and payload.startswith(b"SIP/2.0 200"):
                counts["success"] += 1
    return counts


def append_registration_auth_observation(
    log_dir: Path,
    work_dir: Path,
    args: argparse.Namespace,
    results: List[SmokeResult],
) -> None:
    expected = str(getattr(args, "registration_auth_expected", "") or "")
    if not expected or bool(getattr(args, "dry_run", False)):
        return
    counts = registration_auth_counts(work_dir)
    server_stdout = (work_dir / "server" / "stdout.log").read_text(encoding="utf-8", errors="replace")
    registered = f"Registered {args.callee} " in server_stdout
    valid_common = counts["register"] >= 2 and counts["authorization"] >= 1
    if expected == "success":
        passed = valid_common and counts["challenge"] >= 1 and counts["success"] >= 1 and registered
    else:
        passed = valid_common and counts["challenge"] >= 2 and counts["success"] == 0 and not registered
    append_log_section(
        log_dir,
        "log.sip",
        "REGISTER DIGEST VALIDATION",
        "\n".join(
            [
                f"expected={expected} status={'passed' if passed else 'failed'}",
                f"register_requests={counts['register']}",
                f"authorization_headers={counts['authorization']}",
                f"unauthorized_responses={counts['challenge']}",
                f"ok_responses={counts['success']}",
                f"registrar_binding_created={str(registered).lower()}",
            ]
        ),
    )
    results.append(SmokeResult("register-digest-validation", [], 0 if passed else 1, "passed" if passed else "failed", 0.0))


def append_registration_ladders(log_dir: Path, args: argparse.Namespace, results: List[SmokeResult]) -> None:
    if not args.ladder_enabled:
        return
    statuses = {result.name: result.status for result in results}
    if statuses.get("registration") == "passed":
        append_log_section(
            log_dir,
            "log.sip",
            "CALLEE REGISTRATION LADDER",
            registration_ladder_text(
                "SIPp B",
                args.callee,
                str(getattr(args, "registration_auth_expected", "") or ""),
            ),
        )
    if args.register_caller and statuses.get("caller-registration") == "passed":
        append_log_section(
            log_dir,
            "log.sip",
            "CALLER REGISTRATION LADDER",
            registration_ladder_text("SIPp A", args.caller),
        )


def total_logged_rtp_packets(log_dir: Path) -> int:
    path = log_dir / "log.media"
    if not path.exists():
        return 0
    text = path.read_text(encoding="utf-8", errors="replace")
    return sum(int(value) for value in re.findall(r"rtp_packets_received=(\d+)", text))


def media_summary_stats(log_dir: Path) -> dict:
    path = log_dir / "log.media"
    stats = {
        "summary_count": 0,
        "duration_seconds_max": 0.0,
        "rtp_packets_received_total": 0,
        "rtp_packets_sent_total": 0,
        "rtp_packets_relayed_total": 0,
        "rtcp_packets_received_total": 0,
        "rtcp_packets_sent_total": 0,
        "rtcp_packets_relayed_total": 0,
    }
    if not path.exists():
        return stats

    pending_summary = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "CALL SUMMARY" in line:
            pending_summary = True
        if not pending_summary:
            continue
        stats["summary_count"] += 1
        duration = re.search(r"duration_seconds=([0-9.]+)", line)
        received = re.search(r"rtp_packets_received=(\d+)", line)
        sent = re.search(r"rtp_packets_sent=(\d+)", line)
        relayed = re.search(r"rtp_packets_relayed=(\d+)", line)
        rtcp_received = re.search(r"rtcp_packets_received=(\d+)", line)
        rtcp_sent = re.search(r"rtcp_packets_sent=(\d+)", line)
        rtcp_relayed = re.search(r"rtcp_packets_relayed=(\d+)", line)
        if not any((duration, received, sent, relayed, rtcp_received, rtcp_sent, rtcp_relayed)):
            stats["summary_count"] -= 1
            continue
        pending_summary = False
        if duration:
            stats["duration_seconds_max"] = max(stats["duration_seconds_max"], float(duration.group(1)))
        if received:
            stats["rtp_packets_received_total"] += int(received.group(1))
        if sent:
            stats["rtp_packets_sent_total"] += int(sent.group(1))
        if relayed:
            stats["rtp_packets_relayed_total"] += int(relayed.group(1))
        if rtcp_received:
            stats["rtcp_packets_received_total"] += int(rtcp_received.group(1))
        if rtcp_sent:
            stats["rtcp_packets_sent_total"] += int(rtcp_sent.group(1))
        if rtcp_relayed:
            stats["rtcp_packets_relayed_total"] += int(rtcp_relayed.group(1))
    return stats


def rtpengine_query_stats(log_dir: Path) -> dict:
    path = log_dir / "log.media"
    stats = {
        "query_count": 0,
        "query_failures": 0,
        "query_retries": 0,
        "rtp_packets_total": 0,
        "rtp_packets_min": 0,
        "rtp_packets_max": 0,
        "rtp_bytes_total": 0,
        "rtp_errors_total": 0,
        "rtcp_packets_total": 0,
        "rtcp_bytes_total": 0,
        "rtcp_errors_total": 0,
    }
    if not path.exists():
        return stats

    def record_rtp_packets(packet_count: int) -> None:
        stats["rtp_packets_total"] += packet_count
        stats["rtp_packets_min"] = packet_count if stats["rtp_packets_min"] == 0 else min(stats["rtp_packets_min"], packet_count)
        stats["rtp_packets_max"] = max(stats["rtp_packets_max"], packet_count)

    def parse_query_detail(detail: str) -> bool:
        compact_packets = re.search(r"\brtp_packets_total=(\d+)", detail)
        compact_bytes = re.search(r"\brtp_bytes_total=(\d+)", detail)
        compact_errors = re.search(r"\brtp_errors_total=(\d+)", detail)
        compact_rtcp_packets = re.search(r"\brtcp_packets_total=(\d+)", detail)
        compact_rtcp_bytes = re.search(r"\brtcp_bytes_total=(\d+)", detail)
        compact_rtcp_errors = re.search(r"\brtcp_errors_total=(\d+)", detail)
        if compact_packets:
            record_rtp_packets(int(compact_packets.group(1)))
            stats["rtp_bytes_total"] += int(compact_bytes.group(1)) if compact_bytes else 0
            stats["rtp_errors_total"] += int(compact_errors.group(1)) if compact_errors else 0
            stats["rtcp_packets_total"] += int(compact_rtcp_packets.group(1)) if compact_rtcp_packets else 0
            stats["rtcp_bytes_total"] += int(compact_rtcp_bytes.group(1)) if compact_rtcp_bytes else 0
            stats["rtcp_errors_total"] += int(compact_rtcp_errors.group(1)) if compact_rtcp_errors else 0
            compact_retries = re.search(r"\bquery_retry_count=(\d+)", detail)
            stats["query_retries"] += int(compact_retries.group(1)) if compact_retries else 0
            return True

        if not detail.startswith("{"):
            return False
        try:
            decoded = json.loads(detail)
        except json.JSONDecodeError:
            return False
        rtp_totals = decoded.get("totals", {}).get("RTP", {}) if isinstance(decoded, dict) else {}
        rtcp_totals = decoded.get("totals", {}).get("RTCP", {}) if isinstance(decoded, dict) else {}
        record_rtp_packets(int(rtp_totals.get("packets") or 0))
        stats["rtp_bytes_total"] += int(rtp_totals.get("bytes") or 0)
        stats["rtp_errors_total"] += int(rtp_totals.get("errors") or 0)
        stats["rtcp_packets_total"] += int(rtcp_totals.get("packets") or 0)
        stats["rtcp_bytes_total"] += int(rtcp_totals.get("bytes") or 0)
        stats["rtcp_errors_total"] += int(rtcp_totals.get("errors") or 0)
        return True

    pending_query = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if pending_query:
            if parse_query_detail(line):
                stats["query_count"] += 1
            pending_query = False
            continue
        if "RTPENGINE QUERY FAILED" in line:
            stats["query_failures"] += 1
            continue
        if "B2BUA RTPENGINE QUERY" not in line:
            continue

        _prefix, separator, payload = line.rpartition(" | ")
        if separator and parse_query_detail(payload):
            stats["query_count"] += 1
        else:
            pending_query = True
    return stats


def append_media_observation(log_dir: Path, args: argparse.Namespace) -> None:
    if not args.media_enabled:
        append_log_section(
            log_dir,
            "log.media",
            "MEDIA OBSERVATION",
            "expected_rtp=False reason=media_disabled",
        )
        return

    if args.media_backend == "rtpengine":
        media_text = (log_dir / "log.media").read_text(encoding="utf-8", errors="replace") if (log_dir / "log.media").exists() else ""
        query_stats = rtpengine_query_stats(log_dir)
        rtpengine_answered = "RTPENGINE ANSWER" in media_text
        status = "rtpengine_media_anchored" if rtpengine_answered or query_stats["rtp_packets_total"] > 0 else "rtpengine_media_not_confirmed"
        ai_config = getattr(args, "ai_voice_gateway", {}) if is_ai_gateway_profile(args) else {}
        lines = [
            f"expected_rtp=True status={status}",
            "media_backend=rtpengine",
            f"media_driver={args.media_driver}",
            f"media_codec={args.media_codec}",
            f"media_pcap={args.media_pcap_resolved}",
            f"hold_ms={args.hold_ms}",
            f"media_delivery_threshold_percent={getattr(args, 'media_delivery_threshold_percent', 100.0)}",
            f"media_per_call_threshold_percent={getattr(args, 'media_per_call_threshold_percent', 100.0)}",
            f"rtpengine_query_count={query_stats['query_count']}",
            f"rtpengine_query_failures={query_stats['query_failures']}",
            f"rtpengine_query_retries={query_stats['query_retries']}",
            f"rtpengine_rtp_packets_total={query_stats['rtp_packets_total']}",
            f"rtpengine_rtp_packets_min={query_stats['rtp_packets_min']}",
            f"rtpengine_rtp_packets_max={query_stats['rtp_packets_max']}",
            f"rtpengine_rtp_bytes_total={query_stats['rtp_bytes_total']}",
            f"rtpengine_rtp_errors_total={query_stats['rtp_errors_total']}",
            f"rtpengine_rtcp_packets_total={query_stats['rtcp_packets_total']}",
            f"rtpengine_rtcp_bytes_total={query_stats['rtcp_bytes_total']}",
            f"rtpengine_rtcp_errors_total={query_stats['rtcp_errors_total']}",
            "server_rtp_received_packets_total=0",
        ]
        if isinstance(ai_config, dict) and ai_config:
            lines.extend(
                [
                    "media_mode=ai-gateway",
                    f"stt_adapter={ai_config.get('stt_provider', '')}",
                    f"tts_adapter={ai_config.get('tts_provider', '')}",
                    "ai_media_direction=rtpengine-anchored-input",
                ]
            )
        lines.append("note=RTPengine anchors RTP externally, so PlaySBC internal RTP counters remain zero")
        append_log_section(
            log_dir,
            "log.media",
            "MEDIA OBSERVATION",
            "\n".join(lines),
        )
        return

    packets = total_logged_rtp_packets(log_dir)
    summary = media_summary_stats(log_dir)
    if str(getattr(args, "profile", "")) == "ai-rasa-lab":
        status = "ai_input_observed" if packets > 0 else "no_ai_rtp_input_observed"
        append_log_section(
            log_dir,
            "log.media",
            "MEDIA OBSERVATION",
            "\n".join(
                [
                    f"expected_rtp=True status={status}",
                    "media_mode=ai-gateway",
                    "ai_media_direction=input-only",
                    "stt_adapter=lab-scripted",
                    "tts_adapter=text-only",
                    "tts_audio_generated=false",
                    "rtp_prompt_generated=false",
                    f"media_driver={args.media_driver}",
                    f"media_codec={args.media_codec}",
                    f"media_pcap={args.media_pcap_resolved}",
                    f"hold_ms={args.hold_ms}",
                    f"server_rtp_received_packets_total={packets}",
                    f"server_rtcp_received_packets_total={summary['rtcp_packets_received_total']}",
                    f"server_rtcp_sent_packets_total={summary['rtcp_packets_sent_total']}",
                    "note=AI gateway currently verifies RTP input and Rasa REST; real TTS RTP prompts are a future adapter.",
                ]
            ),
        )
        return
    status = "rtp_observed" if packets > 0 else "no_rtp_observed"
    append_log_section(
        log_dir,
        "log.media",
        "MEDIA OBSERVATION",
        "\n".join(
            [
                f"expected_rtp=True status={status}",
                f"media_driver={args.media_driver}",
                f"media_codec={args.media_codec}",
                f"media_pcap={args.media_pcap_resolved}",
                f"hold_ms={args.hold_ms}",
                f"media_delivery_threshold_percent={getattr(args, 'media_delivery_threshold_percent', 100.0)}",
                f"media_per_call_threshold_percent={getattr(args, 'media_per_call_threshold_percent', 100.0)}",
                f"server_rtp_received_packets_total={packets}",
                f"server_rtcp_received_packets_total={summary['rtcp_packets_received_total']}",
                f"server_rtcp_sent_packets_total={summary['rtcp_packets_sent_total']}",
                f"server_rtcp_relayed_packets_total={summary['rtcp_packets_relayed_total']}",
            ]
        ),
    )


def append_rtcp_observation(log_dir: Path, work_dir: Path, args: argparse.Namespace, results: List[SmokeResult]) -> bool:
    if bool(getattr(args, "dry_run", False)) or not should_run_rtcp(args):
        return True
    result_by_name = {result.name: result for result in results}
    sender_lines = []
    expected_senders = rtcp_expected_sender_names(args)
    for name in expected_senders:
        path = work_dir / f"{name}.log"
        sender_lines.append(path.read_text(encoding="utf-8", errors="replace").strip() if path.exists() else f"{name}=missing")
    sender_ok = all(result_by_name.get(name) and result_by_name[name].status == "passed" for name in expected_senders)
    summary = media_summary_stats(log_dir)
    query = rtpengine_query_stats(log_dir)
    observed = query["rtcp_packets_total"] if args.media_backend == "rtpengine" else summary["rtcp_packets_received_total"]
    append_log_section(
        log_dir,
        "log.media",
        "RTCP OBSERVATION",
        "\n".join(
            [
                f"expected=True status={'observed' if sender_ok and observed > 0 else 'not_observed'}",
                f"media_backend={args.media_backend}",
                f"rtcp_packets_observed={observed}",
                *sender_lines,
            ]
        ),
    )
    return sender_ok and observed > 0


def append_dtmf_observation(log_dir: Path, args: argparse.Namespace, results: List[SmokeResult]) -> bool:
    if bool(getattr(args, "dry_run", False)) or not bool(getattr(args, "dtmf_expected", False)):
        return True
    media_path = log_dir / "log.media"
    text = media_path.read_text(encoding="utf-8", errors="replace") if media_path.exists() else ""
    starts = len(re.findall(r"\| DTMF START \|.*\bdigit=5\b", text))
    ends = len(re.findall(r"\| DTMF END \|.*\bdigit=5\b", text))
    relays = len(re.findall(r"\| DTMF RELAY \|.*\bdigit=5\b", text))
    summaries = len(re.findall(r"\| CALL SUMMARY \|.*\bdtmf=[^\s]*5", text))
    passed = starts > 0 and ends > 0 and relays > 0 and summaries > 0
    append_log_section(
        log_dir,
        "log.media",
        "RFC4733 DTMF VALIDATION",
        "\n".join(
            [
                f"expected_digit=5 status={'passed' if passed else 'failed'}",
                "payload_type=101 encoding=telephone-event/8000",
                f"dtmf_start_events={starts}",
                f"dtmf_end_events={ends}",
                f"dtmf_relay_events={relays}",
                f"call_summaries_with_digit={summaries}",
            ]
        ),
    )
    results.append(SmokeResult("rfc4733-dtmf-validation", [], 0 if passed else 1, "passed" if passed else "failed", 0.0))
    return passed


def expected_rtpengine_load_packets(args: argparse.Namespace) -> int:
    packets_per_leg = max(int(args.hold_ms) // 20, 0)
    return int(args.calls) * 2 * packets_per_leg


def rtpengine_query_result_count(log_dir: Path) -> int:
    path = log_dir / "log.media"
    if not path.exists():
        return 0
    return sum(
        1
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if "B2BUA RTPENGINE QUERY |" in line or "B2BUA RTPENGINE QUERY FAILED |" in line
    )


def wait_for_rtpengine_load_queries(log_dir: Path, expected: int, timeout: float = 6.0) -> Tuple[int, float]:
    started = time.monotonic()
    observed = rtpengine_query_result_count(log_dir)
    deadline = started + timeout
    while observed < expected and time.monotonic() < deadline:
        time.sleep(0.050)
        observed = rtpengine_query_result_count(log_dir)
    return observed, time.monotonic() - started


def rtpengine_load_media_complete(log_dir: Path, args: argparse.Namespace) -> bool:
    if bool(getattr(args, "dry_run", False)) or str(getattr(args, "profile", "")) != "load-5cps-60s-rtpengine-transcoding":
        return True
    stats = rtpengine_query_stats(log_dir)
    expected = expected_rtpengine_load_packets(args)
    threshold = min(max(float(getattr(args, "media_delivery_threshold_percent", 99.5)), 0.0), 100.0)
    per_call_threshold = min(max(float(getattr(args, "media_per_call_threshold_percent", 99.0)), 0.0), 100.0)
    required = math.ceil(expected * threshold / 100.0)
    expected_per_call = max(int(args.hold_ms) // 20, 0) * 2
    required_per_call = math.ceil(expected_per_call * per_call_threshold / 100.0)
    delivery = (stats["rtp_packets_total"] / expected * 100.0) if expected else 100.0
    loss = max(100.0 - delivery, 0.0)
    complete = (
        stats["query_count"] == int(args.calls)
        and stats["query_failures"] == 0
        and stats["rtp_errors_total"] == 0
        and stats["rtp_packets_total"] >= required
        and stats["rtp_packets_min"] >= required_per_call
    )
    append_log_section(
        log_dir,
        "log.media",
        "RTPENGINE LOAD COMPLETENESS",
        "\n".join(
            [
                f"status={'complete' if complete else 'incomplete'}",
                f"expected_queries={args.calls} observed_queries={stats['query_count']}",
                f"query_failures={stats['query_failures']} query_retries={stats['query_retries']}",
                f"expected_rtp_packets={expected} observed_rtp_packets={stats['rtp_packets_total']}",
                f"required_rtp_packets={required} media_delivery_threshold_percent={threshold:.3f}",
                f"required_rtp_packets_per_call={required_per_call} media_per_call_threshold_percent={per_call_threshold:.3f}",
                f"media_delivery_percent={delivery:.3f} media_loss_percent={loss:.3f}",
                f"per_call_rtp_packets_min={stats['rtp_packets_min']} per_call_rtp_packets_max={stats['rtp_packets_max']}",
                f"rtp_errors_total={stats['rtp_errors_total']}",
            ]
        ),
    )
    return complete


def append_transcoding_observation(log_dir: Path, args: argparse.Namespace) -> None:
    transcoding_expected = bool(args.media_codec and args.server_codec and args.media_codec != args.server_codec)
    if not transcoding_expected:
        append_log_section(
            log_dir,
            "log.transcoding",
            "TRANSCODING OBSERVATION",
            "expected=False reason=codec_match_or_media_disabled",
        )
        return

    path = log_dir / "log.transcoding"
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    media_text = (log_dir / "log.media").read_text(encoding="utf-8", errors="replace") if (log_dir / "log.media").exists() else ""
    if args.media_backend == "rtpengine":
        query_stats = rtpengine_query_stats(log_dir)
        delegated = "RTPENGINE CODEC POLICY" in media_text and "RTPENGINE ANSWER" in media_text
        if delegated and query_stats["rtp_packets_total"] > 0:
            status = "delegated_and_media_confirmed"
        elif delegated:
            status = "delegated"
        else:
            status = "not_confirmed"
        append_log_section(
            log_dir,
            "log.transcoding",
            "TRANSCODING OBSERVATION",
            "\n".join(
                [
                    f"expected=True status={status}",
                    f"src={args.media_codec} dst={args.server_codec}",
                    "owner=rtpengine",
                    f"rtpengine_query_count={query_stats['query_count']}",
                    f"rtpengine_rtp_packets_total={query_stats['rtp_packets_total']}",
                    f"rtpengine_rtp_bytes_total={query_stats['rtp_bytes_total']}",
                    f"rtpengine_rtp_errors_total={query_stats['rtp_errors_total']}",
                    "server_rtp_received_packets_total=0",
                    "note=RTPengine performs transcoding externally; validate media stats with RTPengine query/PCAP when available",
                ]
            ),
        )
        return

    active = "TRANSCODE ACTIVE" in text
    bypass = "TRANSCODE BYPASS" in text
    packets = total_logged_rtp_packets(log_dir)
    if active:
        status = "active"
    elif bypass:
        status = "bypassed"
    else:
        status = "not_observed"
    append_log_section(
        log_dir,
        "log.transcoding",
        "TRANSCODING OBSERVATION",
        "\n".join(
            [
                f"expected=True status={status}",
                f"src={args.media_codec} dst={args.server_codec}",
                f"owner={'rtpengine' if args.media_backend == 'rtpengine' else 'internal'}",
                f"server_rtp_received_packets_total={packets}",
                "note=TRANSCODE ACTIVE appears only after RTP packets arrive with a payload type that must be converted",
            ]
        ),
    )


def append_results(log_dir: Path, args: argparse.Namespace, results: List[SmokeResult]) -> None:
    transcoding_expected = bool(args.media_codec and args.server_codec and args.media_codec != args.server_codec)
    media_stats = media_summary_stats(log_dir) if args.media_enabled else None
    lines = [
        f"run_id={args.resolved_run_id}",
        f"log_folder={args.log_folder}",
        f"profile={args.profile or 'custom'}",
        f"caller={args.caller}",
        f"callee={args.callee}",
        f"register_callee={args.register_callee}",
        f"register_caller={args.register_caller}",
        f"start_uas={args.start_uas}",
        f"sip_transport={getattr(args, 'sip_transport', BASE_DEFAULTS['sip_transport'])}",
        f"reject_unknown_routes={args.reject_unknown_routes}",
        f"registration_driver={args.registration_driver}",
        f"registration_auth_expected={getattr(args, 'registration_auth_expected', '')}",
        f"run_call={getattr(args, 'run_call', True)}",
        f"route_policies={json.dumps(effective_route_policies(args), sort_keys=True)}",
        f"b2bua_routes={json.dumps(effective_b2bua_routes(args), sort_keys=True)}",
        f"sipp_trace_mode={'stats-only' if is_load_like_run(args) else 'full'}",
        f"calls={args.calls}",
        f"rate={args.rate}",
        f"hold_ms={args.hold_ms}",
        f"media_delivery_threshold_percent={getattr(args, 'media_delivery_threshold_percent', 100.0)}",
        f"media_per_call_threshold_percent={getattr(args, 'media_per_call_threshold_percent', 100.0)}",
        f"server_codec={args.server_codec}",
        f"media_enabled={args.media_enabled}",
        f"media_codec={args.media_codec or ''}",
        f"uas_media_codec={uas_media_codec(args) if args.media_enabled else ''}",
        f"media_driver={args.media_driver if args.media_enabled else ''}",
        f"sipp_pcap_sudo={args.sipp_pcap_sudo if args.media_enabled and args.media_driver == 'sipp-pcap' else False}",
        f"media_pcap={getattr(args, 'media_pcap_resolved', getattr(args, 'media_pcap', '') or '') if args.media_enabled else ''}",
        f"uas_media_pcap={getattr(args, 'uas_media_pcap_resolved', '') if args.media_enabled else ''}",
        f"media_backend={args.media_backend}",
        f"dtmf_expected={getattr(args, 'dtmf_expected', False)}",
        f"rtpengine_url={args.rtpengine_url if args.media_backend == 'rtpengine' else ''}",
        f"transcoding_expected={transcoding_expected}",
        f"transcoding_owner={'rtpengine' if transcoding_expected and args.media_backend == 'rtpengine' else 'internal' if transcoding_expected else ''}",
        f"pcap_topology={getattr(args, 'pcap_topology', BASE_DEFAULTS['pcap_topology'])}",
        f"pcap_uac_ip={pcap_topology_ips(args)[0]}",
        f"pcap_server_ip={pcap_topology_ips(args)[1]}",
        f"pcap_uas_ip={pcap_topology_ips(args)[2]}",
        f"pcap_rtpengine_ip={pcap_rtpengine_ip(args)}",
        f"ladder_enabled={args.ladder_enabled}",
        "",
    ]
    for result in results:
        code = "" if result.returncode is None else f" returncode={result.returncode}"
        duration_label = "process_lifetime_seconds" if result.name == "sipp-b-uas" else "duration_seconds"
        lines.append(f"{result.name}: {result.status}{code} {duration_label}={result.duration_seconds:.3f}")
    if media_stats and media_stats["summary_count"] > 0:
        lines.extend(
            [
                "",
                "MEDIA DURATION SUMMARY",
                f"media_call_summary_count={media_stats['summary_count']}",
                f"media_call_duration_seconds_max={media_stats['duration_seconds_max']:.3f}",
                f"media_rtp_packets_received_total={media_stats['rtp_packets_received_total']}",
                f"media_rtp_packets_sent_total={media_stats['rtp_packets_sent_total']}",
                f"media_rtp_packets_relayed_total={media_stats['rtp_packets_relayed_total']}",
                "media_duration_source=log.media CALL SUMMARY",
            ]
        )
    append_log_section(log_dir, "log.platform", "B2BUA SIPP RUN RESULT", "\n".join(lines))


def collect_work_logs(log_dir: Path, work_dir: Path, args: Optional[argparse.Namespace] = None) -> None:
    append_file_section(log_dir, "log.platform", "SERVER STDOUT", work_dir / "server" / "stdout.log")
    if args is not None:
        append_ordered_sip_trace(log_dir, work_dir, args)

    for leg in ("registration-callee", "registration-caller", "sipp-a-uac", "sipp-b-uas"):
        leg_dir = work_dir / leg
        append_file_section(log_dir, "log.sipp", f"{leg.upper()} STDOUT", leg_dir / "stdout.log")
        append_file_section(log_dir, "log.sipp", f"{leg.upper()} STDERR", leg_dir / "stderr.log")
        if not (args and is_load_like_run(args)):
            for trace in sorted(leg_dir.glob("*_messages.log")):
                append_file_section(log_dir, "log.sipp", f"{leg.upper()} RAW SIP TRACE {trace.name}", trace)
        for trace in sorted(leg_dir.glob("*_logs.log")):
            append_file_section(log_dir, "log.sipp", f"{leg.upper()} EVENT TRACE {trace.name}", trace)
        for trace in sorted(leg_dir.glob("*srtp*")):
            if trace.is_file() and trace.suffix != ".raw" and trace.stat().st_size <= 1_000_000:
                append_file_section(log_dir, "log.media", f"{leg.upper()} SRTP TRACE {trace.name}", trace)

    for media_log in sorted(work_dir.glob("media-*.log")):
        append_file_section(log_dir, "log.media", f"MEDIA PLAYER {media_log.name}", media_log)
    for capture_log in sorted(work_dir.glob("live-pcap*.log")):
        append_file_section(log_dir, "log.platform", f"LIVE PCAP {capture_log.name}", capture_log)


def register_user(
    args: argparse.Namespace,
    log_dir: Path,
    user: str,
    contact_port: int,
    bind_port: int,
    label: str,
) -> int:
    branch = f"z9hG4bK-register-{int(time.time() * 1000)}"
    call_id = f"register-{user}-{int(time.time())}@{args.host}"
    packet = CRLF.join(
        [
            f"REGISTER sip:{args.host}:{args.server_port} SIP/2.0",
            f"Via: SIP/2.0/UDP {args.host}:{bind_port};branch={branch}",
            f"From: <sip:{user}@{args.host}>;tag=register-{user}",
            f"To: <sip:{user}@{args.host}>",
            f"Call-ID: {call_id}",
            "CSeq: 1 REGISTER",
            f"Contact: <sip:{user}@{args.host}:{contact_port}>",
            "Max-Forwards: 70",
            "Expires: 300",
            "Content-Length: 0",
            "",
            "",
        ]
    ).encode("utf-8")

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(3)
        sock.bind((args.host, bind_port))
        sock.sendto(packet, (args.host, args.server_port))
        response, _ = sock.recvfrom(4096)

    text = response.decode("utf-8", errors="replace")
    append_log_section(
        log_dir,
        "log.sip",
        f"DYNAMIC {label.upper()} REGISTER",
        packet.decode("utf-8", errors="replace") + "\n--- response ---\n" + text,
    )
    return 0 if "SIP/2.0 200" in text else 1


def register_endpoint(args: argparse.Namespace, log_dir: Path) -> int:
    return register_user(args, log_dir, args.callee, args.uas_port, args.register_port, "callee")


def register_caller(args: argparse.Namespace, log_dir: Path) -> int:
    return register_user(args, log_dir, args.caller, args.uac_port, args.caller_register_port, "caller")


def run_sipp_registration(command: List[str], work_dir: Path, label: str) -> int:
    step_dir = work_dir / label
    step_dir.mkdir(exist_ok=True)
    completed = subprocess.run(command, cwd=step_dir, text=True, capture_output=True)
    (step_dir / "stdout.log").write_text(completed.stdout, encoding="utf-8")
    (step_dir / "stderr.log").write_text(completed.stderr, encoding="utf-8")
    return completed.returncode


def resolve_log_dir(args: argparse.Namespace, run_id: str) -> Tuple[Path, bool]:
    log_folder = args.log_folder or DEFAULT_LOG_FOLDER
    bundle_name = run_id.replace(os.sep, "-")
    if args.output_root:
        return Path(args.output_root) / log_folder / bundle_name, True
    if args.dry_run:
        return Path(tempfile.mkdtemp(prefix=f"{run_id}-")) / log_folder / bundle_name, True
    return ROOT / "logs" / log_folder / bundle_name, True


def apply_profile(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if not args.profile:
        return
    defaults = parser.get_default
    for key, value in B2BUA_PROFILES[args.profile].items():
        if getattr(args, key, defaults(key)) == defaults(key):
            setattr(args, key, value)


def print_profiles() -> None:
    print("Available B2BUA SIPp profiles:")
    for name, description in PROFILE_DESCRIPTIONS.items():
        print(f"  {name}: {description}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run registrar-backed SIPp B2BUA smoke/load tests")
    parser.add_argument("--profile", choices=sorted(B2BUA_PROFILES), default="", help="Named B2BUA SIPp test profile")
    parser.add_argument("--list-profiles", action="store_true", help="List named B2BUA SIPp test profiles")
    parser.add_argument("--host", default=BASE_DEFAULTS["host"])
    parser.add_argument("--server-port", type=int, default=BASE_DEFAULTS["server_port"])
    parser.add_argument("--sip-transport", choices=("udp", "tcp", "tls", "udp,tcp"), default=BASE_DEFAULTS["sip_transport"], help="PlaySBC SIP listener transport for this run")
    parser.add_argument("--uac-port", type=int, default=BASE_DEFAULTS["uac_port"])
    parser.add_argument("--uas-port", type=int, default=BASE_DEFAULTS["uas_port"])
    parser.add_argument("--register-port", type=int, default=BASE_DEFAULTS["register_port"])
    parser.add_argument("--caller-register-port", type=int, default=BASE_DEFAULTS["caller_register_port"])
    parser.add_argument("--server-rtp-min", type=int, default=BASE_DEFAULTS["server_rtp_min"])
    parser.add_argument("--server-rtp-max", type=int, default=BASE_DEFAULTS["server_rtp_max"])
    parser.add_argument("--uac-rtp-min", type=int, default=BASE_DEFAULTS["uac_rtp_min"])
    parser.add_argument("--uac-rtp-max", type=int, default=BASE_DEFAULTS["uac_rtp_max"])
    parser.add_argument("--uas-rtp-min", type=int, default=BASE_DEFAULTS["uas_rtp_min"])
    parser.add_argument("--uas-rtp-max", type=int, default=BASE_DEFAULTS["uas_rtp_max"])
    parser.add_argument("--caller", default=BASE_DEFAULTS["caller"], help="SIP user used by SIPp A in From/Contact")
    parser.add_argument("--callee", default=BASE_DEFAULTS["callee"])
    parser.add_argument("--register-callee", dest="register_callee", action="store_true", default=BASE_DEFAULTS["register_callee"], help="REGISTER SIPp B before the UAC call")
    parser.add_argument("--no-register-callee", dest="register_callee", action="store_false", help="Skip callee registration")
    parser.add_argument("--register-caller", action="store_true", default=BASE_DEFAULTS["register_caller"], help="REGISTER the SIPp A caller before originating")
    parser.add_argument("--start-uas", dest="start_uas", action="store_true", default=BASE_DEFAULTS["start_uas"], help="Start the SIPp B UAS leg")
    parser.add_argument("--no-start-uas", dest="start_uas", action="store_false", help="Skip SIPp B UAS startup")
    parser.add_argument("--reject-unknown-routes", action="store_true", default=BASE_DEFAULTS["reject_unknown_routes"], help="Make PlaySBC reject unrouted INVITEs with 404 instead of echo mode")
    parser.add_argument("--calls", type=int, default=BASE_DEFAULTS["calls"])
    parser.add_argument("--rate", type=int, default=BASE_DEFAULTS["rate"])
    parser.add_argument("--hold-ms", type=int, default=BASE_DEFAULTS["hold_ms"])
    parser.add_argument("--media-codec", choices=sorted(MEDIA_PCAPS), default=BASE_DEFAULTS["media_codec"], help="Play 60s RTP PCAP media using this G.711 codec")
    parser.add_argument("--media-pcap", default=BASE_DEFAULTS["media_pcap"], help="Override the RTP PCAP file used with --media-codec")
    parser.add_argument("--media-driver", choices=("python", "sipp-pcap"), default=BASE_DEFAULTS["media_driver"], help="Use Python UDP replay or SIPp play_pcap_audio for media")
    parser.add_argument(
        "--sipp-pcap-sudo",
        action="store_true",
        default=BASE_DEFAULTS["sipp_pcap_sudo"],
        help="Temporary macOS workaround: run SIPp play_pcap_audio processes with sudo -n",
    )
    parser.add_argument("--media-start-delay", type=float, default=BASE_DEFAULTS["media_start_delay"], help="Seconds to wait after starting SIPp A before Python media replay starts")
    parser.add_argument("--expect-dtmf", dest="dtmf_expected", action="store_true", help="Require RFC 4733 digit detection and relay evidence")
    parser.add_argument(
        "--media-delivery-threshold-percent",
        type=float,
        default=BASE_DEFAULTS["media_delivery_threshold_percent"],
        help="Minimum aggregate RTP delivery percentage required by media load profiles",
    )
    parser.add_argument(
        "--media-per-call-threshold-percent",
        type=float,
        default=BASE_DEFAULTS["media_per_call_threshold_percent"],
        help="Minimum RTP delivery percentage required for every observed media call",
    )
    parser.add_argument("--server-codec", choices=sorted(MEDIA_PCAPS), default=BASE_DEFAULTS["server_codec"], help="Server preferred G.711 codec; set different from media codec to exercise transcoding")
    parser.add_argument("--media-backend", choices=("internal", "rtpengine"), default=BASE_DEFAULTS["media_backend"])
    parser.add_argument("--rtpengine-url", default=BASE_DEFAULTS["rtpengine_url"])
    parser.add_argument("--rtpengine-timeout", type=float, default=BASE_DEFAULTS["rtpengine_timeout"])
    parser.add_argument("--skip-rtpengine-preflight", action="store_true", help="Start the profile without checking RTPengine NG readiness first")
    parser.add_argument("--registration-driver", choices=("sipp", "python"), default=BASE_DEFAULTS["registration_driver"])
    parser.add_argument("--uac-scenario", default=BASE_DEFAULTS["uac_scenario"], help="Override SIPp UAC scenario XML")
    parser.add_argument("--uas-scenario", default=BASE_DEFAULTS["uas_scenario"], help="Override SIPp UAS scenario XML")
    parser.add_argument("--ladder", dest="ladder", action="store_true", default=BASE_DEFAULTS["ladder"], help="Force unified B2BUA ladder logs on")
    parser.add_argument("--no-ladder", dest="ladder", action="store_false", help="Force unified B2BUA ladder logs off")
    parser.add_argument("--output-root", default=BASE_DEFAULTS["output_root"])
    parser.add_argument(
        "--log-folder",
        default=BASE_DEFAULTS["log_folder"],
        help="Folder name used under logs/ as the parent for per-testcase B2BUA log bundles",
    )
    parser.add_argument(
        "--pcap-topology",
        choices=("logical", "runtime"),
        default=BASE_DEFAULTS["pcap_topology"],
        help="Use logical SIPp A/PlaySBC/SIPp B IPs in capture.pcap, or preserve runtime bind IPs",
    )
    parser.add_argument("--pcap-uac-ip", default=BASE_DEFAULTS["pcap_uac_ip"], help="Logical SIPp A IP written to capture.pcap")
    parser.add_argument("--pcap-server-ip", default=BASE_DEFAULTS["pcap_server_ip"], help="Logical PlaySBC IP written to capture.pcap")
    parser.add_argument("--pcap-uas-ip", default=BASE_DEFAULTS["pcap_uas_ip"], help="Logical SIPp B IP written to capture.pcap")
    parser.add_argument("--pcap-rtpengine-ip", default=BASE_DEFAULTS["pcap_rtpengine_ip"], help="Logical RTPengine media-anchor IP written to capture.pcap")
    parser.add_argument("--run-id", default=BASE_DEFAULTS["run_id"])
    parser.add_argument("--sipp-bin", default=BASE_DEFAULTS["sipp_bin"])
    parser.add_argument("--helm-bin", default=BASE_DEFAULTS["helm_bin"])
    parser.add_argument("--dry-run", action="store_true")
    parser.set_defaults(
        registration_scenario=BASE_DEFAULTS["registration_scenario"],
        registration_auth_expected=BASE_DEFAULTS["registration_auth_expected"],
        registration_username=BASE_DEFAULTS["registration_username"],
        registration_password=BASE_DEFAULTS["registration_password"],
        users=BASE_DEFAULTS["users"],
        run_call=BASE_DEFAULTS["run_call"],
        dtmf_expected=BASE_DEFAULTS["dtmf_expected"],
    )
    args = parser.parse_args()
    if args.list_profiles:
        print_profiles()
        return 0
    apply_profile(args, parser)
    args.ladder_enabled = args.ladder if args.ladder is not None else (args.calls == 1 and args.rate == 1)
    args.media_enabled = bool(args.media_codec)
    args.media_pcap = args.media_pcap or (MEDIA_PCAPS[args.media_codec] if args.media_codec else "")
    args.server_codec = args.server_codec or args.media_codec or "PCMU"

    run_prefix = args.profile or ("b2bua-media" if args.media_enabled else "b2bua-signalling")
    run_id = args.run_id or make_run_id(run_prefix)
    args.resolved_run_id = run_id
    log_dir, needs_create = resolve_log_dir(args, run_id)
    if needs_create:
        log_dir.mkdir(parents=True, exist_ok=True)
    initialize_log_dir(log_dir)

    if args.media_backend == "rtpengine" and not args.skip_rtpengine_preflight and not args.dry_run:
        started = time.monotonic()
        ready, detail = check_rtpengine_preflight(args.rtpengine_url, args.rtpengine_timeout)
        duration = time.monotonic() - started
        if not ready:
            result = SmokeResult("rtpengine-preflight", [], None, "blocked", duration)
            append_rtpengine_blocked_observations(log_dir, args, detail, duration)
            append_results(log_dir, args, [result])
            print(f"B2BUA SIPp logs: {log_dir}")
            print(f"{result.name}: {result.status}")
            return 2

    started = time.monotonic()
    sudo_ready, sudo_detail = check_sudo_ready_for_sipp_pcap(args)
    sudo_duration = time.monotonic() - started
    if not sudo_ready:
        result = SmokeResult("sipp-pcap-sudo-preflight", [], None, "blocked", sudo_duration)
        append_sipp_pcap_sudo_blocked_observations(log_dir, args, sudo_detail, sudo_duration)
        append_results(log_dir, args, [result])
        print(f"B2BUA SIPp logs: {log_dir}")
        print(f"{result.name}: {result.status}")
        return 2
    if should_sudo_sipp_pcap(args):
        append_log_section(
            log_dir,
            "log.platform",
            "SIPP PCAP SUDO PREFLIGHT OK",
            f"detail={sudo_detail}\nduration_seconds={sudo_duration:.3f}",
        )

    results: List[SmokeResult] = []
    with tempfile.TemporaryDirectory(prefix=f"{run_id}-work-") as work_tmp:
        work_dir = Path(work_tmp)
        for name in ("server", "registration-callee", "registration-caller", "sipp-a-uac", "sipp-b-uas"):
            (work_dir / name).mkdir()
        prepare_registration_scenario(args, work_dir)
        prepare_media_scenarios(args, work_dir)
        prepare_transport_scenario(args, work_dir)

        sipp_binary = resolve_binary(args.sipp_bin)
        if not sipp_binary and not args.dry_run:
            raise SystemExit("SIPp executable not found")
        sipp = sipp_binary or args.sipp_bin
        server_command = build_server_command(args, work_dir, log_dir)
        uas_command = build_uas_command(args, sipp)
        uac_command = build_uac_command(args, sipp)
        media_commands = build_media_player_commands(args)
        callee_register_command = build_register_command(args, sipp, args.callee, args.uas_port, args.register_port)
        caller_register_command = (
            build_register_command(args, sipp, args.caller, args.uac_port, args.caller_register_port)
            if args.register_caller
            else []
        )
        all_commands = [("server", server_command)]
        if args.registration_driver == "sipp" and args.register_callee:
            all_commands.append(("registration-callee", callee_register_command))
        if args.start_uas:
            all_commands.append(("sipp-b-uas", uas_command))
        if args.registration_driver == "sipp" and caller_register_command:
            all_commands.append(("registration-caller", caller_register_command))
        if args.run_call:
            all_commands.append(("sipp-a-uac", uac_command))
            all_commands.extend(media_commands)

        server_process: Optional[subprocess.Popen] = None
        uas_process: Optional[subprocess.Popen] = None
        uas_started: Optional[float] = None
        media_processes: List[Tuple[str, List[str], subprocess.Popen, float]] = []
        live_captures: List[Tuple[str, List[str], subprocess.Popen]] = []
        try:
            if args.dry_run:
                results.append(SmokeResult("server", server_command, None, "dry-run", 0.0))
                if args.registration_driver == "sipp" and args.register_callee:
                    results.append(SmokeResult("registration-callee", callee_register_command, None, "dry-run", 0.0))
                if args.start_uas:
                    results.append(SmokeResult("sipp-b-uas", uas_command, None, "dry-run", 0.0))
                if args.registration_driver == "sipp" and caller_register_command:
                    results.append(SmokeResult("registration-caller", caller_register_command, None, "dry-run", 0.0))
                if args.run_call:
                    results.append(SmokeResult("sipp-a-uac", uac_command, None, "dry-run", 0.0))
                    for name, command in media_commands:
                        results.append(SmokeResult(name, command, None, "dry-run", 0.0))
                print(f"B2BUA SIPp logs: {log_dir}")
                for result in results:
                    print(f"{result.name}: {result.status}")
                return 0

            live_captures = start_live_captures(args, work_dir)
            all_commands.extend((name, command) for name, command, _process in live_captures)
            server_process = start_process(server_command, ROOT, work_dir / "server" / "stdout.log")
            time.sleep(0.75)
            if server_process.poll() is not None:
                raise RuntimeError(f"Mini call server exited early. See {log_dir / 'log.platform'}")

            started = time.monotonic()
            if args.register_callee:
                if args.registration_driver == "sipp":
                    registration_rc = run_sipp_registration(callee_register_command, work_dir, "registration-callee")
                else:
                    registration_rc = register_endpoint(args, log_dir)
                results.append(SmokeResult("registration", [], registration_rc, "passed" if registration_rc == 0 else "failed", time.monotonic() - started))

            if args.start_uas:
                uas_started = time.monotonic()
                uas_process = start_process(uas_command, work_dir / "sipp-b-uas", work_dir / "sipp-b-uas" / "stdout.log")
                time.sleep(0.75)

            if args.register_caller:
                started = time.monotonic()
                if args.registration_driver == "sipp":
                    caller_registration_rc = run_sipp_registration(caller_register_command, work_dir, "registration-caller")
                else:
                    caller_registration_rc = register_caller(args, log_dir)
                results.append(
                    SmokeResult(
                        "caller-registration",
                        [],
                        caller_registration_rc,
                        "passed" if caller_registration_rc == 0 else "failed",
                        time.monotonic() - started,
                    )
                )

            if args.run_call:
                started = time.monotonic()
                uac_stdout = (work_dir / "sipp-a-uac" / "stdout.log").open("w", encoding="utf-8")
                uac_stderr = (work_dir / "sipp-a-uac" / "stderr.log").open("w", encoding="utf-8")
                try:
                    uac_process = subprocess.Popen(uac_command, cwd=work_dir / "sipp-a-uac", stdout=uac_stdout, stderr=uac_stderr, text=True)
                    if media_commands:
                        time.sleep(args.media_start_delay)
                        for name, command in media_commands:
                            media_started = time.monotonic()
                            process = start_process(command, ROOT, work_dir / f"{name}.log")
                            media_processes.append((name, command, process, media_started))
                    if should_run_rtcp(args):
                        rtcp_commands = build_rtcp_sender_commands(args, work_dir)
                        all_commands.extend(rtcp_commands)
                        for name, command in rtcp_commands:
                            media_started = time.monotonic()
                            process = start_process(command, ROOT, work_dir / f"{name}.log")
                            media_processes.append((name, command, process, media_started))
                    uac_rc = uac_process.wait()
                finally:
                    uac_stdout.close()
                    uac_stderr.close()
                results.append(SmokeResult("sipp-a-uac", uac_command, uac_rc, "passed" if uac_rc == 0 else "failed", time.monotonic() - started))

            for name, command, process, media_started in media_processes:
                try:
                    media_rc = process.wait(timeout=max(5, int(args.hold_ms / 1000) + 10))
                except subprocess.TimeoutExpired:
                    stop_process(process)
                    media_rc = process.returncode if process.returncode is not None else 1
                results.append(SmokeResult(name, command, media_rc, "passed" if media_rc == 0 else "failed", time.monotonic() - media_started))
            media_processes = []

            if uas_process is not None:
                uas_rc = uas_process.wait(timeout=max(30, int(args.hold_ms / 1000) + 30))
                uas_duration = time.monotonic() - uas_started if uas_started is not None else 0.0
                results.append(SmokeResult("sipp-b-uas", uas_command, uas_rc, "passed" if uas_rc == 0 else "failed", uas_duration))
                uas_process = None
            if args.profile == "load-5cps-60s-rtpengine-transcoding":
                observed_queries, drain_duration = wait_for_rtpengine_load_queries(log_dir, args.calls)
                append_log_section(
                    log_dir,
                    "log.platform",
                    "RTPENGINE LOAD QUERY DRAIN",
                    (
                        f"expected_queries={args.calls} observed_query_results={observed_queries} "
                        f"duration_seconds={drain_duration:.3f}"
                    ),
                )
        finally:
            for _name, _command, process, _started in media_processes:
                stop_process(process)
            stop_process(uas_process)
            stop_process(server_process)
            stop_live_captures(live_captures)
            append_commands(log_dir, all_commands)
            collect_work_logs(log_dir, work_dir, args)
            append_registration_auth_observation(log_dir, work_dir, args, results)
            remote_target_errors = dialog_remote_target_errors(work_dir)
            if remote_target_errors:
                append_log_section(log_dir, "log.sip", "DIALOG REMOTE TARGET FAILED", "\n".join(remote_target_errors))
                results.append(SmokeResult("sip-dialog-remote-target", [], 1, "failed", 0.0))
            append_registration_ladders(log_dir, args, results)
            append_media_observation(log_dir, args)
            if not append_rtcp_observation(log_dir, work_dir, args, results):
                results.append(SmokeResult("rtcp-validation", [], 1, "failed", 0.0))
            append_dtmf_observation(log_dir, args, results)
            append_transcoding_observation(log_dir, args)
            if not rtpengine_load_media_complete(log_dir, args):
                results.append(SmokeResult("rtpengine-load-completeness", [], 1, "failed", 0.0))
            pcap_artifacts = generate_pcap_artifacts(log_dir, work_dir, args)
            if should_capture_live_pcap(args) and not pcap_artifacts:
                results.append(SmokeResult("live-pcap-validation", [], 1, "failed", 0.0))
            append_results(log_dir, args, results)

    print(f"B2BUA SIPp logs: {log_dir}")
    for result in results:
        print(f"{result.name}: {result.status}")
    failed = [result for result in results if result.status == "failed"]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
