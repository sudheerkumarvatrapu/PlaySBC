# PlaySBC 1.2.2

Release date: 2026-07-16

PlaySBC 1.2.2 expands the AI/Rasa lab into a clearer end-to-end regression experience. It adds real Rasa chat/NLU regression coverage, RASA-only Kubernetes reports, chat-style HTML evidence, and sharper documentation for the AI voice gateway path.

## Highlights

- Added RASA-only Kubernetes regression coverage for:
  - `ai-rasa-chat-nlu`
  - `ai-rasa-chat-negative`
- Added direct Rasa NLU regression runner with:
  - positive chat intent matrix
  - negative chat / guardrail matrix
  - no-input guard handling
  - unsupported-language guard handling
  - webhook reply capture
- Added HTML report support for:
  - RASA Test Section
  - complete E2E flow cards for all seven RASA-only profiles
  - chat-window evidence
  - NLP Chat/Rasa ladder diagrams
  - audio evidence only for speech profiles
- Improved the Rasa lab bot data with support, sales, billing, agent, repeat, confirm, deny, clarify, language-limitation, safe-continuation, and fallback intents.
- Updated `docs/AI_VOICE_GATEWAY.md` with a shorter, clearer RASA-focused guide.
- Updated Kubernetes regression runner image contents so chat case files are available in-cluster.
- Pinned the bundled Vosk model checksum in the PlaySBC container image build so the release remains reproducible while the upstream model host has an expired TLS certificate.

## RASA Profiles Covered

- `ai-rasa-lab`: mock Rasa REST AI route sanity.
- `ai-rasa-rtpengine`: mock Rasa with RTPengine media anchoring.
- `ai-rasa-real-lab`: real Rasa pod deploy/train/webhook path.
- `ai-rasa-rtpengine-speech`: real G.711 speech, Vosk STT, real Rasa, Piper TTS.
- `ai-rasa-contact-center-sales`: virtual SIPp B contact-center sales bot-agent call.
- `ai-rasa-chat-nlu`: positive chat intent matrix.
- `ai-rasa-chat-negative`: negative chat and guardrail coverage.

## Validation

- Kubernetes RASA-only regression passed: 7/7 profiles.
- Chat/NLU validation passed: 20/20 chat cases.
- `helm template playsbc charts/playsbc`
- `python3 -m unittest discover tests`

## Release Assets

- `playsbc-1.2.2.tgz`: Helm chart package.
- `playsbc-1.2.2.tgz.sha256`: checksum for the Helm chart package.
- GitHub source code ZIP and TAR archives: generated automatically for tag `v1.2.2`.

## GHCR Images

The `v1.2.2` tag publishes:

- `ghcr.io/sudheerkumarvatrapu/playsbc:1.2.2`
- `ghcr.io/sudheerkumarvatrapu/playsbc:1.2`
- `ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine:1.2.2`
- `ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine:1.2`
- `ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.2.2`
- `ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.2`
- `ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.2.2`
- `ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.2`

## Upgrade Notes

- Use the `v1.2.2` Helm package with the `1.2.2` image tags for published-image Kubernetes testing.
- Use `tools/run_k8s_regression_job.py --rasa-profiles` for the focused AI/Rasa suite.
- Use `tools/run_k8s_regression_job.py --all-profiles` for the full 52-profile Kubernetes suite.
