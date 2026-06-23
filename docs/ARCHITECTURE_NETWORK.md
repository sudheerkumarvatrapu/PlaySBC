# PlaySBC Service Network Diagrams

These diagrams describe the current PlaySBC lab architecture: SIP/B2BUA control, optional RTPengine media anchoring, Helm-rendered configuration, and SIPp regression testing.

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
```

## Basic B2BUA Call Path

```mermaid
sequenceDiagram
    participant A as SIPp A
    participant SBC as PlaySBC B2BUA
    participant B as SIPp B

    A->>SBC: INVITE
    SBC-->>A: 100 Trying
    SBC->>B: INVITE
    B-->>SBC: 100 Trying
    B-->>SBC: 180 Ringing
    SBC-->>A: 180 Ringing
    B-->>SBC: 200 OK
    SBC-->>A: 200 OK
    A->>SBC: ACK
    SBC->>B: ACK
    Note over A,B: RTP flows through PlaySBC internal media or RTPengine
    A->>SBC: BYE
    SBC-->>A: 200 OK
    SBC->>B: BYE
    B-->>SBC: 200 OK
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

## Media Path Rule

- Core B2BUA profiles use PlaySBC internal media handling.
- RTPengine profiles keep SIP signalling in PlaySBC but move RTP anchoring to RTPengine.
- Load profiles avoid SIP ladders and PCAP clutter.
- Single-call profiles may include SIP ladders and `capture.pcap`.
