"""Render research markdown reports to standalone HTML.
Wing: code | Topic: render_report | Updated: 2026-10-30 21:05
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import markdown
except ImportError:
    import subprocess

    subprocess.check_call([sys.executable, "-m", "pip", "install", "markdown"])
    import markdown


def render(md_path: Path) -> Path:
    html_path = md_path.with_suffix(".html")
    md_text = md_path.read_text(encoding="utf-8")
    html_body = markdown.markdown(md_text, extensions=["tables", "fenced_code"])

    full_html = f"""<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{md_path.stem}</title>
<style>
body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background-color: #0d1117;
    color: #c9d1d9;
    line-height: 1.6;
    padding: 40px;
    max-width: 1100px;
    margin: auto;
}}
h1, h2, h3 {{
    color: #58a6ff;
    border-bottom: 1px solid #30363d;
    padding-bottom: 8px;
}}
table {{
    border-collapse: collapse;
    width: 100%;
    margin: 20px 0;
    background-color: #161b22;
}}
th, td {{
    border: 1px solid #30363d;
    padding: 10px 14px;
    text-align: left;
}}
th {{
    background-color: #21262d;
    color: #f0f6fc;
}}
tr:nth-child(even) {{
    background-color: #0d1117;
}}
code {{
    background-color: #21262d;
    padding: 2px 6px;
    border-radius: 4px;
    font-family: monospace;
    color: #f0883e;
}}
strong {{
    color: #58a6ff;
}}
hr {{
    border: 1px solid #30363d;
    margin: 30px 0;
}}
</style>
</head>
<body>
{html_body}
</body>
</html>"""
    html_path.write_text(full_html, encoding="utf-8")
    print(f"Rendered: {html_path}")
    return html_path


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("_private/research/2026-10-30-12-weeks-random-scalping-audit.md")
    render(target)
