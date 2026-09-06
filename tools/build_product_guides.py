#!/usr/bin/env python3
"""Build curated PDF and browser guides without changing standalone runbooks."""
from pathlib import Path
from build_product_guide_html import build as build_html
from build_product_guide_pdf import build as build_pdf

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "PRODUCT_GUIDE.md"
PDF = ROOT / "output/pdf/PlaySBC-v2.6.0-Product-Guide.pdf"
HTML = ROOT / "output/html/PlaySBC-v2.6.0-Product-Guide.html"

if __name__ == "__main__":
    build_pdf(SOURCE, PDF, "2.6.0")
    build_html(SOURCE, HTML, "2.6.0")
    print(PDF)
    print(HTML)
