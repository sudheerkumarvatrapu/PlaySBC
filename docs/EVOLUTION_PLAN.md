# PlaySBC Evolution Plan

PlaySBC is an enterprise-style SIP/RTP experimentation lab today, not a production-certified SBC yet.

The long-term mission is serious: evolve PlaySBC from the current `v1.5.0` development line into a public-cloud production SBC line over future major releases, with Azure as the first priority cloud and AWS next. The target future state is a PlaySBC `v10.x.x` generation that can be validated for large-scale SIP gateway deployments such as hundreds of thousands of registered devices and thousands of concurrent calls.

That production path must be earned with benchmarks, security hardening, carrier-grade HA behavior, long soak runs, and cloud networking proof. Until those gates are met, PlaySBC should be described as a lab and regression platform, not a replacement for certified commercial SBCs.

## Implemented

### Signalling And Routing

- SIP over UDP/TCP/TLS; REGISTER, OPTIONS, INVITE, ACK, CANCEL, and BYE
- Digest registration, dialog/transaction state, registrar-backed routing
- Trunk groups, primary/secondary selection, hunt groups, route policies, E.164/header normalization, CAC, health state, and counters
- Active SIP OPTIONS trunk probing with failure thresholds and timed health recovery
- HA shared registrar/dialog/B2BUA leg state using a SQLite lab store, plus node-to-RTPengine pairing for active-active experiments
- HA node model with external-LB policy, per-node weights, runtime drain state, and `503 Node Draining` rejection for new calls
- PlaySBC pre-call, mid-call, and post-call pod-failover regression profiles with shared state restore evidence
- RTPengine pre-call failover and mid-call best-effort recovery profiles

### Media

- G.711u/G.711a RTP, internal transcoding, and RFC 4733 DTMF
- RTCP sender/receiver evidence and quality analytics for single calls; load profiles omit RTCP validation
- RTPengine anchoring, SDP rewrite, interface selection, transcoding, bidirectional SDES-SRTP/RTP interworking, and fault profiles
- RTCP receiver-report loss/jitter analytics for single calls

### AI Voice Gateway

```text
SIP caller -> PlaySBC AI route -> RTP/RTPengine media input -> STT/intent adapter -> Rasa REST -> TTS adapter
```

- Route policies can target `ai-gateway:<bot-name>`.
- STT/TTS provider boundaries exist for lab-scripted, Whisper, Vosk, text-only, Piper, and Coqui modes.
- Rasa REST supports multi-message responses; custom bot actions can request join, transfer, or release and are logged as control-plane actions.
- Regression includes `ai-rasa-lab`: SIPp A calls `ai-bot`, PlaySBC answers, sends a Rasa REST turn, logs `log.ai`, and captures SIP/RTP/HTTP evidence.
- Regression includes `ai-rasa-rtpengine`: RTP/RTCP is anchored by RTPengine while PlaySBC handles SIP/control and the Rasa turn.
- Optional real Rasa lab is wired for local config, Docker dual-realm, Helm, and Kubernetes via `ai-rasa-real-lab`.
- Current AI/Rasa regression includes `ai-rasa-rtpengine-speech`: SIPp plays real G.711 speech, PlaySBC decodes RTP to WAV, Vosk transcribes `i need support`, PlaySBC posts the transcript to real Rasa, Piper generates the bot-response WAV/RTP prompt, and RTP/RTCP stay anchored by RTPengine.
- Whisper STT is selectable through `ai-rasa-rtpengine-speech-whisper`, using the same RTPengine/WAV/Rasa/Piper path with the Whisper adapter boundary.
- Contact-center sales bot profile is wired as `ai-rasa-contact-center-sales`: SIPp A calls a virtual SIPp B bot agent, Vosk transcribes `connect me to sales`, real Rasa runs the sales workflow, Piper generates the bot-agent prompt, and RTP/RTCP stay anchored by RTPengine.
- Coqui TTS is selectable through `ai-rasa-contact-center-sales-coqui`, using the same contact-center sales bot flow with Coqui-generated prompt evidence.
- Long Rasa replies are covered by `ai-rasa-long-response-streaming`, where real Rasa long-response text is split into ordered TTS chunks with per-chunk WAV/RTP prompt evidence.
- Real Rasa project assets live under `rasa/`, with `tools/check_rasa.py` as the readiness gate.

