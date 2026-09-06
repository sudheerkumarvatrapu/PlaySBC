# PlaySBC Observability

The lab observability path is:

```text
PlaySBC /metrics -> Prometheus -> Grafana
```

PlaySBC also exports RTPengine and AI/Rasa state derived from its call-control evidence. Enable or upgrade the stack with the canonical command in [KUBERNETES_HELM_RUNBOOK.md](KUBERNETES_HELM_RUNBOOK.md).

## What Is Measured

| Area | Examples |
| --- | --- |
| Calls | active, admitted, completed, rejected, peak |
| SIP | requests and responses by realm, method, direction, status, and class |
| Media | negotiated codecs, transcoding intent, active RTPengine sessions, failures |
| HA | node health, drain state, shared registrations, shared dialogs |
| AI | STT, provider timeout/failure/interruption, Rasa, TTS, prompt, fallback, and bot-action counters |
| Business services | accepted/completed/recovered transfer and selected/redirected forwarding transitions |

Prometheus defaults to a short scrape interval for brief SIPp calls and 31-day retention. Persistence depends on a working cluster storage class.

## Open Grafana

```bash
kubectl -n playsbc port-forward svc/playsbc-playsbc-grafana 3000:3000
```

Open `http://127.0.0.1:3000` and use the lab credentials:

```text
user: admin
password: playsbc-lab
dashboard: PlaySBC Core/Peer SBC Lab
```

## Query Prometheus

```bash
kubectl -n playsbc port-forward svc/playsbc-playsbc-prometheus 9090:9090
```

Open `http://127.0.0.1:9090`. Useful queries:

```promql
sum(playsbc_active_calls)
sum(increase(playsbc_b2bua_calls_total[15m]))
sum(increase(playsbc_b2bua_calls_completed_total[15m]))
sum by (realm,method,direction) (increase(playsbc_sip_requests_total[15m]))
sum by (realm,status,status_class,direction) (increase(playsbc_sip_responses_total[15m]))
sum by (realm,trunk) (max_over_time(playsbc_trunk_healthy[15m]))
sum by (backend,inbound_codec,outbound_codec,transcoding) (increase(playsbc_media_negotiations_total[15m]))
sum by (backend,inbound_codec,outbound_codec) (increase(playsbc_transcoding_sessions_total[15m]))
sum by (from_realm,to_realm) (playsbc_rtpengine_media_sessions_active)
sum(increase(playsbc_rtpengine_control_failures_total[15m]))
sum by (cluster,node) (playsbc_ha_shared_registrations)
sum by (cluster,node) (playsbc_ha_shared_dialogs)
sum by (cluster,node) (playsbc_ha_node_draining)
sum by (bot,stt,tts) (increase(playsbc_ai_voice_turns_total[15m]))
sum(increase(playsbc_ai_rasa_failures_total[15m]))
sum by (provider) (increase(playsbc_ai_provider_timeouts_total[15m]))
sum by (provider) (increase(playsbc_ai_provider_failures_total[15m]))
sum by (provider) (increase(playsbc_ai_provider_interruptions_total[15m]))
sum by (service,outcome) (increase(playsbc_business_service_events_total[15m]))
```

Provider interruptions are call-control outcomes and are tracked separately
from provider failures. A caller ending an AI call should increase the
interruption counter without synthesizing fallback audio for that ended call.

## Interpret The Panels

- Counters reset when regression rolls PlaySBC. Use `increase(metric[window])`.
- `playsbc_active_calls` is a live gauge and should return to `0` after calls end.
- Range panels intentionally retain completed calls until the selected time window moves forward.
- Use `max_over_time` only for gauges such as peak calls or trunk health.
- A value such as `2.1` on a smoothed panel is a rate or average, not a fractional call.
- Grafana and Prometheus should match because Grafana queries Prometheus; compare the exact query, time range, job filter, and refresh interval.

## Direct Metrics Check

```bash
kubectl -n playsbc port-forward svc/playsbc-playsbc 8080:8080
curl http://127.0.0.1:8080/metrics
```

The endpoint returns Prometheus text with `# HELP`, `# TYPE`, and labels such as `cluster`, `node`, `realm`, `trunk`, `backend`, `inbound_codec`, `outbound_codec`, and `transcoding`.

## Optional Operator Resources

For clusters with Prometheus Operator CRDs:

```bash
helm upgrade playsbc charts/playsbc \
  --namespace playsbc \
  --reuse-values \
  --set observability.prometheus.serviceMonitor.enabled=true \
  --set observability.prometheus.rules.enabled=true
```

## Current Boundary

RTPengine does not expose native Prometheus metrics through this chart. PlaySBC reports RTPengine control failures, call-owned media sessions, codec negotiation, and transcoding intent from its own state. Packet-level truth remains in RTPengine query evidence and `capture.pcap`.
