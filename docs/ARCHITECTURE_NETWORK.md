# PlaySBC Service Network Diagrams

These diagrams describe the current PlaySBC lab architecture: SIP/B2BUA control, optional RTPengine media anchoring, Helm-rendered configuration, and SIPp regression testing.

PDF version: [PlaySBC_Service_Network_Diagrams.pdf](PlaySBC_Service_Network_Diagrams.pdf)

## Color Key

| Color | Direction |
| --- | --- |
| Blue | Configuration/rendering flow |
| Green | SIP signalling |
| Orange | Internal RTP/media path |
| Purple | RTPengine NG control |
| Red | RTPengine anchored RTP/media path |
| Gray | Logs, PCAP, and reports |

## Broad Platform View

This is the larger picture: PlaySBC is the SIP/B2BUA control point, while media can be handled internally for lab profiles or delegated to RTPengine for SBC-style anchoring.

```mermaid
flowchart LR
    subgraph Access["Access / Test Clients"]
        SippA["SIPp A<br/>caller / UAC"]
        RegA["Registered caller"]
        FuturePhone["Future SIP phone"]
        FutureWeb["Future WebRTC browser"]
    end

    subgraph PlaySBC["PlaySBC Control Plane"]
        Listener["SIP UDP/TCP Listener"]
        Auth["Digest Auth"]
        Registrar["Registrar"]
        Router["Routing Policies<br/>registrar / trunk / E.164"]
        B2BUA["B2BUA Dialog Manager"]
        Policy["Future Policy Engine"]
    end

    subgraph Media["Media Plane"]
        Internal["Internal RTP Relay<br/>current core profiles"]
        RTPE["RTPengine<br/>media anchor / transcoding"]
        FutureQoS["Future RTCP / QoS Metrics"]
    end

    subgraph PeerSide["Peer / Trunk Side"]
        SippB["SIPp B<br/>callee / UAS"]
        StaticTrunk["Static SIP Trunk"]
        E164["E.164 Route"]
        FutureCarrier["Future carrier / PBX trunk"]
    end

    subgraph Ops["Config / Observability"]
        Helm["Helm YAML Config"]
        Logs["SBC Logs"]
        Pcap["PCAP Evidence"]
        Report["HTML Regression Report"]
        FutureMetrics["Future Metrics Dashboard"]
    end

    SippA -->|SIP| Listener
    RegA -->|REGISTER / INVITE| Listener
    FuturePhone -.->|SIP TLS later| Listener
    FutureWeb -.->|SIP WebSocket later| Listener

    Listener --> Auth --> Registrar
    Listener --> B2BUA --> Router
    Router --> Registrar
    Router --> StaticTrunk
    Router --> E164
    Router -.-> FutureCarrier
    Policy -.-> Router

    B2BUA -->|internal media| Internal
    B2BUA -->|NG control| RTPE
    SippA -->|RTP| Internal
    Internal -->|RTP| SippB
    SippA ==>|RTP| RTPE
    RTPE ==>|RTP| SippB
    RTPE -.-> FutureQoS

    Helm --> Listener
    B2BUA --> Logs
    Internal --> Logs
    RTPE --> Logs
    Logs --> Pcap
    Logs --> Report
    FutureQoS -.-> FutureMetrics

    classDef access fill:#E0F2FE,stroke:#0369A1,color:#0C4A6E,stroke-width:2px
    classDef control fill:#DBEAFE,stroke:#1D4ED8,color:#1E3A8A,stroke-width:2px
    classDef media fill:#ECFDF5,stroke:#047857,color:#064E3B,stroke-width:2px
    classDef peer fill:#F5F3FF,stroke:#7C3AED,color:#3B0764,stroke-width:2px
    classDef ops fill:#F8FAFC,stroke:#64748B,color:#0F172A,stroke-width:2px
    classDef future fill:#FFF7ED,stroke:#F97316,color:#7C2D12,stroke-width:2px,stroke-dasharray:5 4

    class SippA,RegA access
    class FuturePhone,FutureWeb,Policy,FutureQoS,FutureCarrier,FutureMetrics future
    class Listener,Auth,Registrar,Router,B2BUA control
    class Internal,RTPE media
    class SippB,StaticTrunk,E164 peer
    class Helm,Logs,Pcap,Report ops
```

