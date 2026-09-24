/* =========================================================================
   Delivery and implementation checks, and the published metric definitions.

   The reason this view exists at all is what the vendor review turned up.
   Both Certinia and Rocketlane ship the structure for delivery governance
   and leave the rules to the customer: four Green/Yellow/Red picklists with
   no shipped scoring logic in one, a free-text status and a manual at-risk
   flag in the other, and no documented resource over-allocation rule in
   either. So the rules are the product, and a rule nobody can read is a rule
   nobody can be held to.

   Every check therefore shows four things a chart cannot: what it asserts,
   why that matters, whether the source platform blocks the condition or
   merely lets you find it afterwards, and where the semantics come from.
   The third of those is the one worth the screen space. A condition the
   platform blocks is somebody else's problem. A condition it is silent on is
   where the money leaves.
   ========================================================================= */
"use strict";

let checkFilter = { family: "all", cls: "all", status: "all", enforce: "all" };
let checkOpen = null;

const ENFORCE_LABEL = {
  blocked: "Blocked by the platform",
  detected: "Reported, not prevented",
  silent: "Neither prevented nor reported",
};
const ENFORCE_CLASS = { blocked: "good", detected: "warn", silent: "bad" };
const STATUS_CLASS = {
  pass: "good", warn: "warn", fail: "bad", not_applicable: "mute",
};
const SEV_CLASS = {
  critical: "bad", high: "bad", medium: "warn", low: "mute",
};

