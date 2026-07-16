# PlaySBC 1.2.1

Release date: 2026-07-16

PlaySBC 1.2.1 is a hotfix for the 1.2.x AI/Rasa speech release. It fixes HTML report playback for AI speech evidence when the report is opened from a browser or copied out of the Kubernetes runner pod.

## Fix

- Embedded small AI speech WAV evidence directly into the HTML report as `data:audio/wav;base64,...`.
- Kept the normal file path visible for review.
- Added an `Open WAV file` fallback link for each audio artifact.
- Added regression coverage so AI speech reports require embedded playable WAV sources.
- Regenerated the latest local `k8s-regression-20260715-225450` report with embedded caller and Piper output WAV players.

## Why

The 1.2.0 report generated correct relative paths to the WAV files, and those files existed locally. Some browser contexts still refuse or mishandle local relative media playback from the copied HTML report. Embedding the short WAV files directly makes the report self-contained for playback.

## Validation

- `python3 -m unittest tests.test_sipp_harness -k "regression_report_embeds_ai_speech_audio_players"`
- `python3 -m unittest tests.test_sipp_harness -k "regression_report_embeds_single_call_sip_ladder"`
- `PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 -m compileall tools/run_regression_suite.py`
- Existing completed Kubernetes report `k8s-regression-20260715-225450` regenerated locally with 2 embedded WAV players.

## Release Assets

- `playsbc-1.2.1.tgz`: Helm chart package.
- `playsbc-1.2.1.tgz.sha256`: checksum for the Helm chart package.
- GitHub source code ZIP and TAR archives: generated automatically for tag `v1.2.1`.

## GHCR Images

The `v1.2.1` tag publishes:

- `ghcr.io/sudheerkumarvatrapu/playsbc:1.2.1`
- `ghcr.io/sudheerkumarvatrapu/playsbc:1.2`
- `ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine:1.2.1`
- `ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine:1.2`
- `ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.2.1`
- `ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.2`
- `ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.2.1`
- `ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.2`

## Versioning

- `1.2.x` remains the hotfix line for the real Rasa speech/STT/TTS gateway release.
- Future patch fixes should continue with `1.2.2`, `1.2.3`, and later as needed.