## High-Level Architecture

```mermaid
flowchart LR
    Dev["Developer Terminal"]
    Runner["Regression Runner<br/>tools/run_regression_suite.py"]
    Helm["Helm Template<br/>charts/playsbc"]
    Config["Per-profile server-config.yaml"]

    A["SIPp A<br/>UAC / registered caller<br/>SIP :25081<br/>RTP 36000-36200"]
    SBC["PlaySBC<br/>SIP B2BUA<br/>UDP/TCP :25062<br/>Registrar + Routing"]
    B["SIPp B<br/>UAS / registered callee<br/>SIP :25082<br/>RTP 27000-27200"]
    RTPE["RTPengine<br/>NG control :2223/udp<br/>RTP pool 30000-32000"]

    Logs["Log Bundle<br/>log.sip<br/>log.media<br/>log.transcoding<br/>capture.pcap"]
    Report["HTML Report<br/>logs/reports/latest.html"]

    Dev --> Runner
    Runner --> Helm
    Helm --> Config
    Config --> SBC

    A <-->|SIP INVITE / REGISTER / ACK / BYE| SBC
    SBC <-->|SIP outbound leg| B

    A -.->|Internal-media profiles: RTP| SBC
    SBC -.->|Internal-media profiles: RTP / transcoding| B

    SBC <-->|RTPengine offer / answer / query| RTPE
    A ==>|RTPengine profiles: RTP| RTPE
    RTPE ==>|RTPengine profiles: RTP| B

    SBC --> Logs
    Runner --> Logs
    Runner --> Report

    classDef endpoint fill:#ecfdf5,stroke:#16a34a,color:#064e3b,stroke-width:2px
    classDef sbc fill:#eff6ff,stroke:#2563eb,color:#1e3a8a,stroke-width:3px
    classDef rtpengine fill:#f5f3ff,stroke:#7c3aed,color:#3b0764,stroke-width:2px
    classDef config fill:#eff6ff,stroke:#0284c7,color:#0c4a6e,stroke-width:2px
    classDef observability fill:#f8fafc,stroke:#64748b,color:#0f172a,stroke-width:2px

    class A,B endpoint
    class SBC sbc
    class RTPE rtpengine
    class Dev,Runner,Helm,Config config
    class Logs,Report observability

    linkStyle 0,1,2,3 stroke:#0284c7,stroke-width:2px
    linkStyle 4,5 stroke:#16a34a,stroke-width:3px
    linkStyle 6,7 stroke:#f97316,stroke-width:3px,stroke-dasharray:6 4
    linkStyle 8 stroke:#7c3aed,stroke-width:3px
    linkStyle 9,10 stroke:#dc2626,stroke-width:4px
    linkStyle 11,12,13 stroke:#64748b,stroke-width:2px
```

## Low-Level Service Network

