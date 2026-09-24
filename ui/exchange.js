/* =========================================================================
   Getting data out, getting data in, and capturing a change on the way back.

   The console is a static page over an exported snapshot. It has no server and
   it cannot write to the database, and pretending otherwise would be the worst
   option available: a screen that appears to save and does not.

   So the round trip is explicit and in three parts.

   OUT   Any table on any view exports as CSV, and the whole snapshot exports
         as JSON. Both are exactly what the screen is reading, so a figure
         somebody exports and a figure somebody quotes cannot disagree.

   IN    A payload file produced by `load_target.py --analyse` can be opened
         straight into this page. That is what makes the hosted console usable
         for a second organisation without a rebuild and a redeploy.

   BACK  An override is captured here with its mandatory comment, held in this
         browser, and exported as a change file that `py/apply_changes.py`
         applies to the database with a full audit row. Nothing is written by
         the page itself. The requirement is that no change reaches financial
         or project data without an explicit human confirmation, and a file a
         person exports, inspects and runs is that confirmation made physical.

   The download path degrades rather than failing silently. Some sandboxed
   hosts block a page from starting its own download; where that happens the
   content goes to the clipboard instead and the page says so.
   ========================================================================= */
"use strict";

const OVERRIDE_KEY = "psa.overrides.v1";

/* ---------- getting bytes to the person ------------------------------- */
/**
 * Whether this host will let the page hand the reader a file.
 *
 * Detected up front rather than attempted and caught, because the blocked
 * case does not throw. An embedded viewer simply ignores the click, so a
 * try/catch around it reports success and the reader gets nothing: the exact
 * "looks like it worked" failure this console is built to avoid. Being inside
 * a frame is the signal, since that is what every sandboxed preview has in
 * common and what a static host never does.
 */
function canDownload() {
  try {
    return window.self === window.top;
  } catch (err) {
    return false;              // cross-origin frame: definitely embedded
  }
}

function deliver(filename, text, mime) {
  if (!canDownload()) {
    copyOut(filename, text);
    return;
  }
  // Blob first, because it is the one that produces a real file. The
  // clipboard fallback exists for hosts that refuse a page-initiated
  // download; it is a worse experience and it is better than a dead button.
  try {
    const blob = new Blob([text], { type: mime + ";charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 1000);
    toast(`Downloaded ${filename}`);
    return;
  } catch (err) {
    /* fall through to the clipboard */
  }
  copyOut(filename, text);
}

/**
 * The fallback: the content goes to the clipboard and the reader is told why.
 * Silence here would be worse than the limitation.
 */
function copyOut(filename, text) {
  const note = `This viewer will not let a page save a file, so ${filename} ` +
               `is on your clipboard. Paste it into an editor and save it, ` +
               `or open the console on its own page to download directly.`;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(
      () => toast(note),
      () => toast(`Could not save or copy ${filename} on this host`));
    return;
  }
  // No clipboard API either. A textarea selection still works everywhere.
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;top:-1000px;opacity:0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    toast(ok ? note : `Could not save or copy ${filename} on this host`);
  } catch (err) {
    toast(`Could not save or copy ${filename} on this host`);
  }
}

function toast(msg) {
  let t = document.getElementById("toast");
  if (!t) {
    t = document.createElement("div");
    t.id = "toast";
    t.className = "toast";
    t.setAttribute("role", "status");
    t.setAttribute("aria-live", "polite");
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), 4200);
}

