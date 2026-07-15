# PlaySBC 1.2.0

Release date: 2026-07-15

PlaySBC 1.2.0 promotes the real AI voice gateway lab work into a GA-style feature release. The main addition is an end-to-end Rasa speech path: SIPp plays real G.711 speech, RTPengine remains the media anchor, PlaySBC extracts RTP audio to WAV, Vosk performs STT, Rasa returns the bot response, and Piper generates TTS WAV/RTP evidence.

## Highlights

- Added `ai-rasa-rtpengine-speech` to the full Kubernetes and local regression catalogs.
- Expanded the Kubernetes catalog to 49 B2BUA profiles, including the four Rasa profiles.
- Added real speech evidence for the Rasa lab:
  - caller speech G.711 PCAP input
  - decoded caller WAV
  - Vosk STT transcript
  - real Rasa REST response
  - Piper TTS WAV output
  - generated G.711 RTP prompt PCAP
- Improved AI ladders so reports show RTPengine, PlaySBC, Vosk STT, Rasa, and Piper TTS in a single ordered call flow.
- Added HTML report audio controls for the caller input WAV and Piper output WAV when speech evidence exists.
- Preserved the RASA-only Kubernetes output folder behavior under `logs/RASA-Regression`.

## Regression Profiles Added Or Promoted

- `ai-rasa-lab`
- `ai-rasa-rtpengine`
- `ai-rasa-real-lab`
- `ai-rasa-rtpengine-speech`

## Validation

Pre-release targeted validation completed:

- Python compile check for changed runtime/report modules.
- Focused unit coverage for:
  - Rasa speech profile engine boundaries
  - Rasa profile report names and ladders
  - HTML audio evidence playback controls
  - unified ladder rendering
- Latest RASA-only Kubernetes run before the release line passed after persistent-log evidence copyback.

The full 49-profile Kubernetes suite is intended to run immediately after release. Any post-release fixes on this line should use `1.2.1`, `1.2.2`, and so on.

## Release Assets

- `playsbc-1.2.0.tgz`: Helm chart package.
- `playsbc-1.2.0.tgz.sha256`: checksum for the Helm chart package.
- GitHub source code ZIP and TAR archives: generated automatically for tag `v1.2.0`.

## GHCR Images

The `v1.2.0` tag publishes:

- `ghcr.io/sudheerkumarvatrapu/playsbc:1.2.0`
- `ghcr.io/sudheerkumarvatrapu/playsbc:1.2`
- `ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine:1.2.0`
- `ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine:1.2`
- `ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.2.0`
- `ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.2`
- `ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.2.0`
- `ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.2`

## Kubernetes Regression Command

After the GitHub Actions image publish finishes:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 tools/run_k8s_regression_job.py \
  --all-profiles \
  --runner-image ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.2.0 \
  --sipp-image ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.2.0 \
  --playsbc-image ghcr.io/sudheerkumarvatrapu/playsbc:1.2.0 \
  --set-playsbc-image \
  --no-load-playsbc-image \
  --no-load-sipp-image \
  --kind-cluster playsbc
```

## Versioning

- `1.2.x` is the hotfix line for the real Rasa speech/STT/TTS gateway release.
- Patch fixes should increment through `1.2.1`, `1.2.2`, and later as needed.
- The next larger GA feature release should be `1.3.0`.