```mermaid
flowchart TB
    subgraph ConfigPlane["Configuration Plane"]
        Values["Helm values<br/>charts/playsbc/values.yaml"]
        Template["helm template"]
        RuntimeConfig["Runtime YAML<br/>server-config.yaml"]
    end

    subgraph SipPlane["SIP Control Plane - PlaySBC"]
        Listener["UDP/TCP SIP Listener<br/>:25062"]
        Parser["SIP Parser"]
        Txn["Transaction Cache<br/>retransmission handling"]
        Dialog["Dialog State<br/>Call-ID, tags, CSeq"]
        Registrar["Registrar<br/>REGISTER Contact store"]
        Router["Routing Engine<br/>registrar, static trunk, E.164 policy"]
        B2BUA["B2BUA Leg Manager<br/>A-leg <-> B-leg"]
    end

    subgraph MediaPlane["Media Plane"]
        InternalMedia["Internal RTP Relay<br/>G.711u / G.711a<br/>basic transcoding"]
        RtpengineClient["RTPengine NG Client<br/>offer / answer / delete / query"]
        Rtpengine["Sipwise RTPengine<br/>media anchor / relay / transcoding"]
    end

    subgraph Observability["Observability"]
        SipLog["log.sip<br/>SIP ladder"]
        MediaLog["log.media"]
        TransLog["log.transcoding"]
        PlatformLog["log.platform"]
        Pcap["capture.pcap<br/>single-call profiles"]
        Html["latest.html"]
    end

    Values --> Template --> RuntimeConfig --> Listener

    Listener --> Parser --> Txn --> Dialog --> B2BUA
    Parser --> Registrar
    B2BUA --> Router
    Router --> Registrar

    B2BUA --> InternalMedia
    B2BUA --> RtpengineClient --> Rtpengine

    Listener --> SipLog
    B2BUA --> SipLog
    InternalMedia --> MediaLog
    RtpengineClient --> MediaLog
    InternalMedia --> TransLog
    RtpengineClient --> TransLog
    B2BUA --> PlatformLog
    SipLog --> Pcap
    MediaLog --> Pcap
    PlatformLog --> Html

    classDef config fill:#eff6ff,stroke:#0284c7,color:#0c4a6e,stroke-width:2px
    classDef sip fill:#ecfdf5,stroke:#16a34a,color:#064e3b,stroke-width:2px
    classDef media fill:#fff7ed,stroke:#f97316,color:#7c2d12,stroke-width:2px
    classDef rtpengine fill:#f5f3ff,stroke:#7c3aed,color:#3b0764,stroke-width:2px
    classDef obs fill:#f8fafc,stroke:#64748b,color:#0f172a,stroke-width:2px

    class Values,Template,RuntimeConfig config
    class Listener,Parser,Txn,Dialog,Registrar,Router,B2BUA sip
    class InternalMedia media
    class RtpengineClient,Rtpengine rtpengine
    class SipLog,MediaLog,TransLog,PlatformLog,Pcap,Html obs

    linkStyle 0,1,2 stroke:#0284c7,stroke-width:2px
    linkStyle 3,4,5,6,7,8,9 stroke:#16a34a,stroke-width:3px
    linkStyle 10 stroke:#f97316,stroke-width:3px
    linkStyle 11,12 stroke:#7c3aed,stroke-width:3px
    linkStyle 13,14,15,16,17,18,19,20,21,22 stroke:#64748b,stroke-width:2px
```

## SIPp Regression Testing Network

Profiles run sequentially. Each profile gets its own Helm-rendered YAML config and one log bundle.

