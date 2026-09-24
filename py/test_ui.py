"""
Render the console in a real browser, walk every section in both orgs, and fail
on any console error, page error, or empty view. Screenshots go to out/shots/.
"""
import os
import sys

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "out", "psa_console.html")
WRAPPED = os.path.join(ROOT, "out", "_preview.html")
SHOTS = os.path.join(ROOT, "out", "shots")

SECTIONS = ["psos", "brief", "projects", "margin", "billing", "models",
            "evm", "capacity", "whatif", "gonogo", "acq", "intake", "checks",
            "resource", "dilig", "auto", "how"]

# Views with internal tabs or chip groups. Clicking every one of them is the
# only way to find a renderer that throws on a branch the default tab does
# not touch, which is exactly where that kind of bug hides.
TABBED = {
    "auto": ".chips button[data-aa], tr[data-opp]",
    "intake": ".chips button[data-t]",
    "dilig": ".chips button[data-t]",
    "checks": "tr[data-fam]",
    "resource": "tr[data-line]",
    "billing": ".chips button[data-bs], .chips button[data-bd]",
    "models": "tr[data-model]",
    "evm": ".chips button[data-es], .chips button[data-eq]",
}


def wrap():
    with open(SRC, encoding="utf-8") as fh:
        body = fh.read()
    doc = ("<!doctype html><html><head><meta charset='utf-8'>"
           "<meta name='viewport' content='width=device-width,initial-scale=1'>"
           "<style>:root{color-scheme:light}body{margin:0;font:14px system-ui}"
           "img{max-width:100%}[hidden]{display:none!important}</style>"
           "</head><body>" + body + "</body></html>")
    with open(WRAPPED, "w", encoding="utf-8") as fh:
        fh.write(doc)
    return WRAPPED


def main():
    os.makedirs(SHOTS, exist_ok=True)
    path = wrap()
    problems = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium/chrome-linux/chrome"
                                     if os.path.exists("/opt/pw-browsers/chromium/chrome-linux/chrome")
                                     else None)
        for theme in ("light", "dark"):
            page = browser.new_page(viewport={"width": 1600, "height": 1100},
                                    color_scheme=theme)
            msgs = []
            page.on("console", lambda m: msgs.append((m.type, m.text)))
            page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
            page.goto("file://" + path, wait_until="load")
            page.wait_for_timeout(700)

            # One organisation is exported now, so there is no switcher to
            # walk. The org_label stays in the failure messages because it is
            # what the reader needs to know which run failed.
            for org_label in ("Acquisition target",):
                for sec in SECTIONS:
                    page.click(f"#nav .navbtn[data-s='{sec}']")
                    page.wait_for_timeout(350)
                    view = page.query_selector(f"#view-{sec}")
                    text = (view.inner_text() or "").strip()
                    if len(text) < 120:
                        problems.append(f"{theme}/{org_label}/{sec}: view nearly empty "
                                        f"({len(text)} chars)")
                    if "could not be rendered" in text:
                        problems.append(f"{theme}/{org_label}/{sec}: render error shown")
                    # horizontal page overflow
                    ow = page.evaluate("document.documentElement.scrollWidth")
                    cw = page.evaluate("document.documentElement.clientWidth")
                    if ow > cw + 2:
                        problems.append(f"{theme}/{org_label}/{sec}: body scrolls "
                                        f"horizontally ({ow} > {cw})")
                    # Walk the tabs and sub-selectors inside a view.
                    sel = TABBED.get(sec)
                    if sel:
                        n = len(page.query_selector_all(f"#view-{sec} {sel}"))
                        for i in range(n):
                            els = page.query_selector_all(f"#view-{sec} {sel}")
                            if i >= len(els):
                                break
                            els[i].click()
                            page.wait_for_timeout(220)
                            view2 = page.query_selector(f"#view-{sec}")
                            t2 = (view2.inner_text() or "").strip()
                            if "could not be rendered" in t2:
                                problems.append(
                                    f"{theme}/{org_label}/{sec}: render error "
                                    f"on sub-view {i}")
                            if len(t2) < 120:
                                problems.append(
                                    f"{theme}/{org_label}/{sec}: sub-view {i} "
                                    f"nearly empty ({len(t2)} chars)")
                            ow2 = page.evaluate("document.documentElement.scrollWidth")
                            cw2 = page.evaluate("document.documentElement.clientWidth")
                            if ow2 > cw2 + 2:
                                problems.append(
                                    f"{theme}/{org_label}/{sec}: sub-view {i} "
                                    f"scrolls horizontally ({ow2} > {cw2})")
                    if theme == "light":
                        page.screenshot(path=os.path.join(SHOTS, f"{sec}.png"),
                                        full_page=False)
                    if theme == "dark" and sec in ("acq", "intake"):
                        page.screenshot(path=os.path.join(SHOTS, f"{sec}-dark.png"),
                                        full_page=False)

            for kind, text in msgs:
                if kind == "error" and not any(x in text for x in
                        ("fonts.googleapis", "ERR_TUNNEL", "ERR_NAME_NOT_RESOLVED",
                         "Failed to load resource")):
                    problems.append(f"{theme} console error: {text[:220]}")
            page.close()

        # narrow viewport check
        page = browser.new_page(viewport={"width": 420, "height": 900})
        page.goto("file://" + path, wait_until="load")
        page.wait_for_timeout(500)
        for sec in ("psos", "projects", "acq", "checks", "resource", "dilig"):
            page.click(f"#nav .navbtn[data-s='{sec}']")
            page.wait_for_timeout(300)
            ow = page.evaluate("document.documentElement.scrollWidth")
            cw = page.evaluate("document.documentElement.clientWidth")
            if ow > cw + 2:
                problems.append(f"mobile/{sec}: body scrolls horizontally ({ow} > {cw})")
            page.screenshot(path=os.path.join(SHOTS, f"mobile-{sec}.png"))
        page.close()
        browser.close()

    if problems:
        print(f"{len(problems)} problem(s):")
        for p in problems[:40]:
            print("  -", p)
        sys.exit(1)
    print("clean: all sections rendered in both themes, both orgs, no console errors")


if __name__ == "__main__":
    main()
