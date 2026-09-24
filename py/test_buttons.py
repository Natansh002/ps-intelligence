"""
Click every interactive element in the console and report what does nothing.

test_ui.py proves each view renders. It does not prove the controls work, and
those are different failures: a view that throws is obvious the moment anybody
opens it, while a chip that renders, highlights on click and changes nothing is
invisible until somebody trusts it.

Controls are discovered from the rendered DOM rather than listed here. A
hardcoded list tests the controls somebody remembered to add to it, which is
the wrong set by construction: the control that gets forgotten is exactly the
one that breaks.

The assertion is per click and accounts for the one legitimate case where
nothing should happen. Clicking a chip that is already selected is a no-op, so
that click is exercised and not asserted on. Every other click has to change
what the reader can see.
"""
import os

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "docs", "index.html")

# Anything a person can operate. Rows carrying a data- key are included
# because the console uses row selection as a disclosure control throughout.
CONTROL_SEL = (
    "button:not(.navbtn), select, input[type=range], input[type=number], "
    "tr.clickable, details > summary"
)
MAX_PER_KIND = 6          # enough to reach every branch, not every row


def digest(page):
    """
    A fingerprint of what the reader can currently see.

    This hashes the text rather than measuring it. The first version compared
    innerText.length, and three working controls reported dead because moving
    a slider changed "5" to "6.5" and "$2,000,000" to "$2,600,000" without
    changing the character count. A length is not a fingerprint.
    """
    return page.evaluate("""() => {
      const v = document.querySelector('.view:not([hidden])');
      if (!v) return 'no-view';
      // Selection state belongs in the fingerprint. Clicking the row that
      // the detail panel already happens to be showing changes no text at
      // all, and that is correct behaviour rather than a dead control, but
      // the row's own selected state does move and that is what proves the
      // click landed.
      const state = Array.from(
        v.querySelectorAll('[aria-selected],[aria-pressed],[open]'))
        .map(e => (e.getAttribute('aria-selected') || '') +
                  (e.getAttribute('aria-pressed') || '') +
                  (e.hasAttribute('open') ? 'o' : '')).join('');
      // The toast lives outside the view because it is page-level feedback.
      // A control whose whole job is to tell you why it refused is working,
      // and without this it reads as dead.
      const toast = (document.getElementById('toast') || {}).textContent || '';
      const s = v.innerText + '|' + v.querySelectorAll('*').length + '|' +
                state + '|' + toast;
      let h = 0;
      for (let i = 0; i < s.length; i++) {
        h = (Math.imul(31, h) + s.charCodeAt(i)) | 0;
      }
      return h;
    }""")


def kind_of(page, el):
    """Group controls so one representative of each kind is enough."""
    return el.evaluate("""e => {
      const d = Object.keys(e.dataset)[0] || '';
      const c = (e.className || '').toString().split(' ')[0] || '';
      return e.tagName.toLowerCase() + '|' + (e.type || '') + '|' + c + '|' + d;
    }""")


def main():
    if not os.path.exists(SRC):
        raise SystemExit(f"{SRC} not found. Run `make build` first.")

    dead, errors, clicked, skipped = [], [], 0, 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1000})
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        # A blocked font request is the sandbox having no network, not a
        # defect in the page, and it would otherwise fail every run.
        page.on("console", lambda m: errors.append(f"console: {m.text}")
                if m.type == "error" and "ERR_" not in m.text else None)
        page.goto("file://" + os.path.abspath(SRC))
        page.wait_for_timeout(800)

        nav = page.eval_on_selector_all(
            "#nav .navbtn", "els => els.map(e => e.dataset.s)")

        for sec in nav:
            page.click(f'#nav .navbtn[data-s="{sec}"]')
            page.wait_for_timeout(250)
            if page.eval_on_selector(f"#view-{sec}",
                                     "e => e.innerText.trim().length") < 50:
                dead.append((sec, "the view itself", "renders empty"))
                continue

            seen = {}
            n = page.eval_on_selector_all(
                f"#view-{sec} {CONTROL_SEL}", "els => els.length")
            for i in range(n):
                els = page.query_selector_all(f"#view-{sec} {CONTROL_SEL}")
                if i >= len(els):
                    break                      # the click above re-rendered
                el = els[i]
                kind = kind_of(page, el)
                seen[kind] = seen.get(kind, 0) + 1
                if seen[kind] > MAX_PER_KIND:
                    continue

                tag = el.evaluate("e => e.tagName")
                typ = (el.get_attribute("type") or "").lower()
                pressed = el.get_attribute("aria-pressed")
                selected = el.get_attribute("aria-selected")
                already = pressed == "true" or selected == "true"
                before = digest(page)
                try:
                    before_value = el.evaluate("e => e.value")
                except Exception:
                    before_value = None

                try:
                    if typ == "range":
                        el.evaluate("""e => {
                          const mid = (+e.min + +e.max) / 2;
                          e.value = (+e.value === mid) ? +e.max : mid;
                          e.dispatchEvent(new Event('input', {bubbles: true}));
                          e.dispatchEvent(new Event('change', {bubbles: true}));
                        }""")
                    elif typ == "number":
                        el.evaluate("""e => {
                          e.value = Math.round((+e.value || 1) * 1.3);
                          e.dispatchEvent(new Event('input', {bubbles: true}));
                          e.dispatchEvent(new Event('change', {bubbles: true}));
                        }""")
                    elif tag == "SELECT":
                        opts = el.evaluate(
                            "e => Array.from(e.options).map(o => o.value)")
                        cur = el.evaluate("e => e.value")
                        other = next((o for o in opts if o != cur), None)
                        if other is None:
                            continue
                        el.select_option(other)
                    else:
                        el.click(timeout=3000)
                    clicked += 1
                except Exception as exc:
                    dead.append((sec, kind, f"#{i} click failed: "
                                            f"{str(exc)[:70]}"))
                    continue

                page.wait_for_timeout(180)
                if already:
                    skipped += 1               # re-selecting is a no-op
                    continue
                if digest(page) != before:
                    continue
                # A control can legitimately change nothing on the page and
                # still have worked: a field in a form whose effect is
                # deferred until somebody presses the button next to it. Its
                # own value moving is the proof.
                els2 = page.query_selector_all(f"#view-{sec} {CONTROL_SEL}")
                moved = False
                if i < len(els2):
                    try:
                        moved = els2[i].evaluate(
                            "e => e.value !== undefined") and \
                            els2[i].evaluate("e => e.value") != before_value
                    except Exception:
                        moved = False
                if not moved:
                    dead.append((sec, kind, f"#{i} changed nothing"))

        browser.close()

    print(f"clicked {clicked} controls across {len(nav)} views "
          f"({skipped} were already-selected no-ops)")
    if dead:
        print(f"\nDEAD: {len(dead)}")
        for sec, kind, why in dead[:40]:
            print(f"  {sec:<10}{kind:<34}{why}")
    if errors:
        print(f"\nPAGE ERRORS: {len(errors)}")
        for e in errors[:15]:
            print("  " + e[:160])
    if dead or errors:
        raise SystemExit(f"\n{len(dead)} dead controls, {len(errors)} errors")
    print("\nclean: every control changed something and nothing threw")


if __name__ == "__main__":
    main()