```mermaid
flowchart TD
    Start["Run local regression command"]
    Clean["Delete old passed / blocked bundles<br/>keep failed evidence when configured"]
    List["Build profile list<br/>--all-b2bua-profiles"]
    Profile["Next SIPp profile"]
    Render["Render Helm values<br/>into temporary server-config.yaml"]
    Preflight{"RTPengine profile?"}
    CheckRTPE["tools/check_rtpengine.py<br/>udp://127.0.0.1:2223"]
    Blocked["Mark profile BLOCKED<br/>if RTPengine is down"]
    StartServer["Start PlaySBC<br/>with profile config"]
    Register["Optional SIPp REGISTER<br/>callee / caller"]
    UAS["Start SIPp B UAS"]
    UAC["Run SIPp A UAC"]
    Media{"Media profile?"}
    PcapReplay["SIPp PCAP replay<br/>G.711u / G.711a"]
    Capture["Generate logs and optional capture.pcap"]
    Result["Write one testcase result<br/>PASS / FAIL / BLOCKED"]
    More{"More profiles?"}
    Report["Write latest HTML report"]

    Start --> Clean --> List --> Profile --> Render --> Preflight
    Preflight -- "yes" --> CheckRTPE
    CheckRTPE -- "not ready" --> Blocked --> Result
    CheckRTPE -- "ready" --> StartServer
    Preflight -- "no" --> StartServer
    StartServer --> Register --> UAS --> UAC --> Media
    Media -- "yes" --> PcapReplay --> Capture
    Media -- "no" --> Capture
    Capture --> Result --> More
    More -- "yes" --> Profile
    More -- "no" --> Report

    classDef runner fill:#eff6ff,stroke:#0284c7,color:#0c4a6e,stroke-width:2px
    classDef rtpe fill:#f5f3ff,stroke:#7c3aed,color:#3b0764,stroke-width:2px
    classDef blocked fill:#fef2f2,stroke:#dc2626,color:#7f1d1d,stroke-width:2px
    classDef sip fill:#ecfdf5,stroke:#16a34a,color:#064e3b,stroke-width:2px
    classDef media fill:#fff7ed,stroke:#f97316,color:#7c2d12,stroke-width:2px
    classDef report fill:#f8fafc,stroke:#64748b,color:#0f172a,stroke-width:2px

    class Start,Clean,List,Profile,Render runner
    class Preflight,CheckRTPE rtpe
    class Blocked blocked
    class StartServer,Register,UAS,UAC sip
    class Media,PcapReplay media
    class Capture,Result,More,Report report

    linkStyle 0,1,2,3,4 stroke:#0284c7,stroke-width:2px
    linkStyle 5,6,7,8 stroke:#7c3aed,stroke-width:2px
    linkStyle 9 stroke:#dc2626,stroke-width:3px
    linkStyle 10,11,12,13 stroke:#16a34a,stroke-width:3px
    linkStyle 14,15,16 stroke:#f97316,stroke-width:3px
    linkStyle 17,18,19,20,21 stroke:#64748b,stroke-width:2px
```

## Basic B2BUA Call Path

```mermaid
flowchart LR
    A["SIPp A"]
    SBC["PlaySBC B2BUA"]
    B["SIPp B"]
    Media["RTP Media Path<br/>Internal PlaySBC or RTPengine"]

    A -->|"01 INVITE"| SBC
    SBC -->|"02 100 Trying"| A
    SBC -->|"03 INVITE"| B
    B -->|"04 100 Trying"| SBC
    B -->|"05 180 Ringing"| SBC
    SBC -->|"06 180 Ringing"| A
    B -->|"07 200 OK"| SBC
    SBC -->|"08 200 OK"| A
    A -->|"09 ACK"| SBC
    SBC -->|"10 ACK"| B
    A ==>|"11 RTP"| Media
    Media ==>|"12 RTP"| B
    A -->|"13 BYE"| SBC
    SBC -->|"14 200 OK"| A
    SBC -->|"15 BYE"| B
    B -->|"16 200 OK"| SBC

    classDef endpoint fill:#ecfdf5,stroke:#16a34a,color:#064e3b,stroke-width:2px
    classDef sbc fill:#eff6ff,stroke:#2563eb,color:#1e3a8a,stroke-width:3px
    classDef media fill:#fff7ed,stroke:#f97316,color:#7c2d12,stroke-width:2px

    class A,B endpoint
    class SBC sbc
    class Media media

    linkStyle 0,2,8,9,12,14 stroke:#16a34a,stroke-width:3px
    linkStyle 1,3,4,5,6,7,13,15 stroke:#22c55e,stroke-width:2px
    linkStyle 10,11 stroke:#f97316,stroke-width:4px
```

## Network Roles

| Service | Role | Default Local Ports |
| --- | --- | --- |
| SIPp A | Caller / UAC / registered caller | SIP `25081`, RTP `36000-36200` |
| PlaySBC | SIP registrar, router, B2BUA, logs | SIP `25062`, internal RTP `25100-25400` |
| SIPp B | Callee / UAS / registered endpoint | SIP `25082`, RTP `27000-27200` |
| RTPengine | Optional media backend / anchor | NG control `2223/udp`, RTP `30000-32000` |
| Helm | Config renderer for local and Kubernetes lab | `helm template` |
| Regression runner | Sequential SIPp profile orchestration | `tools/run_regression_suite.py` |

