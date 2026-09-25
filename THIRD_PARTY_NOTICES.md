# Third-Party Notices

This file inventories externally supplied software and model assets used by
the PlaySBC v2.5.5 baseline. It does not replace the license text distributed
by each supplier. Before every commercial release, generate an SBOM, run a
license scan, preserve required attribution and source-offer material, and
have the result reviewed for the intended distribution model. The canonical
intake, vendor-contact, SBOM, legal-review, bundle, and release-gate procedure
is [the commercial compliance and release playbook](docs/COMMERCIAL_COMPLIANCE_PLAYBOOK.md).

| Component | Baseline use | Source or package | Release control |
| --- | --- | --- | --- |
| Python 3.12 and Debian Bookworm | Container runtime and base OS | Official `python:3.12-slim` and `debian:bookworm-slim` images | Pin immutable digests and retain image-package notices |
| RTPengine | Media relay | Debian `rtpengine-daemon` package | Record exact package version and applicable license/source obligations |
| SIPp 3.7.7 | SIP regression endpoint | `SIPp/sipp` tag `v3.7.7` | Preserve upstream notices and build dependency notices |
| Kubernetes CLI 1.30.7 | Regression orchestration | Official Kubernetes binary | Verify checksum and retain Kubernetes notices |
| Helm 3.15.4 | Chart deployment | Official Helm archive | Verify checksum and retain Helm notices |
| Piper TTS and voice model | Text-to-speech and bundled `en_US-lessac-low` voice | PyPI `piper-tts` and Piper voice catalog | Pin package/model versions; record each model card and license |
| Vosk and English model 0.15 | Speech recognition | PyPI `vosk` and `vosk-model-small-en-us-0.15` | Preserve model notice; keep the pinned SHA-256 provenance record |
| OpenSSL, libpcap, tcpdump, libnet, ncurses, CMake and system libraries | SIPp build/runtime and evidence capture | Debian packages | Include notices required by the exact installed package set |
| Rasa | Optional bot provider | `rasa/rasa` container or separately supplied service | Confirm edition and license before bundling or managed-service use |
| Prometheus | Optional observability | `prom/prometheus` container | Pin digest and retain upstream notices |
| Grafana | Optional observability | `grafana/grafana` container | Confirm edition, plugins, trademarks, and distribution terms |

The inventory must describe the actual release bundle. Adding a dependency or
model without updating this file and the release SBOM is a release-blocking
defect.
