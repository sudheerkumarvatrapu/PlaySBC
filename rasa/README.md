# PlaySBC Rasa Lab Bot

This is a tiny real Rasa project for PlaySBC AI Voice Gateway testing.

It is intentionally small:

- REST channel enabled through `credentials.yml`.
- NLU intents for `greet`, `support`, `sales`, `billing`, `agent`, `repeat`, and `confirm`.
- Bot responses include normal text and optional `custom.playsbc_action` payloads that PlaySBC can log as bot control actions.

Run with official Rasa:

```bash
cd rasa
rasa train --config config.yml --domain domain.yml --data data --out /tmp/playsbc-rasa-models
rasa run --enable-api --cors "*" --host 0.0.0.0 --port 5005 \
  --model /tmp/playsbc-rasa-models --credentials credentials.yml --endpoints endpoints.yml
```

Readiness check from the repo root:

```bash
python3 tools/check_rasa.py --url http://127.0.0.1:5005/webhooks/rest/webhook
```

Regression entry points:

- Local direct config: `configs/config.ai-rasa-real.example.yaml`
- Docker dual-realm profile: `ai-rasa-real-lab`
- Kubernetes values: `configs/kubernetes/ai-rasa-real-values.yaml`
