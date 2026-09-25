# PlayFabric Architecture

## Purpose

PlayFabric is the product umbrella for a modular communications platform.
PlaySBC provides the communications edge, while PlayConverse provides the AI
Voice Gateway and conversational AI layer. The products have separate
responsibilities and communicate through explicit platform interfaces.

The initial integrated lab uses one Kubernetes namespace named `playfabric`.
This gives engineering, sales, and customer teams one coherent environment in
which they can demonstrate the complete call and AI conversation path.

## Integrated Kubernetes Lab

```text
Namespace: playfabric

+-- PlaySBC
|   +-- playsbc-0
|   +-- playsbc-1
|
+-- RTPengine
|   +-- rtpengine-0
|   +-- rtpengine-1
|
+-- PlayConverse
|   +-- playconverse-xxxxx
|
+-- Optional AI services
|   +-- rasa-xxxxx
|   +-- local-stt-xxxxx
|   +-- local-tts-xxxxx
|
+-- Observability
    +-- prometheus-xxxxx
    +-- grafana-xxxxx
```

These groups are a logical product view. Kubernetes stores the workloads as
peer resources within the namespace and organizes them through workload names,
labels, Services, service accounts, and policies.

## Why One Namespace

The shared `playfabric` namespace is suitable for the integrated lab because it
provides:

- simple Kubernetes service discovery;
- direct, controlled pod-to-pod communication through stable Services;
- one Helm installation for an end-to-end platform demonstration;
- one regression environment covering SIP, media, AI, and observability;
- common logs, metrics, traces, dashboards, and evidence reports;
- fewer deployment prerequisites for sales and customer demonstrations; and
- a clear view of PlaySBC and PlayConverse as parts of PlayFabric.

Components must communicate through Kubernetes Services rather than individual
pod IP addresses. Example service relationships are:

```text
PlaySBC       -> playconverse:<session-port>
PlaySBC       -> rtpengine:<control-port>
PlayConverse  -> rasa:<api-port>
Prometheus    -> component metrics Services
```

Service addresses remain stable when pods restart or replicas scale. Kubernetes
also supports communication across namespaces, but the shared namespace keeps
the initial lab configuration and product demonstration easier to operate.

## Component Responsibilities

### PlaySBC

PlaySBC provides the communications edge and remains the owner of SIP dialogs.
Its responsibilities include:

- SIP signalling and routing;
- UDP, TCP, and TLS transport;
- RTP and SRTP policy;
- RTPengine integration and media anchoring;
- NAT traversal;
- SIP trunk and PSTN connectivity;
- Microsoft Teams connectivity;
- codec negotiation;
- security and topology hiding;
- high availability; and
- routing selected calls toward PlayConverse.

PlaySBC does not contain bot-specific prompts, intents, workflows, or
AI-provider-specific business logic.

### RTPengine

RTPengine provides media relay and media processing under PlaySBC control. Its
responsibilities can include RTP and RTCP anchoring, SRTP handling,
transcoding, endpoint learning, and media statistics.

### PlayConverse

PlayConverse provides the AI Voice Gateway. Its responsibilities include:

- voice session management;
- audio streaming;
- Voice Activity Detection;
- Speech-to-Text and Text-to-Speech orchestration;
- turn management;
- barge-in and interruption handling;
- conversation state;
- bot and agent selection;
- AI provider abstraction;
- Microsoft Agent Framework integration;
- Rasa and LLM integration;
- MCP and enterprise tool integration; and
- AI latency, quality, and session observability.

Conversation state belongs to PlayConverse or a dedicated state service. SIP
dialogs and registrations remain owned by PlaySBC.

### Optional AI Services

Rasa, local STT/TTS engines, and other AI workloads can be deployed only when a
profile needs them. PlayConverse accesses these systems through provider
adapters so that the telephony and conversation layers can evolve
independently.

### Observability

