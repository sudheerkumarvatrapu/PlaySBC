#!/usr/bin/env python3
"""Build the browser edition of the PlaySBC product guide."""

from __future__ import annotations

import argparse
import html
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "docs" / "PRODUCT_GUIDE.md"
DEFAULT_OUTPUT = ROOT / "output" / "html" / "PlaySBC-v3.0.0-Product-Guide.html"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def inline(text: str) -> str:
    value = html.escape(text.strip())
    value = re.sub(r"`([^`]+)`", r"<code>\1</code>", value)
    value = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", value)
    value = re.sub(r"\[([^]]+)]\(([^)]+)\)", r'<a href="\2">\1</a>', value)
    value = re.sub(r"&lt;(https?://[^&]+)&gt;", r'<a href="\1">\1</a>', value)
    return value


def render_markdown(source: str) -> tuple[str, str]:
    lines = source.splitlines()
    body: list[str] = []
    toc: list[str] = []
    paragraph: list[str] = []
    in_list = False
    index = 0

    def flush_paragraph():
        if paragraph:
            body.append(f"<p>{inline(' '.join(paragraph))}</p>")
            paragraph.clear()

    def close_list():
        nonlocal in_list
        if in_list:
            body.append("</ul>")
            in_list = False

    while index < len(lines):
        raw = lines[index]
        stripped = raw.strip()
        if stripped.startswith("```"):
            flush_paragraph(); close_list()
            index += 1
            code: list[str] = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code.append(lines[index]); index += 1
            value = html.escape("\n".join(code).rstrip())
            body.append(
                '<section class="command"><header><span>COMMAND</span>'
                '<button type="button" class="copy">COPY</button></header>'
                f'<pre><code>{value}</code></pre><p class="copy-status" aria-live="polite"></p></section>'
            )
        elif stripped.startswith("[["):
            flush_paragraph(); close_list()
        elif re.match(r"^#{1,3} ", stripped):
            flush_paragraph(); close_list()
            level = len(stripped) - len(stripped.lstrip("#"))
            title = stripped[level + 1:]
            anchor = slug(title)
            body.append(f'<h{level} id="{anchor}">{inline(title)}</h{level}>')
            if level <= 2:
                toc.append(f'<a class="toc-{level}" href="#{anchor}">{html.escape(title)}</a>')
        elif stripped.startswith("| "):
            flush_paragraph(); close_list()
            rows = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append([c.strip() for c in lines[index].strip().strip("|").split("|")])
                index += 1
            index -= 1
            if len(rows) > 1 and all(set(c) <= {"-", ":"} for c in rows[1]): rows.pop(1)
            body.append("<div class=table-wrap><table>")
            for row_number, row in enumerate(rows):
                tag = "th" if row_number == 0 else "td"
                body.append("<tr>" + "".join(f"<{tag}>{inline(c)}</{tag}>" for c in row) + "</tr>")
            body.append("</table></div>")
        elif re.match(r"^[-*] ", stripped):
            flush_paragraph()
            if not in_list: body.append("<ul>"); in_list = True
            body.append(f"<li>{inline(stripped[2:])}</li>")
        elif not stripped:
            flush_paragraph(); close_list()
        else:
            paragraph.append(stripped)
        index += 1
    flush_paragraph(); close_list()
    return "\n".join(body), "\n".join(toc)


def build(source_path: Path, output_path: Path, version: str = "3.0.0"):
    body, toc = render_markdown(source_path.read_text(encoding="utf-8"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PlaySBC v{version} Product Guide</title>
<style>
:root{{--navy:#16324f;--blue:#2166a5;--line:#c9d4dd;--pale:#f5f8fa;--ink:#202b33;--muted:#5a6975}}
*{{box-sizing:border-box}} body{{margin:0;color:var(--ink);font:16px/1.55 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#eef3f6}}
.layout{{display:grid;grid-template-columns:280px minmax(0,920px);gap:28px;max-width:1260px;margin:auto;padding:28px}}
nav{{position:sticky;top:20px;align-self:start;max-height:calc(100vh - 40px);overflow:auto;background:#fff;border:1px solid var(--line);border-radius:12px;padding:18px}}
nav h2{{margin-top:0}} nav a{{display:block;color:var(--blue);text-decoration:none;padding:4px 0}} nav .toc-2{{padding-left:13px;font-size:13px}}
main{{background:#fff;border:1px solid var(--line);border-radius:12px;padding:42px 52px;box-shadow:0 12px 35px #16324f12}}
h1{{color:var(--navy);border-bottom:2px solid var(--line);padding-bottom:10px;margin-top:54px}} h1:first-child{{margin-top:0}} h2{{color:var(--blue);margin-top:38px}} h3{{color:var(--navy)}}
code{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}} .command{{border:1px solid var(--line);border-left:4px solid var(--blue);border-radius:5px;margin:16px 0 22px;background:var(--pale);overflow:hidden}}
.command header{{display:flex;justify-content:space-between;align-items:center;padding:8px 12px;background:#e7eef4;color:var(--muted);font-size:12px;font-weight:700}}
.copy{{border:0;border-radius:5px;background:var(--blue);color:white;font-weight:750;padding:7px 14px;cursor:pointer}} .copy:focus-visible{{outline:3px solid #f5a623;outline-offset:2px}}
pre{{margin:0;padding:16px;overflow:auto;white-space:pre}} .copy-status{{height:0;margin:0;overflow:hidden}} .command.copied header{{background:#dff3e7}} .command.copied .copy{{background:#257548}}
.table-wrap{{overflow:auto;margin:16px 0 24px}} table{{border-collapse:collapse;width:100%;font-size:14px}} th{{background:var(--navy);color:white;text-align:left}} th,td{{border:1px solid var(--line);padding:8px;vertical-align:top}} tr:nth-child(even) td{{background:var(--pale)}}
.browser-note{{background:#eaf2f8;border-left:4px solid var(--blue);padding:12px 16px;margin-bottom:24px}}
@media(max-width:850px){{.layout{{display:block;padding:10px}}nav{{position:relative;max-height:none;margin-bottom:12px}}main{{padding:24px 18px}}}}
</style></head><body><div class="layout"><nav><h2>Contents</h2>{toc}</nav><main>
<div class="browser-note"><strong>Browser edition:</strong> use COPY on any command block. The PDF edition keeps commands selectable but cannot access the clipboard in Preview or browser PDF viewers.</div>
{body}</main></div>
<script>
async function copyText(button){{
  const card=button.closest('.command'), text=card.querySelector('pre code').innerText;
  let copied=false;
  try{{await navigator.clipboard.writeText(text);copied=true}}catch(error){{
    const area=document.createElement('textarea');area.value=text;area.setAttribute('readonly','');area.style.position='fixed';area.style.opacity='0';document.body.appendChild(area);area.select();copied=document.execCommand('copy');area.remove();
  }}
  button.textContent=copied?'COPIED':'SELECT';card.classList.toggle('copied',copied);card.querySelector('.copy-status').textContent=copied?'Command copied to clipboard':'Copy was blocked; select the command text manually';
  setTimeout(()=>{{button.textContent='COPY';card.classList.remove('copied')}},1600);
}}
document.addEventListener('click',event=>{{if(event.target.matches('.copy'))copyText(event.target)}});
</script></body></html>""", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--version", default="3.0.0")
    args = parser.parse_args(); build(args.source, args.output, args.version); print(args.output); return 0


if __name__ == "__main__": raise SystemExit(main())
