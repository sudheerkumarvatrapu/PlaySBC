# PlaySBC v3.0.0

PlaySBC v3.0.0 publishes the technical SBC and regression baseline under the
MIT license with 78 full Kubernetes regression profiles. The three smoke
profiles remain a separate per-build gate and are not counted in the 78.

## Added

- RFC 5359 unattended transfer with three independent SIPp endpoints.
- RFC 5359 unconditional, busy, and no-answer forwarding.
- Paired RTPengine profiles for all four new business-calling scenarios.
- Provider-neutral streamed AI response contracts and deterministic fallback.
- Network-side business-service policy foundations and operator configuration.

## Evidence and reporting

- Correct participant-aware ladders for transfer and forwarding scenarios.
- Ladder validation against `sipmsg.log` so reversed or invented messages fail.
- Browser-served evidence links for logs, JSON, audio, and PCAP metadata/downloads.
- Retained combined PCAP, SIP message log, media verdict, and report contract.

## Deployment

- Chart and all four public images use `3.0.0`.
- Local kind, minikube, real-device, and Azure AKS commands are maintained in
  the Product Guide and standalone runbooks.
- `docs/AKS.md` is restored as the canonical AKS create, deploy, regression,
  troubleshooting, evidence, and cleanup guide.

## Scope

This release provides a lab and interoperability baseline, not a production
certification or a claim that every RFC 5359 example is complete. The service
matrix and remaining real-device/AKS acceptance gates are documented in
`docs/BUSINESS_CALLING_SERVICES.md`.