function renderChecks(el) {
  const o = org(), c = o.checks;
  if (!c) {
    el.innerHTML = `<div class="card"><h3>No check run</h3>
      <p class="note">The check engine has not run against this
      organisation.</p></div>`;
    return;
  }
  const rs = c.results;
  const gates = rs.filter(r => r.is_gate);
  const ops = rs.filter(r => !r.is_gate);
  const silent = rs.filter(r => r.enforcement === "silent");
  const silentFailing = silent.filter(r => r.failing > 0);
  const verdictClass = c.run.readiness_verdict === "ready" ? "good"
    : c.run.readiness_verdict === "conditional" ? "warn" : "bad";

  const money = rs.filter(r => r.value_unit === "currency" && r.value_at_stake)
    .reduce((a, r) => a + r.value_at_stake, 0);
  const hours = rs.filter(r => r.value_unit === "hours" && r.value_at_stake)
    .reduce((a, r) => a + r.value_at_stake, 0);

  const families = {};
  rs.forEach(r => {
    const f = families[r.family] || (families[r.family] = {
      label: r.family_label, n: 0, fail: 0, warn: 0, na: 0, findings: 0,
      gate: 0, gatePass: 0, gateWeight: 0, gateWeightPass: 0, silent: 0,
    });
    f.n++;
    f.findings += r.failing;
    if (r.status === "fail") f.fail++;
    if (r.status === "warn") f.warn++;
    if (r.status === "not_applicable") f.na++;
    if (r.enforcement === "silent") f.silent++;
    if (r.is_gate) {
      f.gate++;
      f.gateWeight += r.gate_weight;
      if (r.status === "pass" || r.status === "not_applicable") {
        f.gatePass++;
        f.gateWeightPass += r.gate_weight;
      }
    }
  });
  const famRows = Object.entries(families)
    .sort((a, b) => b[1].fail - a[1].fail || b[1].findings - a[1].findings);

  el.innerHTML = `
    <div class="viewhead">
      <div class="eyebrow">Delivery and implementation checks</div>
      <h1>Go-live readiness and operational findings</h1>
      <p>${rs.length} checks in ${famRows.length} families, run against every
         record in scope. Each one records what it asserts, why that matters,
         and whether the platform this rule is modelled on <em>blocks</em> the
         condition, <em>reports</em> it, or is <em>silent</em> on it. The
         silent ones are the point: ${silent.length} of the ${rs.length} are
         conditions neither Certinia nor Rocketlane prevents or surfaces, which
         makes them the ones that only ever get found by someone reconciling a
         spreadsheet.</p>
    </div>

    <div class="grid g4" style="margin-bottom:22px">
      <div class="tile stripe ${verdictClass}">
        <div class="label">Go-live readiness</div>
        <div class="value">${pc(c.run.readiness_pct, 1)}</div>
        <div class="foot">${esc(c.run.readiness_verdict)} ·
          weighted across ${gates.length} gates</div>
      </div>
      <div class="tile stripe ${c.run.blocking_total ? 'bad' : 'good'}">
        <div class="label">Blocking</div>
        <div class="value">${nf(c.run.blocking_total)}</div>
        <div class="foot">checks at fail on critical or high severity</div>
      </div>
      <div class="tile stripe ${silentFailing.length ? 'bad' : 'good'}">
        <div class="label">Silent failures</div>
        <div class="value">${nf(silentFailing.length)}</div>
        <div class="foot">of ${silent.length} checks neither product surfaces</div>
      </div>
      <div class="tile">
        <div class="label">At stake</div>
        <div class="value sm">${moneyK(money)}</div>
        <div class="foot">${nf(hours)} hours also in scope across the findings</div>
      </div>
    </div>

    <div class="card" style="margin-bottom:18px">
      <h3>Readiness against operations</h3>
      <p class="sub">Two different questions, and merging them is what makes a
        readiness score unreachable and therefore ignored.</p>
      <div class="grid g2">
        <div>
          <div class="kv" style="margin-bottom:10px">
            <dt>Readiness checks</dt><dd>${gates.length}</dd>
            <dt>Passing</dt><dd>${gates.filter(g => g.status === "pass").length}</dd>
            <dt>Not applicable</dt><dd>${gates.filter(g => g.status === "not_applicable").length}</dd>
            <dt>Failing or warning</dt><dd>${gates.filter(g => g.status === "fail" || g.status === "warn").length}</dd>
          </div>
          <p class="note">Configuration, controls and data integrity. These are
            clearable before cutover and a hundred per cent is a number this
            organisation can actually reach. Not-applicable counts as passed,
            because a migration check on an organisation that is not migrating
            is not a gap.</p>
        </div>
        <div>
          <div class="kv" style="margin-bottom:10px">
            <dt>Operational checks</dt><dd>${ops.length}</dd>
            <dt>Clean</dt><dd>${ops.filter(g => g.status === "pass").length}</dd>
            <dt>With findings</dt><dd>${ops.filter(g => g.failing > 0).length}</dd>
            <dt>Findings raised</dt><dd>${nf(ops.reduce((a, r) => a + r.failing, 0))}</dd>
          </div>
          <p class="note">Over-allocation, stale forecasts, ageing unbilled
            value, earned-value gaps. Real money and real findings, but never
            zero in a live book, so they are reported at full severity and not
            counted against readiness. Scoring these as go-live gates is how a
            readiness number ends up permanently stuck in the eighties.</p>
        </div>
      </div>
    </div>

    <div class="grid g2" style="margin-bottom:18px">
      <div class="card">
        <h3>By family</h3>
        <p class="sub">Gate weight passed, and where the findings are.</p>
        <div class="tblwrap"><table>
          <thead><tr>
            <th>Family</th><th class="r">Checks</th><th class="r">Silent</th>
            <th class="r">Gate weight</th><th class="r">Findings</th><th>State</th>
          </tr></thead>
          <tbody>${famRows.map(([code, f]) => `
            <tr class="clickable" data-fam="${code}">
              <td>${esc(f.label)}</td>
              <td class="r num">${f.n}</td>
              <td class="r num">${f.silent || "—"}</td>
              <td class="r num">${f.gateWeight
                ? pc(f.gateWeightPass / f.gateWeight * 100, 0) : "—"}</td>
              <td class="r num">${f.findings ? nf(f.findings) : "—"}</td>
              <td class="tight">${f.fail ? pill("bad", f.fail + " failing")
                : f.warn ? pill("warn", f.warn + " warning")
                : pill("good", "clean")}</td>
            </tr>`).join("")}
          </tbody>
          <caption>Click a family to filter the list below.</caption>
        </table></div>
      </div>
      <div class="card">
        <h3>What the source platform does about it</h3>
        <p class="sub">Counting the checks, and the findings behind them, by
          whether the platform would have stopped the condition arising.</p>
        <div class="chartbox" id="enforce-chart"></div>
        <p class="note" style="margin-top:8px">Eleven of these conditions are
          silent in both products. A blocked condition cannot reach your
          ledger; a reported one is a report somebody has to run. A silent one
          is only ever found by accident, which is why it is worth building
          the rule rather than buying it.</p>
      </div>
    </div>

    <div class="card" style="margin-bottom:18px">
      <h3>The catalogue</h3>
      <p class="sub">${rs.length} rules, held as data rather than code, because
        a rule that lives in a query cannot be reviewed by the person
        accountable for it.</p>
      <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px;margin-bottom:12px">
        <label class="field"><span class="lbl">Family</span>
          <select id="ck-family">
            <option value="all">All families</option>
            ${famRows.map(([code, f]) =>
              `<option value="${code}">${esc(f.label)}</option>`).join("")}
          </select></label>
        <label class="field"><span class="lbl">Kind</span>
          <select id="ck-class">
            <option value="all">Readiness and operational</option>
            <option value="readiness">Readiness gates only</option>
            <option value="operational">Operational findings only</option>
          </select></label>
        <label class="field"><span class="lbl">Result</span>
          <select id="ck-status">
            <option value="all">Every result</option>
            <option value="fail">Failing</option>
            <option value="warn">Warning</option>
            <option value="pass">Passing</option>
            <option value="not_applicable">Not applicable</option>
          </select></label>
        <label class="field"><span class="lbl">Platform behaviour</span>
          <select id="ck-enforce">
            <option value="all">Any</option>
            <option value="silent">Silent in both products</option>
            <option value="detected">Reported, not prevented</option>
            <option value="blocked">Blocked by the platform</option>
          </select></label>
      </div>
      <div id="ck-list"></div>
    </div>

    <div class="card">
      <h3>Published metric definitions</h3>
      <p class="sub">The arithmetic this engine uses, beside the vendor formula
        it corresponds to, and where the two deliberately differ.</p>
      <p class="note" style="margin-bottom:12px">Reviewing the vendor
        documentation, the most useful thing either product publishes is its
        arithmetic and the most damaging thing is where it does not. Rocketlane
        publishes utilisation and margin as one-line formulas. Certinia
        publishes three period-scoped utilisation formulas with credited and
        excluded adjustments in both numerator and denominator, and publishes
        no margin formula at all. Neither publishes an estimate-to-complete
        derivation, which is the one that decides whether a forecast is worth
        reading.</p>
      <div id="metric-list"></div>
    </div>`;

  drawEnforcement(c);
  drawMetrics(c.metrics);
  wireCheckFilters(el, c);
  drawCheckList(c);
}

