#!/usr/bin/env python3
"""Generate the PlaySBC architecture PDF.

The PDF intentionally uses few arrows. The detailed flows are shown as
numbered stages and tables so the document remains readable in GitHub's
PDF preview and on smaller laptop screens.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.pdfbase.pdfdoc import PDFName
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "PlaySBC_Service_Network_Diagrams.pdf"
PAGE_W, PAGE_H = landscape(letter)


NAVY = colors.HexColor("#0F172A")
SLATE = colors.HexColor("#475569")
MUTED = colors.HexColor("#64748B")
LINE = colors.HexColor("#CBD5E1")
WHITE = colors.white

BLUE = colors.HexColor("#2563EB")
BLUE_LIGHT = colors.HexColor("#DBEAFE")
CYAN = colors.HexColor("#0284C7")
CYAN_LIGHT = colors.HexColor("#E0F2FE")
TEAL = colors.HexColor("#0F766E")
TEAL_LIGHT = colors.HexColor("#CCFBF1")
AMBER = colors.HexColor("#D97706")
AMBER_LIGHT = colors.HexColor("#FEF3C7")
VIOLET = colors.HexColor("#7C3AED")
VIOLET_LIGHT = colors.HexColor("#EDE9FE")
GREEN = colors.HexColor("#16A34A")
GREEN_LIGHT = colors.HexColor("#DCFCE7")
RED = colors.HexColor("#DC2626")
RED_LIGHT = colors.HexColor("#FEE2E2")
GRAY = colors.HexColor("#64748B")
GRAY_LIGHT = colors.HexColor("#F1F5F9")


PALETTE = [
    ("Config", CYAN),
    ("SIP control", BLUE),
    ("Routing", TEAL),
    ("Internal media", AMBER),
    ("RTPengine", VIOLET),
    ("Future", GREEN),
    ("Evidence", GRAY),
]


def wrap(text: str, max_width: float, font: str = "Helvetica", size: float = 8) -> List[str]:
    lines: List[str] = []
    for para in str(text).split("\n"):
        words = para.split()
        if not words:
            lines.append("")
            continue
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if stringWidth(candidate, font, size) <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
    return lines


class PdfDoc:
    def __init__(self, path: Path):
        self.path = path
        self.c = canvas.Canvas(str(path), pagesize=(PAGE_W, PAGE_H))
        self.c.setTitle("PlaySBC Service Network Architecture")
        self.c.setAuthor("PlaySBC")
        self.c._doc.Catalog.PageMode = PDFName("UseOutlines")
        self.page = 0

    def save(self) -> None:
        self.c.save()

    def new_page(self, title: str, subtitle: str) -> None:
        if self.page:
            self.c.showPage()
        self.page += 1
        bookmark_name = f"page-{self.page}"
        self.c.bookmarkPage(bookmark_name)
        self.c.addOutlineEntry(title, bookmark_name, level=0, closed=False)
        self.c.setFillColor(WHITE)
        self.c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
        self.c.setFillColor(NAVY)
        self.c.setFont("Helvetica-Bold", 20)
        self.c.drawString(36, PAGE_H - 42, title)
        self.c.setFillColor(SLATE)
        self.c.setFont("Helvetica", 9.2)
        self.c.drawString(36, PAGE_H - 58, subtitle)
        self.c.setStrokeColor(LINE)
        self.c.setLineWidth(1)
        self.c.line(36, PAGE_H - 70, PAGE_W - 36, PAGE_H - 70)
        self.c.setFillColor(SLATE)
        self.c.setFont("Helvetica", 8)
        self.c.drawRightString(PAGE_W - 36, 22, f"PlaySBC architecture - page {self.page}")

    def legend(self, x: float = 36, y: float = PAGE_H - 98) -> None:
        self.c.setFont("Helvetica-Bold", 7.2)
        self.c.setFillColor(NAVY)
        self.c.drawString(x, y + 16, "Color key")
        pos = x
        for label, color in PALETTE:
            self.c.setFillColor(color)
            self.c.roundRect(pos, y, 10, 8, 2, fill=1, stroke=0)
            self.c.setFillColor(NAVY)
            self.c.setFont("Helvetica", 6.8)
            self.c.drawString(pos + 14, y - 0.5, label)
            pos += stringWidth(label, "Helvetica", 6.8) + 28

    def card(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        title: str,
        body: str = "",
        fill=GRAY_LIGHT,
        stroke=GRAY,
        title_size: float = 8.4,
        body_size: float = 7.0,
        title_color=NAVY,
        dashed: bool = False,
    ) -> None:
        self.c.setFillColor(fill)
        self.c.setStrokeColor(stroke)
        self.c.setLineWidth(1.4)
        self.c.setDash([5, 3] if dashed else [])
        self.c.roundRect(x, y, w, h, 8, fill=1, stroke=1)
        self.c.setDash()

        title_lines = wrap(title, w - 16, "Helvetica-Bold", title_size)
        body_lines = wrap(body, w - 16, "Helvetica", body_size) if body else []

        if body_lines:
            ty = y + h - title_size - 8
            self.c.setFillColor(title_color)
            self.c.setFont("Helvetica-Bold", title_size)
            for line in title_lines[:2]:
                self.c.drawCentredString(x + w / 2, ty, line)
                ty -= title_size + 2
            ty -= 1
            self.c.setFillColor(colors.HexColor("#334155"))
            self.c.setFont("Helvetica", body_size)
            for line in body_lines[:7]:
                self.c.drawCentredString(x + w / 2, ty, line)
                ty -= body_size + 2
        else:
            total = len(title_lines) * (title_size + 2)
            ty = y + h / 2 + total / 2 - title_size
            self.c.setFillColor(title_color)
            self.c.setFont("Helvetica-Bold", title_size)
            for line in title_lines:
                self.c.drawCentredString(x + w / 2, ty, line)
                ty -= title_size + 2

    def note(self, x: float, y: float, w: float, h: float, text: str, stroke=GRAY, fill=GRAY_LIGHT) -> None:
        self.c.setFillColor(fill)
        self.c.setStrokeColor(stroke)
        self.c.setLineWidth(1)
        self.c.roundRect(x, y, w, h, 6, fill=1, stroke=1)
        self.c.setFillColor(colors.HexColor("#334155"))
        self.c.setFont("Helvetica-Oblique", 7.2)
        ty = y + h - 12
        for line in wrap(text, w - 14, "Helvetica-Oblique", 7.2)[:4]:
            self.c.drawString(x + 8, ty, line)
            ty -= 9

    def small_label(self, x: float, y: float, text: str, color=GRAY) -> None:
        self.c.setFillColor(color)
        self.c.setFont("Helvetica-Bold", 7.2)
        self.c.drawString(x, y, text)

    def line(self, x1: float, y1: float, x2: float, y2: float, color=GRAY, width: float = 1.6) -> None:
        self.c.setStrokeColor(color)
        self.c.setLineWidth(width)
        self.c.line(x1, y1, x2, y2)

    def arrow(self, x1: float, y1: float, x2: float, y2: float, color=GRAY, width: float = 1.8) -> None:
        self.line(x1, y1, x2, y2, color, width)
        size = 6 + width
        if abs(x2 - x1) >= abs(y2 - y1):
            direction = 1 if x2 >= x1 else -1
            points = [(x2, y2), (x2 - direction * size, y2 + size / 2), (x2 - direction * size, y2 - size / 2)]
        else:
            direction = 1 if y2 >= y1 else -1
            points = [(x2, y2), (x2 - size / 2, y2 - direction * size), (x2 + size / 2, y2 - direction * size)]
        path = self.c.beginPath()
        path.moveTo(*points[0])
        path.lineTo(*points[1])
        path.lineTo(*points[2])
        path.close()
        self.c.setFillColor(color)
        self.c.drawPath(path, fill=1, stroke=0)

    def table(
        self,
        x: float,
        y: float,
        widths: Sequence[float],
        headers: Sequence[str],
        rows: Sequence[Sequence[str]],
        accents: Sequence,
        font_size: float = 6.7,
        max_lines: int = 5,
    ) -> float:
        total_w = sum(widths)
        self.c.setFillColor(NAVY)
        self.c.roundRect(x, y - 22, total_w, 22, 5, fill=1, stroke=0)
        self.c.setFillColor(WHITE)
        self.c.setFont("Helvetica-Bold", 7.2)
        pos = x
        for w, header in zip(widths, headers):
            self.c.drawString(pos + 7, y - 14, header)
            pos += w
        y -= 22

        for idx, row in enumerate(rows):
            line_sets = [
                wrap(value, w - 12, "Helvetica-Bold" if col == 0 else "Helvetica", font_size)
                for col, (value, w) in enumerate(zip(row, widths))
            ]
            row_h = max(29, max(len(lines[:max_lines]) * (font_size + 2) for lines in line_sets) + 11)
            self.c.setFillColor(colors.HexColor("#F8FAFC") if idx % 2 else WHITE)
            self.c.rect(x, y - row_h, total_w, row_h, fill=1, stroke=0)
            self.c.setFillColor(accents[idx % len(accents)])
            self.c.rect(x, y - row_h, 4, row_h, fill=1, stroke=0)
            self.c.setStrokeColor(colors.HexColor("#E2E8F0"))
            self.c.line(x, y - row_h, x + total_w, y - row_h)

            pos = x
            for col, (w, lines) in enumerate(zip(widths, line_sets)):
                self.c.setFillColor(NAVY if col == 0 else colors.HexColor("#334155"))
                self.c.setFont("Helvetica-Bold" if col == 0 else "Helvetica", font_size)
                ty = y - 12
                for line in lines[:max_lines]:
                    self.c.drawString(pos + 8, ty, line)
                    ty -= font_size + 2
                pos += w
            y -= row_h
        return y

    def code_panel(self, x: float, y: float, w: float, title: str, code: str, color=BLUE) -> float:
        self.c.setFillColor(color)
        self.c.roundRect(x, y - 18, w, 18, 5, fill=1, stroke=0)
        self.c.setFillColor(WHITE)
        self.c.setFont("Helvetica-Bold", 7.2)
        self.c.drawString(x + 8, y - 12, title)
        lines = code.strip("\n").split("\n")
        h = len(lines) * 9 + 14
        self.c.setFillColor(colors.HexColor("#111827"))
        self.c.roundRect(x, y - 18 - h, w, h, 6, fill=1, stroke=0)
        self.c.setFillColor(colors.HexColor("#E5E7EB"))
        self.c.setFont("Courier", 6.7)
        ty = y - 32
        for line in lines:
            self.c.drawString(x + 8, ty, line[:92])
            ty -= 9
        return y - 18 - h - 12


def numbered_stage(
    doc: PdfDoc,
    num: int,
    x: float,
    y: float,
    w: float,
    h: float,
    title: str,
    body: str,
    fill,
    stroke,
    title_size: float = 8.0,
    body_size: float = 6.6,
) -> None:
    doc.card(x, y, w, h, title, body, fill, stroke, title_size=title_size, body_size=body_size)
    doc.c.setFillColor(stroke)
    doc.c.circle(x + 12, y + h - 12, 9, fill=1, stroke=0)
    doc.c.setFillColor(WHITE)
    doc.c.setFont("Helvetica-Bold", 7.0)
    doc.c.drawCentredString(x + 12, y + h - 15, str(num))


def page_cover(doc: PdfDoc) -> None:
    doc.new_page("PlaySBC Service Network Architecture", "Readable architecture pack for SIP, RTP, B2BUA, SIPp regression, and RTPengine experiments.")
    doc.legend()
    doc.c.setFillColor(NAVY)
    doc.c.setFont("Helvetica-Bold", 28)
    doc.c.drawString(52, 438, "PlaySBC")
    doc.c.setFont("Helvetica", 13)
    doc.c.setFillColor(SLATE)
    doc.c.drawString(54, 414, "Playful Session Border Controller: break SIP here, not in production.")
    doc.note(520, 424, 210, 54, "Professional warning: if a SIP ladder looks like abstract art, the PDF gets redesigned.", BLUE, BLUE_LIGHT)

    cards = [
        (54, 300, 190, 78, "Current Lab", "UDP/TCP SIP B2BUA, registrar routing, G.711 media, internal transcoding, SIPp regression.", BLUE_LIGHT, BLUE),
        (286, 300, 190, 78, "Media Backends", "Core profiles use PlaySBC internal RTP. RTPengine profiles anchor media externally.", AMBER_LIGHT, AMBER),
        (518, 300, 190, 78, "Evidence", "One bundle per testcase: SIP logs, media logs, transcoding logs, PCAP, and HTML report.", GRAY_LIGHT, GRAY),
        (54, 190, 190, 78, "Configuration", "Helm values render one temporary YAML config per SIPp profile.", CYAN_LIGHT, CYAN),
        (286, 190, 190, 78, "ESBC Direction", "Static trunks, E.164 policy, registration-backed routing, negative flows, and load tests.", TEAL_LIGHT, TEAL),
        (518, 190, 190, 78, "Future Work", "TLS, trunk failover, QoS metrics, Kubernetes lab, WebRTC gateway, and AI voice gateway.", GREEN_LIGHT, GREEN),
    ]
    for card in cards:
        doc.card(*card)
    doc.c.setFillColor(MUTED)
    doc.c.setFont("Helvetica", 8.5)
    doc.c.drawString(54, 126, "How to read this PDF: colored boxes show responsibility. Numbered flows show execution order. Tables explain every logical node.")
    doc.c.drawString(54, 110, "Zoom note: diagrams are vector-drawn, not screenshots. Use Preview/Chrome/GitHub zoom and the PDF outline for navigation.")


def page_contents(doc: PdfDoc) -> None:
    doc.new_page("Contents And Navigation", "Compact diagrams are split across pages so you can zoom into each section without arrow soup.")
    doc.legend()
    rows = [
        ("1", "Enterprise SBC Reference Model", "Product-style view: access edge, trust boundary, SBC core, media, peer edge, operations."),
        ("2", "Broad Platform View", "Layered big picture: access, PlaySBC control, media, peer/trunk, evidence."),
        ("3", "Current Local Service Network", "Default local ports and service responsibilities for SIPp regression."),
        ("4", "SIP Control Plane", "PlaySBC internal SIP path: listener, parser, dialog, B2BUA, routing."),
        ("5", "Media Plane Split", "Internal media vs RTPengine media backend with evidence expectations."),
        ("6", "SIPp Regression Flow", "Numbered pipeline with clean stages and no crossing arrows."),
        ("7", "Basic B2BUA Call Flow", "Readable ladder table for one SIPp A to PlaySBC to SIPp B call."),
        ("8", "Lab And Service Nodes", "Logical node explanations for external/lab services."),
        ("9", "PlaySBC Internals", "Logical node explanations for PlaySBC components."),
        ("10", "Config And Evidence Map", "YAML examples and log/PCAP/report meaning."),
        ("11", "Future Enhancement View", "Roadmap: TLS, ESBC features, QoS, Kubernetes, WebRTC, AI voice."),
    ]
    doc.table(54, 480, [48, 220, 420], ["#", "Section", "Why it exists"], rows, [CYAN, BLUE, AMBER, VIOLET, GRAY], font_size=6.9, max_lines=3)
    doc.note(78, 82, 640, 50, "Navigation tip: open the PDF outline/sidebar and jump by section. If something looks small, zoom in; the content is vector and should stay sharp.", BLUE, BLUE_LIGHT)


def page_enterprise_reference(doc: PdfDoc) -> None:
    doc.new_page("Enterprise SBC Reference Model", "Vendor-style architecture vocabulary, mapped to what PlaySBC implements today and where it is going.")
    doc.legend()
    doc.note(514, 500, 220, 42, "Design note: inspired by enterprise SBC product docs; this is PlaySBC's own lab model, not a vendor diagram copy.", BLUE, BLUE_LIGHT)

    # Trust-boundary style lanes.
    zones = [
        (42, 394, 128, 82, "Access Edge", "SIPp A, registered caller, future SIP phone/WebRTC client.", CYAN_LIGHT, CYAN),
        (190, 394, 128, 82, "Ingress Security", "Digest auth today; TLS, topology hiding, malformed SIP checks later.", GREEN_LIGHT, GREEN),
        (338, 394, 128, 82, "SBC Core", "B2BUA dialog state, transactions, routing, registrar, policy.", BLUE_LIGHT, BLUE),
        (486, 394, 128, 82, "Media Services", "Internal RTP today; RTPengine anchoring and transcoding experiments.", AMBER_LIGHT, AMBER),
        (634, 394, 112, 82, "Peer Edge", "SIPp B, static trunk, E.164 route, future PBX/carrier trunk.", VIOLET_LIGHT, VIOLET),
    ]
    for idx, (x, y, w, h, title, body, fill, stroke) in enumerate(zones, start=1):
        numbered_stage(doc, idx, x, y, w, h, title, body, fill, stroke)
        if idx < len(zones):
            doc.arrow(x + w, y + h / 2, zones[idx][0], y + h / 2, stroke)

    doc.card(64, 260, 190, 72, "Management Plane", "Helm YAML, local runtime config, future Kubernetes Secrets and probes.", GRAY_LIGHT, GRAY, 9, 7.2)
    doc.card(302, 260, 190, 72, "Observability Plane", "SBC logs, SIP ladder, media logs, PCAP, HTML regression report.", GRAY_LIGHT, GRAY, 9, 7.2)
    doc.card(540, 260, 190, 72, "Lab Automation Plane", "SIPp regression profiles, RTPengine preflight, pass/fail/blocked verdicts.", GRAY_LIGHT, GRAY, 9, 7.2)

    rows = [
        ("Product-doc block", "PlaySBC mapping today", "Future target"),
        ("Access interface", "SIPp UAC/UAS over UDP/TCP", "SIP TLS, WebRTC access"),
        ("Policy/routing", "registrar, static route, E.164 profile", "trunk groups, normalization, failover"),
        ("Media service", "internal RTP and RTPengine backend", "RTCP/QoS, stronger transcoding validation"),
        ("Operations", "logs, PCAP, latest.html", "metrics dashboard and trend reports"),
    ]
    doc.table(74, 200, [170, 250, 250], rows[0], rows[1:], [CYAN, TEAL, AMBER, GRAY], font_size=7.0, max_lines=3)
    doc.note(78, 214, 640, 36, "SBC-product architecture usually separates control, media, policy, and operations. PlaySBC now mirrors that mental model in lab form.", TEAL, TEAL_LIGHT)


def page_broad_picture(doc: PdfDoc) -> None:
    doc.new_page("2. Broad Platform View", "A cleaner big-picture view with layers instead of crossed arrows.")
    doc.legend()
    layers = [
        ("Access / Test Clients", "SIPp A, registered caller, future SIP phone, future WebRTC browser.", CYAN_LIGHT, CYAN),
        ("PlaySBC SIP Control Plane", "SIP listener, digest auth, registrar, route policy, dialog state, B2BUA leg manager.", BLUE_LIGHT, BLUE),
        ("Media Plane", "Internal RTP relay for core profiles, or RTPengine for media anchoring/transcoding experiments.", AMBER_LIGHT, AMBER),
        ("Peer / Trunk Side", "SIPp B, registered callee, static SIP trunk, E.164 route, future PBX/carrier trunk.", VIOLET_LIGHT, VIOLET),
        ("Config / Evidence", "Helm YAML config, logs, PCAP, latest HTML report, future metrics dashboard.", GRAY_LIGHT, GRAY),
    ]
    y = 422
    for idx, (title, body, fill, stroke) in enumerate(layers, start=1):
        numbered_stage(doc, idx, 62, y, 660, 52, title, body, fill, stroke)
        y -= 64
    doc.note(62, 92, 660, 48, "Core idea: PlaySBC owns SIP signalling, routing, dialog state, and evidence. RTPengine is optional and owns media relay/anchoring when selected.", TEAL, TEAL_LIGHT)


def page_service_network(doc: PdfDoc) -> None:
    doc.new_page("3. Current Local Service Network", "Default local ports and responsibilities used by the SIPp regression lab.")
    doc.legend()
    # Minimal arrows between clearly separated columns.
    columns = [
        (44, "SIPp A", "Caller / UAC\nSIP :25081\nRTP 36000-36200", CYAN_LIGHT, CYAN),
        (212, "PlaySBC", "B2BUA control\nSIP :25062\ninternal RTP 25100-25400", BLUE_LIGHT, BLUE),
        (398, "Media Backend", "Internal RTP or\nRTPengine NG :2223\nRTP 30000-32000", AMBER_LIGHT, AMBER),
        (584, "SIPp B", "Callee / UAS\nSIP :25082\nRTP 27000-27200", VIOLET_LIGHT, VIOLET),
    ]
    for x, title, body, fill, stroke in columns:
        doc.card(x, 340, 150, 100, title, body, fill, stroke, title_size=10, body_size=8)
    doc.arrow(194, 390, 212, 390, BLUE)
    doc.arrow(362, 390, 398, 390, AMBER)
    doc.arrow(548, 390, 584, 390, VIOLET)

    doc.card(70, 218, 170, 64, "Config Path", "Terminal -> regression runner -> helm template -> server-config.yaml", CYAN_LIGHT, CYAN)
    doc.card(312, 218, 170, 64, "Control Path", "REGISTER / INVITE / ACK / BYE always traverse PlaySBC.", BLUE_LIGHT, BLUE)
    doc.card(554, 218, 170, 64, "Evidence Path", "log.sip, log.media, log.transcoding, capture.pcap, latest.html", GRAY_LIGHT, GRAY)
    doc.note(64, 118, 660, 48, "Important split: SIP signalling always goes through PlaySBC. RTP uses PlaySBC internal media in core profiles and RTPengine in RTPengine profiles.", AMBER, AMBER_LIGHT)


def page_control_plane(doc: PdfDoc) -> None:
    doc.new_page("4. SIP Control Plane Inside PlaySBC", "The SIP path is shown as a straight processing chain, with side services explained below.")
    doc.legend()
    steps = [
        ("SIP Listener", "UDP/TCP :25062"),
        ("Parser", "method, headers, SDP"),
        ("Transaction Cache", "retransmits"),
        ("Dialog State", "Call-ID, tags, CSeq"),
        ("B2BUA Manager", "A-leg to B-leg"),
        ("Routing Engine", "registrar / trunk / E.164"),
    ]
    x = 42
    for idx, (title, body) in enumerate(steps, start=1):
        numbered_stage(doc, idx, x, 392, 108, 62, title, body, BLUE_LIGHT if idx < 6 else TEAL_LIGHT, BLUE if idx < 6 else TEAL)
        if idx < len(steps):
            doc.arrow(x + 108, 423, x + 124, 423, BLUE)
        x += 124
    rows = [
        ("Digest Auth", "Challenges REGISTER when users are configured.", "401 then REGISTER with Authorization."),
        ("Registrar", "Stores live Contact for registered users.", "callee -> sip:callee@127.0.0.1:25082"),
        ("Route Policies", "Select registrar, static trunk, or E.164 route.", "+1800* -> sip:{user}@127.0.0.1:25080"),
        ("Reject Unknown Routes", "Makes the lab behave more like an SBC.", "Unknown destination returns 404 instead of demo fallback."),
    ]
    doc.table(48, 302, [150, 320, 250], ["Side service", "Responsibility", "Example"], rows, [TEAL, TEAL, TEAL, RED], font_size=7.2)


def page_media_plane(doc: PdfDoc) -> None:
    doc.new_page("5. Media Plane Split", "Internal media and RTPengine media are separate choices, not the same path with a different label.")
    doc.legend()
    doc.card(54, 334, 310, 124, "Core B2BUA Profiles - Internal Media", "Used by basic-media and internal transcoding profiles. PlaySBC receives and relays RTP in the lab. This is useful for learning, packet inspection, and small controlled tests.", AMBER_LIGHT, AMBER, 10, 8)
    doc.card(428, 334, 310, 124, "RTPengine Profiles - External Media Backend", "Used by rtpengine, rtpengine-media, rtpengine-transcoding, and related load profiles. SIP remains in PlaySBC; RTP is anchored by RTPengine.", VIOLET_LIGHT, VIOLET, 10, 8)
    rows = [
        ("SIP control", "PlaySBC", "PlaySBC"),
        ("RTP packets", "Through PlaySBC internal RTP relay", "Through RTPengine media ports"),
        ("Transcoding owner", "PlaySBC internal logic", "RTPengine intent / RTPengine media backend"),
        ("Expected PlaySBC RTP counter", "Non-zero when media flows internally", "Zero, because media bypasses PlaySBC"),
        ("Best evidence", "log.media + capture.pcap", "RTPengine query + log.media + capture.pcap"),
    ]
    doc.table(78, 262, [170, 250, 250], ["Aspect", "Internal media", "RTPengine media"], rows, [AMBER, VIOLET], font_size=7.1)
    doc.note(78, 92, 670, 46, "Sarcastic but true: if PlaySBC RTP counters are zero in an RTPengine profile, that is usually good news. Media took the media path, not the scenic route.", VIOLET, VIOLET_LIGHT)


def page_regression_flow(doc: PdfDoc) -> None:
    doc.new_page("6. SIPp Regression Flow", "Actual runner behavior: suite-level orchestration calls a per-profile B2BUA runner.")
    doc.legend()
    def flow_lane(y: float, label: str, stages: Sequence[Tuple[str, object, object]], w: float, gap: float) -> None:
        doc.small_label(54, y + 78, label, NAVY)
        x = 54
        for idx, (title, fill, stroke) in enumerate(stages, start=1):
            numbered_stage(doc, idx, x, y, w, 58, title, "", fill, stroke, title_size=6.7)
            if idx < len(stages):
                doc.arrow(x + w, y + 29, x + w + gap, y + 29, stroke, 1.4)
            x += w + gap

    suite_stages = [
        ("Clean Bundles", CYAN_LIGHT, CYAN),
        ("Clean Reports", CYAN_LIGHT, CYAN),
        ("Sudo Keepalive", AMBER_LIGHT, AMBER),
        ("Pick Profile", CYAN_LIGHT, CYAN),
        ("RTPengine Gate", VIOLET_LIGHT, VIOLET),
        ("Run Profile", BLUE_LIGHT, BLUE),
        ("Write Report", GRAY_LIGHT, GRAY),
    ]
    profile_stages = [
        ("Log Bundle", GRAY_LIGHT, GRAY),
        ("Profile Gates", VIOLET_LIGHT, VIOLET),
        ("Helm YAML", CYAN_LIGHT, CYAN),
        ("Start PlaySBC", BLUE_LIGHT, BLUE),
        ("Register / UAS", TEAL_LIGHT, TEAL),
        ("UAC + Media", AMBER_LIGHT, AMBER),
        ("Logs + PCAP", GRAY_LIGHT, GRAY),
        ("Return Result", GRAY_LIGHT, GRAY),
    ]

    flow_lane(390, "SUITE RUNNER: tools/run_regression_suite.py", suite_stages, 84, 14)
    flow_lane(256, "PER-PROFILE RUNNER: tools/run_b2bua_sipp_smoke.py", profile_stages, 75, 11)

    rows = [
        ("Suite runner", "Cleans old passed/blocked B2BUA bundles, cleans old reports, starts sudo keepalive when PCAP replay needs it, loops selected profiles, gates RTPengine profiles, runs the profile command, then writes latest.html."),
        ("Profile runner", "Creates one log bundle, performs profile-specific gates, renders Helm values into server-config.yaml, starts PlaySBC, registers endpoints, starts SIPp B, runs SIPp A and media, collects logs/PCAP/results, then returns one profile verdict."),
        ("Important", "Helm config rendering happens inside the per-profile runner before PlaySBC starts. RTPengine can be gated at suite level and again inside the profile runner."),
    ]
    doc.table(
        74,
        192,
        [130, 560],
        ["Layer", "What actually happens"],
        rows,
        [CYAN, BLUE, VIOLET],
        font_size=6.9,
        max_lines=4,
    )


def page_call_flow(doc: PdfDoc) -> None:
    doc.new_page("7. Basic B2BUA Call Flow", "A compact ladder table is easier to read than diagonal arrows over the content.")
    rows = [
        ("01", "INVITE ->", "receive INVITE; send 100 Trying", ""),
        ("02", "", "create outbound INVITE ->", "receive INVITE"),
        ("03", "", "<- 100 Trying / 180 Ringing / 200 OK", "send provisional/final response"),
        ("04", "<- 180 Ringing / 200 OK", "relay response to A-leg", ""),
        ("05", "ACK ->", "receive ACK; forward ACK ->", "receive ACK"),
        ("06", "RTP", "internal media or RTPengine anchored path", "RTP"),
        ("07", "BYE ->", "200 OK to A; send BYE ->", "receive BYE"),
        ("08", "", "<- 200 OK", "send 200 OK"),
    ]
    doc.table(48, 474, [48, 170, 330, 170], ["Step", "SIPp A", "PlaySBC B2BUA", "SIPp B"], rows, [BLUE, TEAL, AMBER], font_size=7.4, max_lines=4)
    doc.note(64, 110, 660, 48, "Read it like a real SBC: PlaySBC terminates the A-leg and originates the B-leg. It is not merely a UDP pipe with confidence.", BLUE, BLUE_LIGHT)


def page_external_nodes(doc: PdfDoc) -> None:
    doc.new_page("8. Logical Node Catalog - Lab And Service Nodes", "Every logical node used in the diagrams, with its technical meaning and evidence.")
    rows = [
        ("Developer Terminal", "Human starts checks, RTPengine, sudo cache, and regression.", "Full local command from README.", "Terminal output"),
        ("Regression Runner", "Sequentially executes SIPp profiles.", "tools/run_regression_suite.py", "latest.html"),
        ("Helm Template", "Renders YAML config for local profile runs.", "helm template charts/playsbc", "server-config.yaml"),
        ("Per-profile Config", "One runtime YAML per profile.", "media_backend: rtpengine", "log.platform"),
        ("SIPp A", "Caller side / UAC / registered caller.", "INVITE to :25062", "log.sipp, capture.pcap"),
        ("SIPp B", "Callee side / UAS / registered endpoint.", "100/180/200 OK", "log.sipp, capture.pcap"),
        ("RTPengine", "Optional external media anchor/backend.", "NG UDP :2223", "log.media, query stats"),
        ("HTML Report", "One row per testcase/profile.", "logs/reports/latest.html", "PASS/FAIL/BLOCKED"),
    ]
    doc.table(36, 502, [130, 240, 180, 160], ["Node", "Meaning", "Example", "Evidence"], rows, [CYAN, GRAY, CYAN, CYAN, BLUE, VIOLET, VIOLET, GRAY], font_size=6.9)


def page_internal_nodes(doc: PdfDoc) -> None:
    doc.new_page("9. Logical Node Catalog - PlaySBC Internals", "The main internal components and what each is responsible for.")
    rows = [
        ("SIP Listener", "UDP/TCP socket entry point.", "REGISTER, OPTIONS, INVITE, ACK, CANCEL, BYE", "log.sip"),
        ("SIP Parser", "Extracts headers, body, Call-ID, CSeq, tags, Contact, SDP.", "m=audio from SDP", "log.sip"),
        ("Transaction Cache", "Handles retransmitted requests consistently.", "duplicate INVITE branch/CSeq", "retransmission profile"),
        ("Dialog State", "Tracks A-leg and B-leg identity.", "Call-ID, From tag, To tag", "log.call"),
        ("Digest Auth", "Challenges REGISTER when users exist.", "401 + Authorization", "registration ladder"),
        ("Registrar", "Stores live Contact routes.", "callee -> sip:callee@127.0.0.1:25082", "registered profiles"),
        ("Routing Engine", "Chooses registrar/static trunk/E.164/reject.", "+1800* policy", "log.platform"),
        ("B2BUA Manager", "Creates outbound B-leg and relays responses.", "A-leg INVITE -> B-leg INVITE", "log.sip ladder"),
        ("Internal RTP Relay", "Lab RTP path for core profiles.", "G.711u/G.711a", "log.media"),
        ("RTPengine Client", "Controls external media backend.", "offer, answer, query, delete", "log.media"),
    ]
    doc.table(36, 502, [125, 250, 205, 130], ["Internal node", "Responsibility", "Example", "Evidence"], rows, [BLUE, BLUE, BLUE, BLUE, TEAL, TEAL, TEAL, BLUE, AMBER, VIOLET], font_size=6.75)


def page_config_evidence(doc: PdfDoc) -> None:
    doc.new_page("10. Config And Evidence Map", "Where each important behavior is configured and where to prove it worked.")
    doc.code_panel(42, 500, 330, "Core B2BUA config", """sip_transport: udp
