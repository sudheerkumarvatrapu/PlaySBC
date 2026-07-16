#!/usr/bin/env python3
"""Generate G.711 speech PCAP fixtures for AI/Rasa regression."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_gateway.speech import lab_speech_pcm, pcm16_to_g711, read_wav_pcm, rtp_packet, write_rtp_pcap, write_wav  # noqa: E402


def generate(
    output_dir: Path,
    transcript: str,
    seconds: float,
    source_wav: str = "",
    source_label: str = "",
    prefix: str = "ai_rasa_speech",
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    fixture_source = source_label or "deterministic_lab_speech"
    if source_wav:
        pcm, sample_rate = read_wav_pcm(Path(source_wav), target_rate=8000)
        fixture_source = source_label or str(Path(source_wav))
    else:
        pcm = lab_speech_pcm(transcript, seconds=seconds)
    safe_prefix = "".join(character if character.isalnum() or character in {"_", "-"} else "_" for character in prefix)
    wav_path = output_dir / f"{safe_prefix}_prompt.wav"
    write_wav(wav_path, pcm)

    for codec, payload_type, name in (
        ("PCMU", 0, f"{safe_prefix}_g711u.pcap"),
        ("PCMA", 8, f"{safe_prefix}_g711a.pcap"),
    ):
        packets = []
        for index in range(0, len(pcm), 320):
            chunk = pcm[index : index + 320]
            if len(chunk) < 320:
                chunk = chunk + (b"\x00" * (320 - len(chunk)))
            packet_index = index // 320
            packets.append(
                rtp_packet(
                    pcm16_to_g711(chunk, codec),
                    payload_type,
                    22000 + packet_index,
                    64000 + (packet_index * 160),
                    0xA117A117,
                    marker=packet_index == 0,
                )
            )
        pcap_path = output_dir / name
        write_rtp_pcap(pcap_path, packets, src_ip="10.10.10.10", src_port=36000, dst_ip="10.10.10.40", dst_port=30000)
        pcap_path.with_suffix(pcap_path.suffix + ".txt").write_text(transcript + "\n", encoding="utf-8")
    wav_path.with_suffix(wav_path.suffix + ".txt").write_text(transcript + "\n", encoding="utf-8")
    (output_dir / f"{safe_prefix}_fixture.meta").write_text(
        "\n".join(
            [
                f"transcript={transcript}",
                f"source={fixture_source}",
                "codec_outputs=PCMU,PCMA",
                "sample_rate=8000",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate PlaySBC AI speech RTP PCAP fixtures")
    parser.add_argument("--output-dir", default=str(ROOT / "sipp" / "scenarios" / "pcap"))
    parser.add_argument("--transcript", default="I need support")
    parser.add_argument("--seconds", type=float, default=1.6)
    parser.add_argument("--source-wav", default="", help="Optional PCM16 WAV speech source; resampled/mixed to mono 8 kHz")
    parser.add_argument("--source-label", default="", help="Human-readable source label for fixture metadata")
    parser.add_argument("--prefix", default="ai_rasa_speech", help="Output filename prefix")
    args = parser.parse_args()
    generate(Path(args.output_dir), args.transcript, args.seconds, args.source_wav, args.source_label, args.prefix)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
