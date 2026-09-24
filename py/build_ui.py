"""
Assemble the single-file console.

Two outputs, from the same parts.

The Artifact build carries no doctype, html, head or body tags, because the
Artifact runtime supplies its own document skeleton and rejects a file that
brings a second one. The standalone build is that same file inside a minimal
document, for anywhere that expects an ordinary web page: GitHub Pages, a
static host, or a double-click from a file manager.

Everything else - CSS, scripts, logos and the whole data payload - is inlined
in both, because a published artifact cannot fetch anything but fonts, and a
console that works offline is worth more than one that needs a server.
"""
import base64
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI = os.path.join(ROOT, "ui")
OUT = os.path.join(ROOT, "out", "psa_console.html")
# The standalone build lives in docs/ because that is the folder GitHub Pages
# serves without any further configuration.
OUT_STANDALONE = os.path.join(ROOT, "docs", "index.html")
DATA = os.path.join(ROOT, "out", "psa_data.json")
# Vendored into the repository rather than read from wherever the brand assets
# happen to live on one machine. The build used to point at a synced skill
# directory by absolute path, which worked here and nowhere else: CI had no
# such path and the whole pipeline failed on a logo.
BRAND = os.path.join(UI, "assets")

SCRIPTS = ["charts.js", "sections.js", "sections2.js", "sections3.js",
           "sections4.js", "sections5.js", "sections6.js", "sections7.js",
           "sections9.js", "exchange.js", "boot.js"]


def data_uri(path):
    with open(path, "rb") as fh:
        return "data:image/png;base64," + base64.b64encode(fh.read()).decode()


def wordmark(color):
    # Neutral, vendor-agnostic wordmark inlined as an SVG data URI, so the
    # console carries no brand image asset.
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="188" height="26">'
           '<text x="0" y="20" font-family="Georgia, \'Times New Roman\', serif" '
           'font-size="20" font-weight="700" letter-spacing="-0.3" '
           'fill="%s">PS Intelligence</text></svg>' % color)
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode()


def main():
    with open(os.path.join(UI, "app.html"), encoding="utf-8") as fh:
        html = fh.read()

    with open(DATA, encoding="utf-8") as fh:
        payload = fh.read()
    # </script> inside a JSON island would close the tag early
    payload = payload.replace("</", "<\\/")

    logo_light = wordmark("#1A2930")   # dark wordmark on the light theme
    logo_dark = wordmark("#FFFFFF")    # white wordmark on the dark theme

    html = html.replace("__LOGO_LIGHT__", logo_light)
    html = html.replace("__LOGO_DARK__", logo_dark)
    html = html.replace("__DATA__", payload)

    parts = [html]
    for name in SCRIPTS:
        with open(os.path.join(UI, name), encoding="utf-8") as fh:
            js = fh.read()
        assert "</script" not in js.lower(), name
        parts.append(f"\n<script>\n{js}\n</script>\n")
    out = "".join(parts)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(out)

    # The standalone document. The wrapper is deliberately thin: a charset, a
    # viewport, and the two theme defaults. Anything more here would be a
    # second place where the console's appearance is decided.
    doc = ("<!doctype html>\n<html lang=\"en\">\n<head>\n"
           "<meta charset=\"utf-8\">\n"
           "<meta name=\"viewport\" content=\"width=device-width,"
           "initial-scale=1,viewport-fit=cover\">\n"
           "<meta name=\"description\" content=\"PS Intelligence: "
           "professional services operating console and acquisition "
           "diligence engine.\">\n"
           "<meta name=\"robots\" content=\"noindex, nofollow\">\n"
           "<style>:root{color-scheme:light dark}"
           "html,body{margin:0;padding:0}"
           "img{max-width:100%}[hidden]{display:none!important}</style>\n"
           "</head>\n<body>\n" + out + "\n</body>\n</html>\n")
    os.makedirs(os.path.dirname(OUT_STANDALONE), exist_ok=True)
    with open(OUT_STANDALONE, "w", encoding="utf-8") as fh:
        fh.write(doc)

    # The wrapper supplies the document skeleton. Match whole tags only, so
    # <thead> and <tbody> in the markup do not trip the check.
    for tag in ("!doctype", "html", "head", "body"):
        if re.search(r"<\s*/?\s*" + tag + r"[\s>]", out, re.I):
            raise SystemExit(f"output must not contain a <{tag}> tag")
    print(f"wrote {OUT}  {os.path.getsize(OUT)/1e6:.2f} MB")
    print(f"wrote {OUT_STANDALONE}  "
          f"{os.path.getsize(OUT_STANDALONE)/1e6:.2f} MB")


if __name__ == "__main__":
    main()
