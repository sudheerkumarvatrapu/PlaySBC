# PlaySBC Observability

PlaySBC can deploy a small lab observability stack in the same namespace as PlaySBC, RTPengine, and Rasa:

```text
PlaySBC /metrics -> Prometheus -> Grafana
       RTPengine evidence and AI/Rasa counters are exported by PlaySBC.
```

## What It Gives

- Prometheus pod scraping PlaySBC every `15s`.
- Grafana pod with a PlaySBC dashboard.
- Core/peer labels for trunk and RTPengine media views.
- AI Voice Gateway counters for STT, Rasa, TTS, bot actions, and RTP prompts.
- Prometheus retention set to `31d` by default.
- PersistentVolumeClaims for Prometheus and Grafana data when the cluster has a storage class.

## Enable It

```bash
helm upgrade --install playsbc charts/playsbc \
  --namespace playsbc \
  --create-namespace \
  --reuse-values \
  --set observability.enabled=true \
  --set observability.prometheus.retention=31d \
  --set observability.prometheus.persistence.size=5Gi \
  --set observability.grafana.persistence.size=2Gi
```

Expected pods:

```bash
kubectl -n playsbc get pods
```

You should see PlaySBC, RTPengine when enabled, Rasa when enabled, plus:

```text
playsbc-playsbc-prometheus-...
playsbc-playsbc-grafana-...
```

## Open Grafana

```bash
kubectl -n playsbc port-forward svc/playsbc-playsbc-grafana 3000:3000
```

Open:

```text
http://127.0.0.1:3000
```

Default lab login:

```text
user: admin
password: playsbc-lab
```

The dashboard is named:

```text
PlaySBC Core/Peer SBC Lab
```

## Query Prometheus Directly

```bash
kubectl -n playsbc port-forward svc/playsbc-playsbc-prometheus 9090:9090
```

Open:

```text
http://127.0.0.1:9090
```

Useful queries:

```promql
sum(playsbc_active_calls)
sum(increase(playsbc_b2bua_calls_total[15m]))
sum(increase(playsbc_b2bua_calls_completed_total[15m]))
sum by (realm,method,direction) (increase(playsbc_sip_requests_total[15m]))
sum by (status_class,direction) (increase(playsbc_sip_responses_total[15m]))
sum by (realm,trunk) (playsbc_trunk_healthy)
sum by (from_realm,to_realm) (playsbc_rtpengine_media_sessions_active)
sum(increase(playsbc_rtpengine_control_failures_total[15m]))
sum by (bot,stt,tts) (increase(playsbc_ai_voice_turns_total[15m]))
sum(increase(playsbc_ai_rasa_failures_total[15m]))
```

For fast SIPp regression calls, prefer the `increase(...[window])` counters. `playsbc_active_calls` is an instant gauge and can legitimately be `0` if Prometheus scrapes between short calls.

## Direct Metrics Check

```bash
kubectl -n playsbc port-forward svc/playsbc-playsbc 8080:8080
curl http://127.0.0.1:8080/metrics
```

The endpoint emits Prometheus text format with `# HELP`, `# TYPE`, and labels such as:

```text
cluster
node
realm
trunk
from_realm
to_realm
bot
stt
tts
```

## Notes

- RTPengine does not expose Prometheus metrics directly in this lab chart yet. PlaySBC exports RTPengine control failures and active RTPengine-backed media sessions from its own call state.
- Rasa is observed through PlaySBC AI counters for request, failure, STT, TTS, and bot-action evidence.
- For Prometheus Operator clusters, you can also enable `ServiceMonitor` and `PrometheusRule` objects:

```bash
helm upgrade playsbc charts/playsbc \
  --namespace playsbc \
  --reuse-values \
  --set observability.prometheus.serviceMonitor.enabled=true \
  --set observability.prometheus.rules.enabled=true
```