function drawEnforcement(c) {
  const host = document.getElementById("enforce-chart");
  if (!host) return;
  const order = ["silent", "detected", "blocked"];
  const rows = order.map(k => {
    const set = c.results.filter(r => r.enforcement === k);
    const failing = set.filter(r => r.failing > 0);
    return {
      label: ENFORCE_LABEL[k],
      values: [set.length, failing.length],
      tip: `${set.length} checks, ${failing.length} with findings`,
    };
  });
  groupedBars(host, rows, [
    { label: "Checks in the catalogue", color: "var(--s3)" },
    { label: "With findings in this run", color: "var(--bad)" },
  ], { height: 200, tickFormat: v => nf(v) });
  host.insertAdjacentHTML("afterbegin", legend([
    { label: "Checks in the catalogue", color: "var(--s3)" },
    { label: "With findings in this run", color: "var(--bad)" },
  ]));
}

function wireCheckFilters(el, c) {
  const bind = (id, key) => {
    const sel = el.querySelector(id);
    if (!sel) return;
    sel.value = checkFilter[key];
    sel.addEventListener("change", () => {
      checkFilter[key] = sel.value;
      checkOpen = null;
      drawCheckList(c);
    });
  };
  bind("#ck-family", "family");
  bind("#ck-class", "cls");
  bind("#ck-status", "status");
  bind("#ck-enforce", "enforce");
  el.querySelectorAll("tr[data-fam]").forEach(tr =>
    tr.addEventListener("click", () => {
      checkFilter.family = checkFilter.family === tr.dataset.fam
        ? "all" : tr.dataset.fam;
      const sel = el.querySelector("#ck-family");
      if (sel) sel.value = checkFilter.family;
      drawCheckList(c);
    }));
}

function drawCheckList(c) {
  const host = document.getElementById("ck-list");
  if (!host) return;
  const f = checkFilter;
  const rows = c.results.filter(r =>
    (f.family === "all" || r.family === f.family) &&
    (f.cls === "all" || r.check_class === f.cls) &&
    (f.status === "all" || r.status === f.status) &&
    (f.enforce === "all" || r.enforcement === f.enforce));

  if (!rows.length) {
    host.innerHTML = `<p class="note">No check matches that combination.
      That is a filter result, not a clean bill of health.</p>`;
    return;
  }

  host.innerHTML = `
    <div class="tblwrap"><table>
      <thead><tr>
        <th>Check</th><th>Assertion</th><th class="r">Population</th>
        <th class="r">Failing</th><th class="r">At stake</th>
        <th>Platform</th><th>Result</th>
      </tr></thead>
      <tbody>${rows.map(r => `
        <tr class="clickable" data-code="${r.check_code}"
            aria-selected="${checkOpen === r.check_code}">
          <td class="tight">
            <span class="note">${r.check_code}</span><br>${esc(r.title)}
            ${r.is_gate ? `<span class="note"> · gate ×${nf(r.gate_weight, 1)}</span>` : ""}
          </td>
          <td style="max-width:34ch">${esc(r.assertion)}</td>
          <td class="r num">${nf(r.population)}</td>
          <td class="r num">${r.failing ? nf(r.failing) : "—"}</td>
          <td class="r num">${r.value_at_stake
            ? (r.value_unit === "currency" ? moneyK(r.value_at_stake)
               : nf(r.value_at_stake, 0) + " " + (r.value_unit || ""))
            : "—"}</td>
          <td class="tight">${pill(ENFORCE_CLASS[r.enforcement],
            r.enforcement)}</td>
          <td class="tight">${pill(STATUS_CLASS[r.status],
            r.status === "not_applicable" ? "n/a" : r.status)}</td>
        </tr>
        ${checkOpen === r.check_code ? checkDetailRow(r) : ""}`).join("")}
      </tbody>
      <caption>${rows.length} of ${c.results.length} checks shown. Click a row
        for the assertion, the consequence, the remediation and the
        findings.</caption>
    </table></div>`;

  host.querySelectorAll("tr[data-code]").forEach(tr =>
    tr.addEventListener("click", () => {
      checkOpen = checkOpen === tr.dataset.code ? null : tr.dataset.code;
      drawCheckList(c);
    }));
}

