# RFC 5359 Regression Profile Plan

This is the mandatory regression inventory for all RFC 5359 services. Each
row has an internal-media and RTPengine profile. A profile may enter the
runnable catalog only when it drives live SIP signaling and asserts the
listed outcome; placeholders and state-only tests are not accepted as live
evidence.

Every multi-party scenario uses three independent SIPp pods named A, B, and C.
The report must retain each pod's messages, one combined PCAP, a correct
three-column ladder, media verdicts, server logs, and RTPengine control/session
evidence for the `-rtpengine` variant. Real phones and softphones must pass the
same standards-based flow before the service is closed.

| RFC service | Internal-media profile | RTPengine profile | Three-node assertion |
| --- | --- | --- | --- |
| 2.1 Call Hold | `rfc5359-call-hold-resume` | `rfc5359-call-hold-resume-rtpengine` | A/B call plus independent C observer/negative-route role; hold interval and restored media |
| 2.2 Consultation Hold | `rfc5359-consultation-hold` | `rfc5359-consultation-hold-rtpengine` | A-B original dialog and A-C consultation remain distinct |
| 2.3 Music on Hold | `rfc5359-music-on-hold` | `rfc5359-music-on-hold-rtpengine` | A-B call while C is the controlled music source |
| 2.4 Unattended Transfer | `rfc5359-unattended-transfer` | `rfc5359-unattended-transfer-rtpengine` | A transfers B to C; REFER/NOTIFY and B-C media pass |
| 2.5 Attended Transfer | `rfc5359-attended-transfer` | `rfc5359-attended-transfer-rtpengine` | A-B plus A-C consultation, mapped Replaces, then B-C media |
| 2.6 IM Transfer | `rfc5359-im-transfer` | `rfc5359-im-transfer-rtpengine` | Reserved pending product decision; never silently omitted |
| 2.7 Unconditional Forwarding | `rfc5359-unconditional-forwarding` | `rfc5359-unconditional-forwarding-rtpengine` | A calls B policy identity and reaches independent C |
| 2.8 Forwarding Busy | `rfc5359-forwarding-on-busy` | `rfc5359-forwarding-on-busy-rtpengine` | B returns busy and policy redirects A to C |
| 2.9 Forwarding No Answer | `rfc5359-forwarding-on-no-answer` | `rfc5359-forwarding-on-no-answer-rtpengine` | B rings, is cancelled, and A reaches C after the timer |
| 2.10 Conference: Party Added | `rfc5359-conference-party-added` | `rfc5359-conference-party-added-rtpengine` | A-B call, C added, all three media contributions verified |
| 2.11 Conference: Party Joins | `rfc5359-conference-party-joins` | `rfc5359-conference-party-joins-rtpengine` | A/B established and C joins the focus; all participant legs verified |
| 2.12 Find-Me | `rfc5359-find-me` | `rfc5359-find-me-rtpengine` | A calls identity B; B attempt and independent C fallback/fork are proven |
| 2.13 Incoming Screening | `rfc5359-incoming-call-screening` | `rfc5359-incoming-call-screening-rtpengine` | A blocked/allowed toward B; C proves unrelated policy isolation |
| 2.14 Outgoing Screening | `rfc5359-outgoing-call-screening` | `rfc5359-outgoing-call-screening-rtpengine` | A destination policy toward B; C proves unrelated policy isolation |
| 2.15 Call Park | `rfc5359-call-park` | `rfc5359-call-park-rtpengine` | A parks B and independent C retrieves B from the assigned slot |
| 2.16 Call Pickup | `rfc5359-call-pickup` | `rfc5359-call-pickup-rtpengine` | A calls B and C picks up B's ringing dialog with race checks |
| 2.17 Automatic Redial | `rfc5359-automatic-redial` | `rfc5359-automatic-redial-rtpengine` | B is initially busy, retry is bounded, and C validates isolation/overload behavior |
| 2.18 Click to Dial | `rfc5359-click-to-dial` | `rfc5359-click-to-dial-rtpengine` | Authenticated controller originates A and B legs; C validates authorization isolation |

For reject-before-answer screening cases, the RTPengine variant must prove that
no media session is allocated. For every established call it must prove the
expected offer/answer/delete lifecycle and zero leaked sessions after teardown.