media_backend: internal
default_codec: PCMU
route_policies:
  - name: registered-endpoints
    match: "*"
    target: registration""", BLUE)
    doc.code_panel(420, 500, 330, "RTPengine config", """media_backend: rtpengine
rtpengine_url: udp://127.0.0.1:2223
rtpengine_timeout: 3.0
b2bua_ladder_logs: true
reject_unknown_routes: false""", VIOLET)
    rows = [
        ("log.sip", "SIP messages, ladder, A-leg/B-leg view"),
        ("log.media", "RTP observations, RTPengine offer/answer/query"),
        ("log.transcoding", "Codec direction and transcoding owner"),
        ("capture.pcap", "Single-call SIP/RTP packet evidence"),
        ("latest.html", "Regression result summary"),
    ]
    doc.table(92, 314, [180, 470], ["Evidence file", "What it proves"], rows, [GRAY], font_size=7.2)
    doc.note(92, 78, 600, 46, "Tiny bit of attitude, serious rule: YAML without logs is just a wish. PlaySBC tries to leave receipts.", GRAY, GRAY_LIGHT)


def page_future(doc: PdfDoc) -> None:
    doc.new_page("11. Future Enhancement View", "A broader architecture direction, still anchored in testability and evidence.")
    roadmap = [
        ("SIP Transport Hardening", "TLS, TCP connection reuse, transport-specific policies.", "Near"),
        ("ESBC Lab Features", "Trunk groups, failover, header normalization, CAC.", "Near"),
        ("Media Quality", "RTCP, jitter/loss, RTPengine health, QoS reporting.", "Near"),
        ("Kubernetes Lab", "Docker image, Helm install, probes, Secrets.", "Near"),
        ("WebRTC Gateway", "SIP WebSocket, ICE/STUN, DTLS-SRTP.", "Future"),
        ("AI Voice Gateway", "RTP -> STT -> LLM -> TTS -> RTP.", "Future"),
    ]
    x_positions = [54, 292, 530]
    y_positions = [370, 230]
    for idx, (item, (x, y)) in enumerate(zip(roadmap, [(x, y) for y in y_positions for x in x_positions]), start=1):
        title, body, status = item
        fill, stroke = (GREEN_LIGHT, GREEN) if status == "Near" else (AMBER_LIGHT, AMBER)
        numbered_stage(doc, idx, x, y, 190, 86, title, f"{body}\nStatus: {status}", fill, stroke)
    doc.note(78, 108, 640, 52, "Roadmap rule: if it cannot be configured, tested, logged, and explained, it is just a feature-shaped rumor.", GREEN, GREEN_LIGHT)


def build() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc = PdfDoc(OUTPUT)
    page_cover(doc)
    page_contents(doc)
    page_enterprise_reference(doc)
    page_broad_picture(doc)
    page_service_network(doc)
    page_control_plane(doc)
    page_media_plane(doc)
    page_regression_flow(doc)
    page_call_flow(doc)
    page_external_nodes(doc)
    page_internal_nodes(doc)
    page_config_evidence(doc)
    page_future(doc)
    doc.save()
    print(OUTPUT)


if __name__ == "__main__":
    build()