function checkDetailRow(r) {
  const findings = r.findings || [];
  return `<tr><td colspan="7" style="background:var(--surface-sunk)">
    <div class="grid g2" style="gap:16px">
      <div>
        <p style="margin:0 0 8px"><b>What it asserts.</b>
          ${esc(r.assertion)}</p>
        <p style="margin:0 0 8px"><b>Why it matters.</b>
          ${esc(r.why_it_matters)}</p>
        <p style="margin:0 0 8px"><b>What to do.</b>
          ${esc(r.remediation)}</p>
        <p class="note" style="margin:0">
          Runs over: ${esc(r.scope)}. Severity ${esc(r.severity)}.
          ${r.is_gate ? `Counts toward readiness at weight
            ${nf(r.gate_weight, 1)}.` : "Operational finding, not a go-live gate."}
        </p>
      </div>
      <div>
        <p style="margin:0 0 6px"><b>${esc(ENFORCE_LABEL[r.enforcement])}.</b>
          ${esc(r.vendor_basis || "Neither product documents this behaviour.")}</p>
        ${r.vendor_source ? `<p class="note" style="margin:0 0 10px">
          Source: ${esc(r.vendor_source)}</p>` : ""}
        <p style="margin:0 0 6px"><b>Result.</b> ${esc(r.statement)}</p>
      </div>
    </div>
    ${findings.length ? `
      <details class="evidence" open>
        <summary>${findings.length === (r.failing || 0)
          ? `All ${findings.length} findings`
          : `${findings.length} of ${nf(r.failing)} findings, most material first`}</summary>
        <div class="tblwrap" style="margin-top:8px"><table>
          <thead><tr><th>Record</th><th>Observed</th><th>Expected</th>
            <th class="r">At stake</th></tr></thead>
          <tbody>${findings.map(x => `
            <tr><td>${esc(x.entity_label)}
                  <div class="note">${esc(x.message)}</div></td>
                <td class="tight">${esc(x.observed || "—")}</td>
                <td class="tight">${esc(x.expected || "—")}</td>
                <td class="r num">${x.value_at_stake
                  ? (x.value_unit === "currency" ? moneyK(x.value_at_stake)
                     : nf(x.value_at_stake, 1) + " " + (x.value_unit || ""))
                  : "—"}</td></tr>`).join("")}
          </tbody>
          ${findings.length < (r.failing || 0) ? `<caption>Detail is capped at
            twelve rows per check so one systemic fault cannot bury every other
            check in the report. The failing count above is not
            capped.</caption>` : ""}
        </table></div>
      </details>` : `<p class="note" style="margin:10px 0 0">
        ${r.status === "not_applicable"
          ? "Nothing in scope to check, so nothing to show."
          : "No findings. Every record in scope satisfies the assertion."}</p>`}
  </td></tr>`;
}

function drawMetrics(metrics) {
  const host = document.getElementById("metric-list");
  if (!host) return;
  host.innerHTML = `<div class="tblwrap"><table>
    <thead><tr><th>Metric</th><th>This engine</th><th>Vendor formula</th>
      <th>Where they differ</th></tr></thead>
    <tbody>${metrics.map(m => `
      <tr>
        <td class="tight"><b>${esc(m.metric_label)}</b>
          <div class="note">${esc(m.notes)}</div></td>
        <td><code style="font-size:12px">${esc(m.formula)}</code>
          ${m.numerator ? `<div class="note">numerator: ${esc(m.numerator)}</div>` : ""}
          ${m.denominator ? `<div class="note">denominator: ${esc(m.denominator)}</div>` : ""}</td>
        <td>${m.vendor_formula
          ? `<code style="font-size:12px">${esc(m.vendor_formula)}</code>
             <div class="note">${esc(m.vendor || "")}${m.vendor_source
               ? " · " + esc(m.vendor_source) : ""}</div>`
          : `<span class="note">Not published by either product.</span>`}</td>
        <td>${m.divergence ? esc(m.divergence)
          : `<span class="note">Same basis.</span>`}</td>
      </tr>`).join("")}
    </tbody></table></div>`;
}