Prometheus and Grafana provide a shared view across PlaySBC, RTPengine,
PlayConverse, and optional AI services. A common call or trace identifier should
follow a session through every component so an operator can measure signalling,
media, STT, agent, tool, and TTS latency in one timeline.

## Microsoft Agent Framework Integration

Microsoft Agent Framework integration belongs behind a PlayConverse provider or
agent adapter. PlaySBC routes the call to PlayConverse without needing to know
which agent framework, model, prompt, or enterprise tool handles the
conversation.

```text
SIP, Teams, carrier, or PSTN endpoint
                  |
                  v
               PlaySBC
        SIP policy and call routing
                  |
                  +-------- RTPengine
                  |         media relay
                  v
             PlayConverse
       voice and conversation control
                  |
        +---------+---------+-------------+
        |                   |             |
        v                   v             v
 Microsoft Agent       Rasa adapter   LLM adapter
 Framework adapter                         |
        |                                  v
        +-------------------------- MCP and tools
```

This supports a concise customer message:

> PlaySBC provides the secure communications edge. PlayConverse connects
> real-time voice sessions to Microsoft Agent Framework, Rasa, LLMs, and
> enterprise tools.

## End-to-End Demonstration

A complete demonstration should show:

1. A SIP, Teams, carrier, or PSTN call reaches PlaySBC.
2. PlaySBC applies routing and security policy and selects PlayConverse.
3. RTPengine anchors the audio when the call profile requires it.
4. PlayConverse manages the voice session and streams speech to STT.
5. PlayConverse sends the recognized text and context to a Microsoft Agent
   Framework agent or another configured provider.
6. The agent invokes approved enterprise tools when required.
7. PlayConverse converts the response to speech and handles interruption or
   barge-in.
8. Audio returns to the caller while PlaySBC retains SIP dialog control.
9. Grafana presents call health and end-to-end turn latency.

## Integration Contract

PlaySBC and PlayConverse need a versioned integration contract that covers:

- call, tenant, and route identity;
- the selected bot, agent, or application;
- authentication and authorization context;
- audio transport, codec, sampling, and media endpoints;
- session start, update, transfer, and termination;
- DTMF, speech activity, interruption, and playback events;
- correlation identifiers for logs, metrics, and traces; and
- timeouts, retries, fallback routing, and failure behavior.

The contract should allow PlayConverse replicas to scale independently from
PlaySBC replicas.

## Deployment Independence

Even inside one namespace, every product should retain its own:

- Deployment or StatefulSet;
- Kubernetes Service;
- container image and version;
- ConfigMaps and Secrets;
- service account and RBAC permissions;
- NetworkPolicies;
- resource requests and limits;
- health and metrics endpoints;
- PodDisruptionBudget where required; and
- independently controlled Helm chart component.

This makes the integrated lab simple without coupling product release cycles.
It also preserves the option to use separate namespaces later for customers
that require stronger administrative isolation, quotas, ownership boundaries,
or independent upgrades.

## Decisions Required Before Implementation

1. Select the internal audio transport between RTPengine and PlayConverse.
2. Define the versioned PlaySBC-to-PlayConverse session API.
3. Define Microsoft Agent Framework authentication, session, streaming, and
   tool invocation adapters.
4. Specify behavior when PlayConverse, STT, TTS, or the selected agent is
   unavailable.
5. Define independent scaling measures for SIP traffic, concurrent voice
   sessions, and AI processing.
6. Define NetworkPolicies and per-component service accounts within the shared
   namespace.
7. Define one correlation model and latency budget across the complete call.

## Direction

Use the `playfabric` namespace for the integrated lab and initial platform
installation. Keep PlaySBC and PlayConverse independently deployable, connect
them through stable Kubernetes Services and a versioned contract, and place
Microsoft Agent Framework integration within PlayConverse. This provides a
clean customer demonstration today while preserving product and deployment
independence as the PlayFabric portfolio grows.