/* ---------- CSV ------------------------------------------------------- */
function csvCell(v) {
  const s = (v === null || v === undefined) ? "" : String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/**
 * Serialise the tables on a view.
 *
 * Read from the rendered DOM rather than from the payload, deliberately. What
 * a person means by "export this" is the table in front of them, with the
 * filter they applied and the columns they can see, not the underlying
 * records that happen to feed it.
 */
function viewToCsv(viewEl, title) {
  const out = [];
  viewEl.querySelectorAll("table").forEach((tbl, i) => {
    const caption = tbl.closest(".card")
      ? (tbl.closest(".card").querySelector("h3")
         || {}).textContent || `Table ${i + 1}`
      : `Table ${i + 1}`;
    out.push(`# ${title} — ${caption.trim()}`);
    tbl.querySelectorAll("tr").forEach(tr => {
      const cells = Array.from(tr.querySelectorAll("th,td"));
      if (!cells.length) return;
      // Detail rows that span the table are prose, not data.
      if (cells.length === 1 && cells[0].hasAttribute("colspan")) return;
      out.push(cells.map(c => csvCell(
        c.innerText.replace(/\s+/g, " ").trim())).join(","));
    });
    out.push("");
  });
  return out.join("\n");
}

function exportCurrentView() {
  const el = document.querySelector(".view:not([hidden])");
  if (!el) return;
  const sec = SECTIONS.find(s => `view-${s.id}` === el.id);
  const title = sec ? sec.label : "view";
  const csv = viewToCsv(el, title);
  if (!csv.trim()) {
    toast("This view has no table to export");
    return;
  }
  const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
  deliver(`${ORG.toLowerCase()}-${slug}-${DATA.as_of}.csv`, csv, "text/csv");
}

function exportPayload() {
  deliver(`${ORG.toLowerCase()}-psa-data-${DATA.as_of}.json`,
          JSON.stringify(DATA, null, 2), "application/json");
}

/* ---------- importing a payload --------------------------------------- */
function importPayload(file) {
  const reader = new FileReader();
  reader.onerror = () => toast("Could not read that file");
  reader.onload = () => {
    let next;
    try {
      next = JSON.parse(reader.result);
    } catch (err) {
      toast("That file is not valid JSON");
      return;
    }
    // Check the shape before swapping it in. A payload from the wrong tool
    // renders as eighteen broken views, and the reader has no way to tell
    // that from a bug in the console.
    const missing = ["orgs", "as_of", "engine_version"]
      .filter(k => !(k in next));
    if (missing.length) {
      toast(`Not a console payload: missing ${missing.join(", ")}`);
      return;
    }
    const codes = Object.keys(next.orgs || {});
    if (!codes.length) {
      toast("That payload contains no organisation");
      return;
    }
    DATA = next;
    ORGS.length = 0;
    codes.forEach(c => ORGS.push(c));
    ORG = codes[0];
    SECTION = "psos";
    buildChrome();
    render();
    window.scrollTo(0, 0);
    toast(`Loaded ${next.orgs[ORG].org.org_name} — ` +
          `${codes.length} organisation${codes.length === 1 ? "" : "s"}, ` +
          `as of ${next.as_of}`);
  };
  reader.readAsText(file);
}

/* ---------- overrides, captured here and applied by a script ---------- */
function loadOverrides() {
  try {
    return JSON.parse(localStorage.getItem(OVERRIDE_KEY) || "[]");
  } catch (err) {
    return [];               // private window, blocked storage, corrupt value
  }
}

function saveOverrides(list) {
  try {
    localStorage.setItem(OVERRIDE_KEY, JSON.stringify(list));
    return true;
  } catch (err) {
    toast("This browser will not store the change locally. Export it now or " +
          "it is lost when the tab closes.");
    return false;
  }
}

/**
 * Record one override.
 *
 * The comment is mandatory and is enforced here rather than trusted to the
 * form, because an override with no reason is indistinguishable from a
 * mistake six weeks later, which is precisely when somebody reads it.
 */
function captureOverride(o) {
  if (!o.reason || o.reason.trim().length < 8) {
    toast("An override needs a reason of at least a few words");
    return false;
  }
  if (String(o.old_value) === String(o.new_value)) {
    toast("That is already the current value");
    return false;
  }
  const list = loadOverrides();
  list.push({
    ...o,
    reason: o.reason.trim(),
    org_code: ORG,
    as_of: DATA.as_of,
    captured_at: new Date().toISOString().replace("T", " ").slice(0, 19),
  });
  saveOverrides(list);
  toast(`Override recorded. ${list.length} pending — export from Pending ` +
        `changes to apply it.`);
  return true;
}

function exportOverrides() {
  const list = loadOverrides();
  if (!list.length) {
    toast("Nothing to export");
    return;
  }
  deliver(`${ORG.toLowerCase()}-changes-${DATA.as_of}.json`,
          JSON.stringify({
            format: "psa-change-file/v1",
            org_code: ORG,
            as_of: DATA.as_of,
            engine_version: DATA.engine_version,
            exported_at: new Date().toISOString(),
            changes: list,
          }, null, 2), "application/json");
}

function clearOverrides() {
  saveOverrides([]);
  render();
  toast("Pending changes cleared");
}

/* ---------- the toolbar ----------------------------------------------- */
function buildExchangeBar() {
  const host = document.getElementById("exchange");
  if (!host) return;
  const pending = loadOverrides().length;
  host.innerHTML = `
    <button class="btn sm" id="ex-csv" title="The tables on this view, as they are filtered right now">Export view</button>
    <button class="btn sm" id="ex-json" title="The whole snapshot this console is reading">Export data</button>
    <label class="btn sm" for="ex-file" title="Open a payload written by load_target.py --analyse">Import
      <input type="file" id="ex-file" accept="application/json,.json" hidden></label>
    ${pending ? `<button class="btn sm cta" id="ex-pending">${pending} pending
      change${pending === 1 ? "" : "s"}</button>` : ""}`;

  document.getElementById("ex-csv").addEventListener("click", exportCurrentView);
  document.getElementById("ex-json").addEventListener("click", exportPayload);
  document.getElementById("ex-file").addEventListener("change", ev => {
    if (ev.target.files && ev.target.files[0]) importPayload(ev.target.files[0]);
    ev.target.value = "";
  });
  const p = document.getElementById("ex-pending");
  if (p) {
    p.addEventListener("click", () => {
      SECTION = "changes";
      render();
      window.scrollTo(0, 0);
    });
  }
}

/* ---------- the pending changes view ---------------------------------- */
function renderChanges(el) {
  const list = loadOverrides();
  el.innerHTML = `
    <div class="viewhead">
      <div class="eyebrow">Pending changes</div>
      <h1>Overrides waiting to be applied</h1>
      <p>This console reads a snapshot and never writes to the database. An
         override captured here is held in this browser until somebody exports
         it and runs it against the database, which is what makes the
         confirmation explicit rather than implied. Every applied change lands
         in the audit log with who, what, the previous value, the new value and
         the reason.</p>
    </div>

    ${list.length ? `
      <div class="card" style="margin-bottom:18px">
        <div class="row" style="margin-bottom:12px">
          <button class="btn primary" id="ch-export">Export change file</button>
          <button class="btn" id="ch-clear">Clear all</button>
          <div class="spacer"></div>
          <span class="note">${list.length} change${list.length === 1 ? "" : "s"}</span>
        </div>
        <div class="tblwrap"><table>
          <thead><tr><th>Captured</th><th>Entity</th><th>Field</th>
            <th>From</th><th>To</th><th>Who</th><th>Reason</th><th></th>
          </tr></thead>
          <tbody>${list.map((c, i) => `
            <tr><td class="tight note">${esc(c.captured_at)}</td>
              <td><b>${esc(c.entity_label || c.entity_pk)}</b>
                <div class="note">${esc(c.entity_table)} ${c.entity_pk}</div></td>
              <td class="mono">${esc(c.field_name)}</td>
              <td>${esc(String(c.old_value))}</td>
              <td><b>${esc(String(c.new_value))}</b></td>
              <td class="note">${esc(c.changed_by || "")}</td>
              <td class="note">${esc(c.reason)}</td>
              <td class="tight"><button class="btn sm" data-drop="${i}">Remove</button></td>
            </tr>`).join("")}
          </tbody>
          <caption>Held in this browser only. Nothing here has reached the
            database yet.</caption>
        </table></div>
      </div>

      <div class="card">
        <h3>Applying them</h3>
        <p class="sub">Export the file, put it somewhere the repository can see
          it, and run the applier. It validates every change against the
          current database before it writes anything, and it writes the field
          and its audit row in one transaction.</p>
        <div class="schema" style="margin:12px 0">
          <div class="note" style="margin-bottom:4px">1. Check what it would do. Writes nothing.</div>
          <div>python3 py/apply_changes.py changes.json --dry-run</div>
          <div class="note" style="margin:12px 0 4px">2. Apply, then rebuild so the console reflects it.</div>
          <div>python3 py/apply_changes.py changes.json --by "<b>your name</b>"<br>make build</div>
        </div>
        <p class="note">A change whose recorded previous value no longer
          matches the database is rejected rather than applied. Somebody else
          moved it while this was pending, and overwriting that silently is how
          two people's decisions become one.</p>
      </div>`
    : `<div class="card">
        <h3>Nothing pending</h3>
        <p class="sub">Overrides are captured from the engagement detail on
          Project intelligence. Select an engagement, then override its health
          with a reason.</p>
      </div>`}`;

  const ex = document.getElementById("ch-export");
  if (ex) ex.addEventListener("click", exportOverrides);
  const cl = document.getElementById("ch-clear");
  if (cl) cl.addEventListener("click", clearOverrides);
  el.querySelectorAll("button[data-drop]").forEach(b =>
    b.addEventListener("click", () => {
      const l = loadOverrides();
      l.splice(+b.dataset.drop, 1);
      saveOverrides(l);
      render();
    }));
}