## Logical Node Examples

| Logical Node | Meaning | Example |
| --- | --- | --- |
| Developer Terminal | Local shell used to start checks and regression | `python3 tools/run_regression_suite.py --all-b2bua-profiles` |
| Regression Runner | Orchestrates profiles sequentially | Starts PlaySBC, SIPp B, SIPp A, then writes result |
| Helm Template | Renders lab config without requiring Kubernetes | `helm template playsbc charts/playsbc` |
| Per-profile Config | Temporary YAML used by one profile only | `media_backend: rtpengine`, `sip_transport: tcp` |
| SIPp A | Caller side, usually UAC | Sends `INVITE`, `ACK`, `BYE`; may send RTP PCAP |
| PlaySBC | SIP registrar, router, B2BUA, and log owner | Receives A-leg INVITE and creates B-leg INVITE |
| SIPp B | Callee side, usually UAS | Sends `100 Trying`, `180 Ringing`, `200 OK` |
| RTPengine | Optional media anchor/backend | PlaySBC sends `offer`, `answer`, `query` on UDP `2223` |
| Registrar | Stores REGISTER contacts | `callee -> sip:callee@127.0.0.1:25082` |
| Routing Engine | Chooses outbound target | Registrar lookup, static trunk, or E.164 policy |
| Internal RTP Relay | PlaySBC-owned media path for core profiles | G.711u/G.711a relay and basic transcoding |
| Log Bundle | One folder per testcase | `log.sip`, `log.media`, `log.transcoding`, `capture.pcap` |
| HTML Report | Regression summary | `logs/reports/latest.html` |

## Media Path Rule

- Core B2BUA profiles use PlaySBC internal media handling.
- RTPengine profiles keep SIP signalling in PlaySBC but move RTP anchoring to RTPengine.
- Load profiles avoid SIP ladders and PCAP clutter.
- Single-call profiles may include SIP ladders and `capture.pcap`.

## Future Enhancement View

The next target is to grow PlaySBC from a local B2BUA regression lab into a broader SBC experimentation platform.

```mermaid
flowchart TB
    Current["Current PlaySBC<br/>UDP/TCP B2BUA, registrar routing,<br/>SIPp regression, RTPengine backend"]

    SipHardening["SIP Transport Hardening<br/>TLS, connection reuse,<br/>transport-specific route policy"]
    Esbc["ESBC Lab Features<br/>trunk groups, failover,<br/>header normalization, CAC"]
    MediaQuality["Media Quality<br/>RTCP, jitter/loss metrics,<br/>RTPengine health checks"]
    Kubernetes["Kubernetes Lab<br/>Docker image, Helm install,<br/>readiness/liveness, Secrets"]
    WebRTC["WebRTC Gateway<br/>SIP WebSocket, ICE/STUN,<br/>DTLS-SRTP"]
    AiVoice["AI Voice Gateway<br/>RTP -> STT -> LLM -> TTS -> RTP"]
    Observability["Observability<br/>metrics dashboard,<br/>regression trend reports"]

    Current --> SipHardening
    Current --> Esbc
    Current --> MediaQuality
    Current --> Kubernetes
    Current -.-> WebRTC
    Current -.-> AiVoice
    Current --> Observability

    classDef current fill:#DBEAFE,stroke:#1D4ED8,color:#1E3A8A,stroke-width:3px
    classDef near fill:#ECFDF5,stroke:#047857,color:#064E3B,stroke-width:2px
    classDef future fill:#FFF7ED,stroke:#F97316,color:#7C2D12,stroke-width:2px,stroke-dasharray:5 4
    classDef obs fill:#F8FAFC,stroke:#64748B,color:#0F172A,stroke-width:2px

    class Current current
    class SipHardening,Esbc,MediaQuality,Kubernetes near
    class WebRTC,AiVoice future
    class Observability obs
```
