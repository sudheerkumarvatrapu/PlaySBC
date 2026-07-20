# PlaySBC v1.3.3

PlaySBC `v1.3.3` is the Kubernetes active-active SBC lab release.

## Highlights

- Adds active-active Kubernetes topology for the full regression path.
- Runs PlaySBC as a stable StatefulSet with two replicas by default.
- Runs RTPengine as a paired StatefulSet with a headless service for deterministic node pairing.
- Adds shared HA registrar/dialog state PVC mounted at `/var/lib/playsbc/ha-state.sqlite3`.
- Adds optional Multus NetworkAttachmentDefinition templates for core `172.28.0.0/24` and peer `192.168.28.0/24` realm interfaces.
- Keeps kind/minikube compatible by defaulting Multus off and using logical dual-realm evidence over normal pod networking.
- Extends Grafana with active calls by node, HA shared state, drain status, and restore evidence.
- Updates Kubernetes regression so every profile defaults through the active-active PlaySBC/RTPengine topology.

## Regression

Validated locally:

```text
python3 -m unittest tests.test_mini_call_server
python3 -m unittest tests.test_sipp_harness
helm template playsbc charts/playsbc -f configs/kubernetes/kind-values.yaml -f configs/kubernetes/active-active-values.yaml
python3 tools/run_k8s_regression_job.py --all-profiles --dry-run --active-active-topology
```

## Deployment Note

Use `configs/kubernetes/active-active-values.yaml` with the release chart for the HA lab shape. In single-node kind, the shared state PVC uses `ReadWriteOnce`; for true multi-node clusters, use RWX storage or a later Redis/PostgreSQL shared-state backend.