### Lab Platform

- Dual-realm Docker topology: core `172.28.0.0/24`, peer `192.168.28.0/24`
- Dual-homed PlaySBC and RTPengine with Docker-based SIPp agents
- Helm-rendered configuration for every regression profile
- Every dual-realm regression profile runs with HA enabled by default
- SBC category logs, combined live PCAP, and Robot-style HTML report with unified ladders and AI speech WAV playback evidence
- Prometheus text-format `/metrics` endpoint with `HELP`, `TYPE`, and labels for node, realm, trunk, SIP requests/responses, RTPengine direction, negotiated codecs, transcoding, and AI providers
- Helm observability lab stack: Prometheus, Grafana, 31-day retention, PVC-backed storage, core/peer dashboard, scrape annotations, optional `ServiceMonitor`, and alert rules
- Signalling, media, auth, routing, negative, soak, and 5 cps / 60-second CHT profiles
- Kubernetes Helm lab with health probes, Secret-backed SIP users, RTPengine pairing, kind/minikube values, and a dialog-affinity experiment
- HA regression profiles: `ha-shared-state-rtpengine`, `ha-options-health-recovery`, `ha-node-draining`, `ha-playsbc-precall-failover`, `ha-playsbc-midcall-failover`, `ha-playsbc-postcall-failover`, `ha-rtpengine-precall-failover`, `ha-rtpengine-midcall-recovery`, `ha-node-drain-active-calls`, `ha-active-active-load-distribution`, and `ha-shared-registrar-dialog-restore`
- Kubernetes active-active lab mode: PlaySBC runs as a two-replica StatefulSet, RTPengine runs as a paired two-replica StatefulSet, `$POD_NAME` becomes the HA node identity, shared registrar/dialog state is mounted from a PVC, and all Kubernetes regression profiles default through this topology
- Optional Multus chart wiring: core `172.28.0.0/24` and peer `192.168.28.0/24` NetworkAttachmentDefinition templates and pod annotations are available, while kind remains logical dual-realm until Multus CRDs are installed

## Next

### Production Cloud SBC Track

Target direction:

- Azure-first deployment model for AKS, Azure Load Balancer, static public IPs, SIP UDP/TCP/TLS, RTP/SRTP media port ranges, private networking, firewall rules, and observability.
- AWS deployment model after Azure, covering EKS, NLB, static addresses, security groups, and media-port exposure.
- Scale target roadmap: 10k, 50k, 100k, then 300k registered devices; 250, 500, 1000, then 2500 concurrent calls.
- Replace SQLite lab HA state with production-grade shared state such as PostgreSQL, Redis, or another replicated store.
- Harden registrar, dialog, transaction, CDR, audit, and billing-grade event persistence.
- Add production SIP load-balancer and affinity model for UDP/TCP/TLS with health-based steering and controlled node draining.
- Add SIP flood, malformed-message, registration storm, OPTIONS storm, INVITE burst, and overload-control protection.
- Add TLS certificate lifecycle, secret rotation, SRTP/DTLS-SRTP hardening, and security policy controls.
- Add multi-AZ failure testing, pod/node/AZ failure simulation, and long-running soak jobs measured in days.
- Add capacity dashboards, alerting, release gates, and performance baselines for CPU, memory, packets per second, RTP sessions, registrations, dialogs, and call attempts per second.

Azure release track:

- `v1.4.2`: frozen local lab baseline for kind, minikube, local Docker regression, RTPengine, Rasa, Prometheus, and Grafana validation.
- `v1.4.3`: AKS Helm values, Azure public SIP LoadBalancer service, optional private SIP LoadBalancer service, static public IP annotations, lab media-port service wiring, observability defaults, and `docs/AZURE_AKS.md`.
- `v1.4.4`: AKS-specific regression/report evidence, `--aks-profiles`, Azure LoadBalancer validation for SIP UDP/TCP/TLS, TLS certificate lifecycle notes, per-exposure source CIDR hardening, and single-call media dataplane checks.
- `v1.5.0`: first Azure AKS public-cloud validation target with production-style reference architecture, dedicated node pools, RTP/SRTP range validation path, NSG/Azure Firewall guidance, external shared state planning, multi-zone failure planning, backup/restore planning, upgrade/rollback planning, and a three-hardphone registration/calling lab target. The same v1.5.0 chart must also stay kind-compatible through the local-image Kubernetes regression command.
- `v1.5.1`: Azure Cloud Shell playbook milestone. Document the validated free-account AKS path end to end: provider registration, ACR import, one PlaySBC pod, one RTPengine pod, public SIP/RTP LoadBalancers, AKS regression profiles, report download, and cleanup.
- `v1.5.2`: Azure Cloud Shell resilience milestone. Document the kube-credential refresh workaround for Azure CLI API-version mismatch, the need to re-export session variables, and immediate report/evidence download when Cloud Shell is ephemeral.
- `v1.5.3`: Azure Cloud Shell cleanup milestone. Document asynchronous `az group delete --no-wait` behavior, split resource-group deletion progress, and the final verification gate that both lab resource groups are gone.
- `v1.5.4`: Azure documentation cleanup milestone. Merge the Azure AKS and Cloud Shell playbook content into one shorter `docs/AZURE_AKS.md` guide with deploy, regression, report download, recovery, and cleanup in one flow.

Production-readiness gates:

- No critical SIP/RTP/HA caveats open for the target release line.
- Full Kubernetes regression passes on the cloud reference architecture.
- Load and soak profiles pass with packet, SIP, media, RTCP, CDR, and observability evidence.
- Security scans, dependency review, container scan, config scan, fuzz tests, and negative SIP tests pass.
- Documented operating model exists for deploy, upgrade, rollback, scale-out, drain, failover, backup, restore, and incident triage.

### Observability Lab

- Add direct RTPengine exporter support if the deployed RTPengine image exposes native counters.
- Add SIP signalling rate, profile, and regression-verdict labels where those can be measured without distorting call handling.
- Kubernetes regression profiles: `observability-prometheus-scrape`, `observability-grafana-dashboard`, and `observability-alert-rules`.
- Report evidence that Prometheus scraped PlaySBC after a B2BUA call and that Grafana dashboard JSON loads cleanly.

### HA And Networking

- Extend restored mid-call handling from ACK/BYE to CANCEL/re-INVITE and transfer flows.
- Promote RTPengine mid-call media-session migration from best-effort recovery to lossless continuity if Sipwise/session ownership support allows it.
- Multi-node chaos/failover regression that kills one PlaySBC/RTPengine pair during active SIP/RTP traffic.
- Kubernetes real dual-realm networking: install Multus or another multi-network CNI so SIPp, PlaySBC, and RTPengine can use real secondary interfaces, not only logical core/peer evidence over normal pod networking.
- External load balancer model for active-active SIP affinity, health-based draining, and per-node traffic steering.
- External shared state backend option such as Redis/PostgreSQL after the SQLite lab store proves the behavior.

### AI Voice Gateway

- Play generated TTS RTP back into the live SIP call through RTPengine, not only as report evidence.
- Package heavyweight Whisper and Coqui image variants with real models preloaded, beside the portable lab-fallback wrappers.
- Add streamed callback/channel support for bot responses that arrive over time rather than in one REST result.
- Add Rasa Action Server integration for tool-backed bot workflows.
- Add multi-turn contact-center bot calls with stateful sales, support, billing, repeat, confirm, deny, and agent-transfer paths.
- Add speech plus RFC 4733 DTMF hybrid IVR flows.
- Add interruption/barge-in handling for long streamed bot prompts.
- Expand RASA regression with multi-turn chat, long audio prompts, fallback recovery, and action-server verdicts.
- Add AI latency metrics for STT decode, Rasa request, TTS generation, streamed chunk duration, fallback count, and action count.

## Later

### WebRTC Gateway

- SIP WebSocket, ICE/STUN, DTLS-SRTP, and browser calling

### AI Voice Gateway

- Executed bot-assisted B2BUA actions: REFER/re-INVITE transfer, conference join, and release

## Delivery Rule

Every new feature needs focused unit tests, a dual-realm SIPp profile when applicable, clear SBC logs, combined packet evidence, and a report verdict.
