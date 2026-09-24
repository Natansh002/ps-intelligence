/* =========================================================================
   Acquisition assessment, data intake, and the model notes.
   ========================================================================= */
"use strict";

/* ===================== 34-36. Acquisition ============================== */
function renderAcq(el) {
  const o = org();
  const bench = DATA.benchmark || {};
  const benchName = bench.org_name || "the acquirer";
  const groups = {};
  o.metrics.forEach(m => { (groups[m.metric_group] = groups[m.metric_group] || []).push(m); });

  const fmt = (v, u) => v === null || v === undefined ? "—"
    : u === "currency" ? moneyK(v) : u === "percent" ? pc(v)
    : u === "count" ? nf(v, 0) : u === "hours" ? hrs(v) : nf(v, 1);

  el.innerHTML = `
    <div class="viewhead">
      <div class="eyebrow">Requirements 34, 35 and 36</div>
      <h1>Acquisition assessment</h1>
      <p>An executive read on ${esc(o.org.org_name)} from
         ${o.assessment.months_of_history} months of its own PSA history, every metric
         set beside the same metric for ${esc(benchName)}. Acquired
         ${esc(o.org.acquired_on || '')}, migration state
         ${esc(o.org.migration_state)}.</p>
      <p class="note" style="margin-top:6px;max-width:78ch">The benchmark column is
         ${esc(benchName)}: ${nf(bench.projects)} projects and
         ${nf(bench.consultants)} billable consultants, measured on the same
         definitions over the same window. That organisation is not browsable here
         and does not need to be. A number is only a benchmark if it was measured
         the same way, so what matters is the definition rather than the screen,
         and the definitions are on the <em>How it works</em> page.</p>
    </div>

    <div class="grid g4" style="margin-bottom:20px">
      ${["REVENUE", "GROSS_MARGIN", "BILLABLE_UTIL", "REV_PER_CONSULTANT"].map(code => {
        const m = o.metrics.find(x => x.metric_code === code);
        if (!m) return "";
        const worse = m.variance_vs_benchmark !== null &&
          ((m.direction_good === "higher" && m.variance_vs_benchmark < 0) ||
           (m.direction_good === "lower" && m.variance_vs_benchmark > 0));
        return `<div class="tile stripe ${worse ? 'bad' : 'good'}">
          <div class="label">${esc(m.metric_label)}</div>
          <div class="value sm">${fmt(m.metric_value, m.metric_unit)}</div>
          <div class="foot">Benchmark ${fmt(m.benchmark_value, m.metric_unit)}
            ${m.variance_vs_benchmark !== null ? `<span class="delta ${worse ? 'down' : 'up'}">
              ${m.variance_vs_benchmark > 0 ? '+' : ''}${fmt(m.variance_vs_benchmark, m.metric_unit)}</span>` : ''}</div>
        </div>`;
      }).join("")}
    </div>

    <div class="card" style="margin-bottom:16px">
      <h3>Red flags</h3>
      <p class="sub">Threshold breaches, each with the rows behind it. Nothing here is a
        judgement the engine cannot show its work for.</p>
      <div class="grid g2" style="margin-top:12px">${o.red_flags.map(f => `
        <div class="flag ${esc(f.severity)}">
          <div class="row" style="margin-bottom:4px">
            <span class="pill ${f.severity === 'Critical' || f.severity === 'High' ? 'bad' : f.severity === 'Medium' ? 'warn' : 'mute'}">
              <i class="dot"></i>${esc(f.severity)}</span>
            <span class="note">${esc(f.flag_category)}</span>
          </div>
          <h4>${esc(f.headline)}</h4>
          <p class="detail">${esc(f.detail || '')}</p>
          <div class="action"><b>Recommended</b>${esc(f.recommended_action || '')}</div>
          ${(f.evidence || []).length ? `<details class="evidence">
            <summary>Show the ${f.evidence.length} record${f.evidence.length === 1 ? '' : 's'} behind this</summary>
            <div class="tblwrap" style="margin-top:8px"><table>
              <thead><tr>${Object.keys(f.evidence[0]).map(k =>
                `<th class="${typeof f.evidence[0][k] === 'number' ? 'r' : ''}">${esc(k.replace(/_/g, ' '))}</th>`).join("")}</tr></thead>
              <tbody>${f.evidence.map(row => `<tr>${Object.entries(row).map(([k, v]) =>
                `<td class="${typeof v === 'number' ? 'r num' : ''}">${typeof v === 'number'
                  ? (k.includes('revenue') || k.includes('value') ? moneyK(v)
                     : k.includes('pct') || k.includes('rate') ? nf(v, 1) : nf(v, v % 1 ? 1 : 0))
                  : esc(v)}</td>`).join("")}</tr>`).join("")}</tbody>
            </table></div></details>` : ""}
        </div>`).join("")}</div>
    </div>

    <div class="card" style="margin-bottom:16px">
      <h3>Executive assessment</h3>
      <p class="sub">${o.assessment.months_of_history} complete months,
        ${esc(o.assessment.period_from)} to ${esc(o.assessment.period_to)}.
        Benchmark column is ${esc(benchName)} on the same definitions.</p>
      ${Object.entries(groups).map(([g, ms]) => `
        <h4 style="margin:16px 0 6px;font-size:12px;text-transform:uppercase;letter-spacing:.07em;color:var(--ink-3)">${esc(g)}</h4>
        <div class="tblwrap"><table>
          <thead><tr><th>Measure</th><th class="r">${esc(o.org.org_code)}</th>
            <th class="r">${esc(bench.org_code || "Benchmark")}</th><th class="r">Variance</th>
            <th>Reading</th></tr></thead>
          <tbody>${ms.map(m => {
            const worse = m.variance_vs_benchmark !== null &&
              ((m.direction_good === "higher" && m.variance_vs_benchmark < 0) ||
               (m.direction_good === "lower" && m.variance_vs_benchmark > 0));
            return `<tr><td>${esc(m.metric_label)}</td>
              <td class="r num tight"><b>${fmt(m.metric_value, m.metric_unit)}</b></td>
              <td class="r num tight">${fmt(m.benchmark_value, m.metric_unit)}</td>
              <td class="r num tight ${m.variance_vs_benchmark === null ? '' : worse ? 'delta down' : 'delta up'}">
                ${m.variance_vs_benchmark === null ? '—' : (m.variance_vs_benchmark > 0 ? '+' : '') + fmt(m.variance_vs_benchmark, m.metric_unit)}</td>
              <td class="note">${m.direction_good === 'neutral' ? 'context' :
                m.variance_vs_benchmark === null ? '' : worse ? 'worse than benchmark' : 'at or better than benchmark'}</td></tr>`;
          }).join("")}</tbody>
        </table></div>`).join("")}
      <p class="note" style="margin-top:12px">The EBITDA proxy deducts a flat
        ${pc(DATA.assumptions.overhead_rate * 100, 0)} of revenue as non-delivery overhead.
        It is a proxy, not an accounting figure, and should be replaced with the target's
        actual overhead before it goes near a valuation.</p>
    </div>

    <div class="grid g2">
      <div class="card"><h3>Top and bottom customers</h3>
        <p class="sub">Gross profit over the assessment window.</p>
        <div id="ch-acq-cust"></div></div>
      <div class="card"><h3>Revenue concentration</h3>
        <p class="sub">Share of recognised revenue by customer, largest first.</p>
        <div id="ch-acq-conc"></div></div>
    </div>`;

  const cust = (o.profitability.customer || []).slice().sort((a, b) => b.gross_profit - a.gross_profit);
  hBars(document.getElementById("ch-acq-cust"),
    cust.slice(0, 7).concat(cust.slice(-5).reverse()).map(r => ({
      label: r.dimension_label, value: r.gross_profit,
      color: r.gross_profit < 0 ? "var(--bad)" : "var(--s1)",
      tipRows: [["Revenue", moneyK(r.revenue)], ["Gross profit", moneyK(r.gross_profit)],
                ["Margin", pc(r.margin_pct)], ["Projects", nf(r.project_count)]]
    })), { rowH: 24, labelW: 240, valueFormat: moneyK, tipFormat: moneyK });

  const total = cust.reduce((a, r) => a + r.revenue, 0);
  const byRev = cust.slice().sort((a, b) => b.revenue - a.revenue).slice(0, 12);
  hBars(document.getElementById("ch-acq-conc"), byRev.map(r => ({
    label: r.dimension_label, value: r.revenue / total * 100,
    color: r.revenue / total > 0.15 ? "var(--s4)" : "var(--s1)",
    tipRows: [["Share", pc(r.revenue / total * 100)], ["Revenue", moneyK(r.revenue)]]
  })), { rowH: 24, labelW: 240, valueFormat: v => pc(v), tipFormat: v => pc(v) });
}

/* ===================== 27-29, 43-44. Intake ============================ */
let intakeTab = "wizard";
function renderIntake(el) {
  const o = org();
  const m = o.migration;
  const batches = o.import_batches;
  const totalRows = batches.reduce((a, b) => a + b.row_count, 0);
  const errors = batches.reduce((a, b) => a + b.error_count, 0);
  const warnings = batches.reduce((a, b) => a + b.warning_count, 0);

  el.innerHTML = `
    <div class="viewhead">
      <div class="eyebrow">Requirements 27, 28, 29, 43 and 44</div>
      <h1>Data intake and migration</h1>
      <p>The acquired company's extract, as it arrived. Eight standard templates, a
         validation pass, a terminology mapping, and a migration that keeps every
         legacy identifier so a record can always be traced back.</p>
    </div>

    <div class="grid g5" style="margin-bottom:18px">
      <div class="tile"><div class="label">Rows received</div>
        <div class="value sm">${nf(totalRows)}</div><div class="foot">across 8 templates</div></div>
      <div class="tile stripe ${errors ? 'bad' : 'good'}"><div class="label">Errors</div>
        <div class="value sm">${nf(errors)}</div><div class="foot">block the load</div></div>
      <div class="tile stripe ${warnings ? 'warn' : 'good'}"><div class="label">Warnings</div>
        <div class="value sm">${nf(warnings)}</div><div class="foot">load, but need a decision</div></div>
      <div class="tile"><div class="label">Data quality score</div>
        <div class="value sm">${m ? pc(m.data_quality_score) : '—'}</div>
        <div class="foot">${m ? esc(m.stage) + " stage" : ''}</div></div>
      <div class="tile"><div class="label">Legacy IDs retained</div>
        <div class="value sm">${nf(o.lineage_count)}</div><div class="foot">traceable records</div></div>
    </div>

    ${m ? `<div class="card" style="margin-bottom:18px">
      <h3>Migration readiness</h3>
      <p class="sub">Completeness is measured against the mandatory fields in the
        templates, not asserted.</p>
      <div class="stage-flow" style="margin:12px 0 16px">
        ${["Legacy", "Standardized", "Validated", "Mapped", "Clean", "Loaded"].map((st, i, arr) => {
          const idx = arr.indexOf(m.stage);
          const cls = i <= idx ? "done" : i === idx + 1 ? "next" : "";
          return `<span class="stage ${cls}">${st}</span>` +
            (i < arr.length - 1 ? `<span class="stage-arrow">→</span>` : "");
        }).join("")}
      </div>
      <div class="grid g4">
        ${[["Data quality", m.data_quality_score], ["Financial data", m.financial_completeness_pct],
           ["Resource data", m.resource_completeness_pct], ["Project data", m.project_completeness_pct]]
          .map(([l, v]) => `<div>
            <div class="label" style="font-family:var(--font-ui);font-size:11.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3)">${l}</div>
            <div class="row" style="gap:8px;margin-top:5px">
              <div class="num" style="font-family:var(--font-ui);font-size:20px;font-weight:600;min-width:64px">${pc(v)}</div>
              <div class="bar-mini" style="flex:1"><span style="width:${Math.max(0, Math.min(100, v || 0))}%;background:${(v || 0) >= 95 ? 'var(--good)' : (v || 0) >= 85 ? 'var(--warn)' : 'var(--bad)'}"></span></div>
            </div></div>`).join("")}
      </div>
      <p class="note" style="margin-top:12px">${esc(m.notes || '')}
        ${m.approved_at ? `Approved ${esc(m.approved_at)} by ${esc(m.approved_by)}.`
          : "No migration approval recorded, so nothing has been loaded into the operating tenant."}</p>
    </div>` : ""}

    <div class="chips" style="margin-bottom:14px" id="intake-tabs">
      ${[["wizard", "Import wizard"], ["templates", "Standard templates"],
         ["load", "Load a new target"], ["mapping", "Terminology mapping"],
         ["quality", "Data quality"]]
        .map(([k, l]) => `<button class="chip" data-t="${k}" aria-pressed="${intakeTab === k}">${l}</button>`).join("")}
    </div>
    <div id="intake-body"></div>`;

  document.querySelectorAll("#intake-tabs button").forEach(b =>
    b.addEventListener("click", () => { intakeTab = b.dataset.t; renderIntake(el); }));
  drawIntakeTab();
}

// The eight templates, the seven validation stages and the two commands, as
// the loader actually implements them. Held here rather than written as prose
// so the page and the script cannot describe different behaviour: the stage
// list is the order load_target.py runs in, and the rules come from the same
// data dictionary the target is sent.
const LOAD_STAGES = [
  ["Headers", "Every column in the template definition. A missing mandatory "
   + "column stops that file. An unexpected column is reported and ignored, "
   + "because targets add columns and that is not an error."],
  ["Types", "Dates parse, decimals parse, whole numbers are whole. Currency "
   + "symbols, thousands separators and parenthesised negatives are accepted: "
   + "they arrive from every finance export and none of them is a defect."],
  ["Mandatory values", "Present. A blank in a mandatory column blocks the "
   + "row, because nothing downstream can be computed without it."],
  ["Enumerations", "Against the allowed list, case-insensitively. An "
   + "unmatched value is carried through unmapped for a person to decide "
   + "rather than discarded, which would turn a data problem into a missing "
   + "row."],
  ["Keys", "Unique within the file. A duplicate key double counts hours, "
   + "revenue or headcount depending on which file it is in."],
  ["References", "Resolve to a key, or to a name, in the file they point at. "
   + "A reference to a row that was itself rejected upstream is reported as a "
   + "consequence of that rejection rather than as missing data: one bad date "
   + "on an engagement can orphan a hundred timesheets, and reporting all "
   + "hundred the same way buries the single fix."],
  ["Cross-field sense", "An end date before a start, a go-live before a "
   + "start, negative hours, a person over twenty-four hours in a day, a "
   + "percent complete outside nought to a hundred, an invoice more than "
   + "three times the month's recognition. All of these parse perfectly and "
   + "none of them is true."],
];

function drawIntakeTab() {
  const o = org();
  const host = document.getElementById("intake-body");

  if (intakeTab === "load") {
    const tpl = (DATA.templates || []);
    host.innerHTML = `
      <div class="card" style="margin-bottom:18px">
        <h3>Loading a target's history</h3>
        <p class="sub">The eight templates go to the target, come back filled
          in, and load through one script. Everything on every other screen in
          this console is computed from what that script writes, so the
          validation in front of it is the whole quality control.</p>
        <div class="schema" style="margin:14px 0">
          <div class="note" style="margin-bottom:4px">1. Validate and write
            nothing. Do this first, every time.</div>
          <div>python3 py/load_target.py --dir &lt;folder&gt; \<br>
            &nbsp;&nbsp;&nbsp;&nbsp;--org-name '<b>Target name</b>'
            --org-code <b>TGT</b> --dry-run</div>
          <div class="note" style="margin:12px 0 4px">2. Load, then run the
            engine, the checks, the resourcing model, earned value, the
            economics and re-export the console.</div>
          <div>python3 py/load_target.py --dir &lt;folder&gt; \<br>
            &nbsp;&nbsp;&nbsp;&nbsp;--org-name '<b>Target name</b>'
            --org-code <b>TGT</b> --analyse</div>
        </div>
        <p class="note">A dry run applies the same rules the load uses, so a
          clean dry run means the load will not fail halfway. Add
          <span class="mono">--replace</span> to rebuild a target already
          loaded under that code, and <span class="mono">--json-report
          &lt;path&gt;</span> to keep the findings as a file. There is no merge
          path on purpose: a partial merge into a book somebody is reading puts
          two versions of the same engagement on one screen.</p>
      </div>

      <div class="card" style="margin-bottom:18px">
        <h3>What the validator checks, in this order</h3>
        <p class="sub">The order is not arbitrary. A type error makes every
          downstream check meaningless, so reporting "end date before start
          date" against a cell that is not a date is noise that hides the real
          finding. Each stage only sees the rows the previous one passed.</p>
        <div class="tblwrap"><table>
          <thead><tr><th style="width:60px">Stage</th><th></th></tr></thead>
          <tbody>${LOAD_STAGES.map(([name, detail], i) => `
            <tr><td class="tight"><b>${i + 1}. ${esc(name)}</b></td>
              <td class="note">${esc(detail)}</td></tr>`).join("")}
          </tbody>
          <caption>Errors block the row. Warnings load it and raise a data
            quality finding, because a target with a blank cost rate is still
            worth assessing and the blank is itself a finding. That asymmetry
            is the difference between an import that gets used and one that
            gets worked around with a hand-edited spreadsheet.</caption>
        </table></div>
      </div>

      <div class="card" style="margin-bottom:18px">
        <h3>What the loader has to supply itself</h3>
        <p class="sub">Four things this system needs are not in any of the
          eight templates, and mostly cannot be: a margin target on a
          practice, a utilisation target on a person, a weekly capacity, a
          cost rate to value a budget. They are the acquirer's operating
          parameters rather than the target's history.</p>
        <p style="margin:0 0 10px">So they are substituted from the acquirer's
          own book, and every substitution is recorded once per column with a
          count and a basis. That matters more than it sounds: the check
          catalogue asserts that a margin target exists for every practice and
          a utilisation target for every role. Defaulting quietly would make
          those checks pass against numbers the loader invented, and a
          readiness score built on defaults reads as readiness.</p>
        <p class="note">Each substitution appears in the data quality view as
          an intake finding, not as a silent default.</p>
      </div>

      <div class="card">
        <h3>The kit</h3>
        <p class="sub">Blank templates to send, a worked sample that loads end
          to end, and the dictionary that defines every column.</p>
        <div class="tblwrap"><table>
          <thead><tr><th>File</th><th>One row per</th>
            <th class="r">Mandatory</th><th class="r">Columns</th>
            <th>Minimum history</th></tr></thead>
          <tbody>${tpl.map(t => `
            <tr><td class="mono">${esc(t.template_code)}.csv</td>
              <td>${esc((t.template_name || "").toLowerCase())}</td>
              <td class="r num">${(t.fields || [])
                .filter(f => f.is_mandatory).length}</td>
              <td class="r num">${(t.fields || []).length}</td>
              <td class="note">${t.min_history_months
                ? t.min_history_months + " months" : "—"}</td></tr>`).join("")}
          </tbody>
          <caption>Send all eight. Where a target cannot produce one, say so
            explicitly rather than sending an empty file: a missing template is
            a scope limitation on the assessment and an empty one looks like a
            target with no risks and no issues.</caption>
        </table></div>
      </div>`;
    return;
  }

  if (intakeTab === "wizard") {
    host.innerHTML = `
      <div class="card">
        <h3>Validation report</h3>
        <p class="sub">One batch per template. Errors block the load; warnings need a
          decision. Correct the source and re-upload, or fix in place and re-validate.</p>
        ${o.import_batches.map(b => {
          const f = o.validation_findings[b.template_code] || [];
          const pctValid = b.row_count ? b.valid_count / b.row_count * 100 : 100;
          return `<div style="margin-top:16px;padding-top:14px;border-top:1px solid var(--line)">
            <div class="row">
              <div>
                <b style="font-family:var(--font-ui);font-size:14px">${esc(b.template_name)}</b>
                <div class="note">${esc(b.file_name || '')} · uploaded by ${esc(b.uploaded_by || '')}
                  ${b.min_history_months ? ` · ${b.min_history_months} months of history required` : ''}</div>
              </div>
              <div class="spacer"></div>
              ${pill(b.status === 'Committed' ? 'good' : b.status === 'Failed Validation' ? 'bad' : 'warn', b.status)}
            </div>
            <div class="row" style="margin:10px 0 6px;gap:18px">
              <span class="num"><b>${nf(b.row_count)}</b> <span class="note">rows</span></span>
              <span class="num" style="color:var(--good)">✓ ${nf(b.valid_count)} valid</span>
              ${b.warning_count ? `<span class="num" style="color:var(--warn)">⚠ ${nf(b.warning_count)} warning${b.warning_count === 1 ? '' : 's'}</span>` : ''}
              ${b.error_count ? `<span class="num" style="color:var(--bad)">✕ ${nf(b.error_count)} error${b.error_count === 1 ? '' : 's'}</span>` : ''}
              <div class="bar-mini" style="flex:1;min-width:140px"><span style="width:${pctValid}%;background:var(--good)"></span></div>
            </div>
            ${f.length ? `<details class="evidence" ${b.error_count ? "open" : ""}>
              <summary>${f.length} finding${f.length === 1 ? '' : 's'}</summary>
              <div class="tblwrap" style="margin-top:8px"><table>
                <thead><tr><th>Severity</th><th>Rule</th><th>Column</th><th>Finding</th><th>How to fix it</th></tr></thead>
                <tbody>${f.slice(0, 40).map(x => `<tr>
                  <td class="tight">${pill(x.severity === 'error' ? 'bad' : 'warn', x.severity)}</td>
                  <td class="mono note">${esc(x.rule_code)}</td>
                  <td class="note">${esc(x.column_header || '')}</td>
                  <td>${esc(x.message)}</td>
                  <td class="note">${esc(x.recommendation || '')}</td></tr>`).join("")}
                  ${f.length > 40 ? `<tr><td colspan="5" class="note">…and ${f.length - 40} more in the database.</td></tr>` : ""}
                </tbody></table></div></details>`
              : `<p class="note">No findings.</p>`}
          </div>`;
        }).join("")}
      </div>`;
    return;
  }

  if (intakeTab === "templates") {
    host.innerHTML = `
      <div class="card">
        <h3>The eight standard templates</h3>
        <p class="sub">Held as data, not as eight hard-coded spreadsheets, so the download,
          the validator and the mapping screen all read one definition. Mandatory columns
          are marked; a reference column names the template it must resolve against.</p>
        ${DATA.templates.map(t => `
          <details class="evidence" style="margin-top:12px;border-top:1px solid var(--line);padding-top:12px">
            <summary style="color:var(--ink);font-size:14px;font-weight:600;font-family:var(--font-ui)">
              ${esc(t.template_name)} <span class="note">· ${t.fields.length} columns · loads into
              <span class="mono">${esc(t.target_table)}</span>${t.min_history_months ? ` · ${t.min_history_months}m history` : ''}</span>
            </summary>
            <p class="note" style="margin:6px 0 8px">${esc(t.description || '')}</p>
            <div class="tblwrap"><table>
              <thead><tr><th style="width:34px">#</th><th>Column header</th><th>Type</th>
                <th>Required</th><th>Maps to</th><th>Allowed values / reference</th></tr></thead>
              <tbody>${t.fields.map(f => `<tr>
                <td class="num note">${f.column_order}</td>
                <td><b style="font-family:var(--font-ui);font-weight:500">${esc(f.column_header)}</b></td>
                <td class="note">${esc(f.data_type)}</td>
                <td>${f.is_mandatory ? pill('bad', 'required') : '<span class="note">optional</span>'}
                    ${f.is_key ? pill('accent', 'key') : ''}</td>
                <td class="mono note">${esc(f.target_column || '—')}</td>
                <td class="note">${esc(f.enum_values ? f.enum_values.split('|').join(', ')
                  : f.fk_template_code ? 'must exist in ' + f.fk_template_code : '')}</td>
              </tr>`).join("")}</tbody>
            </table></div>
          </details>`).join("")}
      </div>`;
    return;
  }

  if (intakeTab === "mapping") {
    const dims = Object.keys(o.mappings);
    host.innerHTML = dims.length ? `
      <div class="card">
        <h3>Legacy terminology mapping</h3>
        <p class="sub">The acquired company used its own vocabulary. Each legacy value is
          matched to a platform value with a confidence, and anything unmapped is shown
          rather than silently dropped.</p>
        <div class="grid g2" style="margin-top:12px">${dims.map(d => `
          <div>
            <h4 style="font-size:12px;text-transform:uppercase;letter-spacing:.07em;color:var(--ink-3);margin-bottom:6px">${esc(d.replace(/_/g, ' '))}</h4>
            <div class="tblwrap"><table>
              <thead><tr><th>Legacy value</th><th>Platform value</th><th class="r">Confidence</th>
                <th class="r">Rows</th><th>Source</th></tr></thead>
              <tbody>${o.mappings[d].map(r => `<tr>
                <td class="mono">${esc(r.legacy_value)}</td>
                <td>${r.target_value ? esc(r.target_value) : pill('bad', 'unmapped')}</td>
                <td class="r num">${r.target_value ? pc(r.confidence_pct, 0) : '—'}</td>
                <td class="r num">${nf(r.occurrence_count)}</td>
                <td class="note">${esc(r.match_source)}${r.approved ? '' : ' · unapproved'}</td>
              </tr>`).join("")}</tbody>
            </table></div>
          </div>`).join("")}</div>
      </div>` : `<div class="card"><p class="note">No mapping set for this organisation.</p></div>`;
    return;
  }

  // quality
  const dq = o.data_quality;
  host.innerHTML = `
    <div class="grid g2">
      <div class="card">
        <h3>Findings by category</h3>
        <p class="sub">Run against loaded data, not just the upload, so quality can be
          re-checked at any point.</p>
        <div id="ch-dq"></div>
      </div>
      <div class="card">
        <h3>Rules and counts</h3>
        <div class="tblwrap" style="margin-top:10px"><table>
          <thead><tr><th>Category</th><th>Rule</th><th>Severity</th><th class="r">Records</th></tr></thead>
          <tbody>${dq.summary.length ? dq.summary.map(r => `<tr>
            <td>${esc(r.category)}</td>
            <td class="mono note">${esc(r.rule_code)}</td>
            <td>${pill(r.severity === 'error' ? 'bad' : 'warn', r.severity)}</td>
            <td class="r num">${nf(r.n)}</td></tr>`).join("")
            : `<tr><td colspan="4" class="note">No data quality findings for this organisation.</td></tr>`}
          </tbody></table></div>
      </div>
    </div>
    ${dq.examples.length ? `<div class="card" style="margin-top:14px">
      <h3>The records themselves</h3>
      <p class="sub">Every finding names the record and how to fix it. This is the queue,
        not a summary of one.</p>
      <div class="tblwrap" style="max-height:52vh;overflow-y:auto"><table>
        <thead><tr><th>Severity</th><th>Category</th><th>Record</th><th>Finding</th><th>How to fix it</th></tr></thead>
        <tbody>${dq.examples.map(x => `<tr>
          <td class="tight">${pill(x.severity === 'error' ? 'bad' : 'warn', x.severity)}</td>
          <td class="note">${esc(x.category)}</td>
          <td class="mono">${esc(String(x.entity_label).slice(0, 40))}</td>
          <td>${esc(x.message)}</td>
          <td class="note">${esc(x.recommendation || '')}</td></tr>`).join("")}</tbody>
        <caption>First 120 findings; the database holds the full queue.</caption>
      </table></div>
    </div>` : ""}`;

  if (dq.summary.length) {
    const byCat = {};
    dq.summary.forEach(r => { byCat[r.category] = (byCat[r.category] || 0) + r.n; });
    hBars(document.getElementById("ch-dq"),
      Object.entries(byCat).sort((a, b) => b[1] - a[1]).map(([k, v]) => ({
        label: k, value: v, color: "var(--s2)",
        tipRows: [["Records", nf(v)]]
      })), { rowH: 26, labelW: 230, valueFormat: v => nf(v), tipFormat: v => nf(v) });
  } else {
    document.getElementById("ch-dq").innerHTML = `<p class="note">Nothing to plot.</p>`;
  }
}

/* ===================== How it works =================================== */
function renderHow(el) {
  const o = org(), a = DATA.assumptions;
  // Counted rather than written down, so the prose cannot drift away from the
  // catalogue the next time a family is added.
  const checkCount = o.checks ? o.checks.results.length : 0;
  const silentCount = o.checks
    ? o.checks.results.filter(r => r.enforcement === "silent").length : 0;
  el.innerHTML = `
    <div class="viewhead">
      <div class="eyebrow">Requirements 30 and 45</div>
      <h1>How it works</h1>
      <p>What the engine calculates, what it assumes, and where each number comes from.
         A predictive number that cannot be interrogated gets ignored the first time it is
         wrong, so this page is part of the product rather than an appendix to it.</p>
    </div>

    <div class="grid g2" style="margin-bottom:16px">
      <div class="card">
        <h3>What the engine reads on every run</h3>
        <p class="sub">One pass per organisation over the whole book of business.</p>
        <div class="grid g2" style="margin-top:10px">
          <ul style="margin:0;padding-left:18px">
            <li>Projects, plans and plan versions</li>
            <li>Tasks and milestones, including critical path</li>
            <li>Time entries and approval state</li>
            <li>Monthly revenue, cost and recognition</li>
            <li>Contracts, SOWs and change orders</li>
            <li>Risks and issues</li>
          </ul>
          <ul style="margin:0;padding-left:18px">
            <li>Resource assignments and capacity</li>
            <li>Cost and billing rates</li>
            <li>Pipeline and probability</li>
            <li>Forecast snapshots, original and revised</li>
            <li>Customer and practice attributes</li>
            <li>Completed project history, as the peer set</li>
          </ul>
        </div>
        <dl class="kv" style="margin-top:14px">
          <dt>Records in scope</dt><dd>${Object.entries(o.counts).map(([k, v]) =>
            `${nf(v)} ${k.replace(/_/g, ' ')}`).join(" · ")}</dd>
          <dt>Projects scored</dt><dd>${nf(o.run.projects_scored)} live and not-started</dd>
          <dt>Run time</dt><dd>${nf(o.run.runtime_ms)} ms</dd>
          <dt>Engine version</dt><dd class="mono">${esc(DATA.engine_version)}</dd>
        </dl>
      </div>

      <div class="card">
        <h3>Definitions that are easy to get wrong</h3>
        <div class="tblwrap" style="margin-top:10px"><table><tbody>
          <tr><td><b>Margin to date</b></td><td>Recognised revenue to date less cost incurred
            to date. An in-flight project is not credited with revenue it has not earned.</td></tr>
          <tr><td><b>Predicted margin</b></td><td>Revenue at completion less predicted cost
            at completion. Predicted cost is the estimate plus named additions for burn
            rate, peer history, schedule slip, rate mix, and whichever bound binds. Built
            that way the additions sum exactly to the distance between the margin in the
            estimate and the predicted margin, which the verification suite checks on every
            project.</td></tr>
          <tr><td><b>Revenue at completion</b></td><td>Fixed price and milestone work earns
            the contract plus approved change orders. Time and materials earns the expected
            billable hours at the realised rate, capped at the ceiling where one exists.
            Using the value billed so far as the denominator would make every live T&amp;M
            project look catastrophic.</td></tr>
          <tr><td><b>Physical progress</b></td><td>Earned planned hours over total planned
            hours. Not an average of task percentages, which would weight a two-hour
            sign-off the same as a 400-hour build.</td></tr>
          <tr><td><b>Utilisation</b></td><td>Billable hours over capacity hours. The current
            partial month is excluded from every trend measure.</td></tr>
          <tr><td><b>Net hourly rate</b></td><td>Recognised revenue over hours actually
            booked, not budget per hour.</td></tr>
          <tr><td><b>Ledger against timesheets</b></td><td>In a native tenant the monthly
            cost ledger is built from approved time, so the two tie by construction. An
            acquired extract arrives as two separate files and often does not tie; where
            it does not, the gap is raised as a data quality finding rather than
            reconciled away.</td></tr>
          <tr><td><b>Revenue at risk</b></td><td>Revenue at completion on live projects the
            engine predicts will land negative, or predicts red overall. Deliberately
            narrow. A wider definition that swept in everything below an ambitious target
            put a quarter of the book on the number, which made it useless; that broader
            figure is reported separately as revenue below target margin.</td></tr>
          <tr><td><b>Peer cohort</b></td><td>Completed projects scored on product, type,
            methodology, billing model, practice and size band. The threshold relaxes
            rather than returning a cohort of two.</td></tr>
        </tbody></table></div>
      </div>
    </div>

    <div class="grid g2" style="margin-bottom:16px">
      <div class="card">
        <h3>Assumptions, in one place</h3>
        <p class="sub">These are judgement calls. They are held as named constants so they
          can be recalibrated against outturns rather than argued about in a meeting.</p>
        <div class="tblwrap" style="margin-top:10px"><table><tbody>
          <tr><td>Overhead allowance for the EBITDA proxy</td><td class="r num">${pc(a.overhead_rate * 100, 0)} of revenue</td></tr>
          <tr><td>Assessment window</td><td class="r num">${a.assessment_months} complete months</td></tr>
          <tr><td>Capacity horizon</td><td class="r num">${a.horizon_months} months</td></tr>
          <tr><td>Share of the book repriceable within a year</td><td class="r num">${pc(a.reprice_share * 100, 0)}</td></tr>
          <tr><td>Ramp to full utilisation for a new hire</td><td class="r num">${nf(a.ramp_months)} months</td></tr>
          <tr><td>Extra cost per month of delay</td><td class="r num">${pc(a.delay_idle_factor * 100, 0)} of remaining work</td></tr>
          <tr><td>Capacity hours per FTE per year</td><td class="r num">${nf(a.annual_hours)}</td></tr>
          <tr><td>Failure probability blend</td><td class="r num">45% margin, 35% budget, 20% schedule</td></tr>
        </tbody></table></div>
        <h4 style="margin:18px 0 6px;font-size:13px">How the probabilities are calibrated</h4>
        <p class="note" style="margin:0 0 8px">The weights on each signal are priors: they
          encode which conditions matter and roughly how much. The intercepts are not
          guessed. On every run the engine measures this organisation's actual outturn rate
          on completed projects and shifts each intercept until the mean predicted
          probability across the live book equals that rate. A hand-set intercept was
          giving a flawless project a 25% failure probability, which put the whole
          portfolio in alarm.</p>
        <div class="tblwrap"><table>
          <thead><tr><th>Outcome</th><th class="r">Observed rate</th><th class="r">Fitted intercept</th></tr></thead>
          <tbody>
            <tr><td>Exceeded hours budget</td><td class="r num">${pc((o.run.base_rates.budget || 0) * 100)}</td>
              <td class="r num mono">${nf(o.run.intercepts.budget, 2)}</td></tr>
            <tr><td>Delivered more than ten days late</td><td class="r num">${pc((o.run.base_rates.schedule || 0) * 100)}</td>
              <td class="r num mono">${nf(o.run.intercepts.schedule, 2)}</td></tr>
            <tr><td>Landed below target margin</td><td class="r num">${pc((o.run.base_rates.margin || 0) * 100)}</td>
              <td class="r num mono">${nf(o.run.intercepts.margin, 2)}</td></tr>
            <tr><td>Blended base rate</td><td class="r num">${pc((o.run.base_rates.failure || 0) * 100)}</td>
              <td class="r note">measured on ${nf(o.run.base_rates.sample)} completed projects</td></tr>
          </tbody></table></div>
        <p class="note" style="margin-top:8px">Health bands are relative to that base rate:
          red at ${pc((o.run.base_rates.failure || 0) * 100 + 18, 0)} or worse, yellow at
          ${pc((o.run.base_rates.failure || 0) * 100 + 7, 0)}. In a business where
          ${pc((o.run.base_rates.budget || 0) * 100, 0)} of projects historically overran,
          a fixed 50% threshold would flag almost everything.</p>
        <p class="note" style="margin-top:8px">Calibrating the mean is not the same as being
          calibrated across the range. Before anyone treats a 78% as meaning 78%, the
          predictions need checking bucket by bucket against what actually happened.</p>
      </div>

      <div class="card">
        <h3>Where the numbers are stored</h3>
        <p class="sub">Every engine output is written back with its drivers, so a figure
          shown here can be re-audited later rather than regenerated.</p>
        <div class="schema" style="margin-top:10px">
          <div><b>project_risk_score</b> + <b>risk_driver</b> — probabilities and the reasons</div>
          <div><b>project_margin_prediction</b> + <b>margin_driver</b> — cost build-up in margin points</div>
          <div><b>project_benchmark</b> + <b>benchmark_peer</b> — cohort statistics and its members</div>
          <div><b>profitability_cut</b> — ten dimensions with flags</div>
          <div><b>acquisition_metric</b> — assessment measures with benchmark values</div>
          <div><b>red_flag</b> — findings with evidence rows attached</div>
          <div><b>capacity_month</b> · <b>future_risk_prediction</b> — supply, demand, outlook</div>
          <div><b>forecast_accuracy</b> — variance by PM, practice, customer and org</div>
          <div><b>scenario</b> + <b>scenario_result</b> — saved what-ifs with parameters</div>
          <div><b>deal_assessment</b> + factors + recommendations — go / no-go decisions</div>
          <div><b>ps_os_snapshot</b> · <b>leadership_action</b> · <b>executive_briefing</b></div>
          <div><b>import_batch</b> · <b>import_staging_row</b> · <b>validation_finding</b></div>
          <div><b>mapping_set</b> + <b>mapping_rule</b> — legacy vocabulary</div>
          <div><b>migration_run</b> + <b>migration_lineage</b> — legacy IDs retained</div>
          <div><b>data_quality_finding</b> — the standing quality queue</div>
          <div><b>audit_log</b> · <b>project_plan_version</b> — who changed what, and which plan</div>
          <div><b>delivery_check</b> + <b>check_result</b> + <b>check_finding</b> — the rules, held as data</div>
          <div><b>metric_definition</b> — this engine's arithmetic beside the vendor's</div>
          <div><b>resource_plan</b> + line + month + action + sensitivity — the capacity model and its inputs</div>
          <div><b>csat_response</b> + <b>csat_summary</b> — verbatims and the roll-up derived from them</div>
          <div><b>phase_duration_analysis</b> + <b>phase_slip_cause</b> — which phase runs long, and why</div>
          <div><b>hypercare_period</b> + <b>hypercare_summary</b> — go live to exit, and who paid</div>
          <div><b>rate_analysis</b> — list, sold, per billable hour, per hour delivered</div>
          <div><b>performance_cycle</b> + <b>performance_review</b> — ratings beside their measures</div>
          <div><b>product_ticket</b> + <b>product_ticket_summary</b> — the defect backlog and its cost</div>
          <div><b>rag_register</b> + <b>rag_reason</b> — red and amber with the reasons attached</div>
        </div>
      </div>
    </div>

    <div class="card" style="margin-bottom:16px">
      <h3>Where the delivery rules came from</h3>
      <p class="sub">The check catalogue is not invented. It is built from what the
        established products in this category do and, more usefully, what they do not.</p>
      <div class="grid g2">
        <div>
          <p style="margin:0 0 8px">Reviewing Certinia and Rocketlane against this
            problem produces one clear conclusion: both ship the <em>structure</em> for
            delivery governance and leave the rules to the customer. Certinia gives you
            four Green/Yellow/Red picklists on the project with no shipped scoring logic
            and a note that automation is available through Salesforce Flow Builder.
            Rocketlane gives you a free-text status and a manual at-risk flag. Neither
            documents a resource over-allocation rule, and Rocketlane documents the
            absence of one outright.</p>
          <p style="margin:0">So the rules are the product here, which is why they are
            held as rows rather than code. Each check names what it asserts, why that
            matters, whether the source platform blocks the condition or merely reports
            it, and where the semantics come from. ${silentCount} of the
            ${checkCount} are conditions neither product prevents nor
            surfaces.</p>
        </div>
        <div>
          <p style="margin:0 0 8px">Three specific divergences are worth knowing about,
            and all three are published in the metric definitions table:</p>
          <ul style="margin:0;padding-left:20px">
            <li>Certinia's billing-event eligibility is a conjunction of flags. A record
              failing any one of them is silently left out of the invoice, so the work is
              delivered, the cost is incurred and nothing is raised.</li>
            <li>Certinia's percent complete is hours spent over estimate at completion,
              which means progress <em>rises</em> when you overspend. This engine derives
              progress from earned plan instead, so effort and progress are two views of
              one fact and cannot disagree.</li>
            <li>Rocketlane caps fixed-fee recognition at 100% of the fee. This engine
              does the same and checks it, because the alternative is recognising revenue
              nobody agreed to pay.</li>
          </ul>
        </div>
      </div>
    </div>

    <div class="card">
      <h3>What this is not</h3>
      <p class="sub">Worth being direct about, because the gap between a demonstration and
        a product is usually hidden.</p>
      <ul style="margin:8px 0 0;padding-left:20px">
        <li>The dataset is generated. It is internally consistent and shaped to contain
          the problems the requirements describe, but it is not anyone's real book of
          business, and no figure here should be quoted.</li>
        <li>The risk coefficients have not been fitted to outturns, because there are no
          outturns yet. Treat the probabilities as an ordering of attention, not as
          calibrated odds.</li>
        <li>The EBITDA proxy uses a flat overhead assumption. A real assessment needs the
          target's actual overhead, contract terms and working capital position.</li>
        <li>Time entry, approvals, billing and rev rec are modelled in the schema but this
          console is read-only. Nothing here writes back.</li>
        <li>Peer cohorts are drawn within one organisation. Cross-organisation
          benchmarking after an acquisition needs the rate and cost bases normalised
          first.</li>
        <li>The check catalogue's enforcement column describes the two products as their
          own documentation describes them, at the point it was read. Both ship
          frequently, and a condition that is silent today may be validated in the next
          release. The claim to check is the citation on each row, not the column.</li>
        <li>The capacity model's demand beyond the pipeline window is the line's trailing
          run rate. That is a better assumption than zero and it is still an assumption:
          the coverage figures on each line say how much of the plan rests on it, and a
          line at forty per cent run-rate coverage is a forecast rather than a
          commitment.</li>
        <li>The apportionment of phase slip across its causes is an apportionment, not a
          measurement. The incidence beside each cause is measured; the day split is
          proportional to it, and is there to give a sense of scale rather than to
          attribute blame.</li>
        <li>The performance ratings here are generated from each person's own measured
          utilisation, satisfaction and delivery record plus a persistent individual
          component. That makes the correlation between rating and utilisation a real
          property of this dataset rather than a claim about any real appraisal process.</li>
        <li>Satisfaction scores and verbatims are generated to match each engagement's
          actual outcome, so the relationship between a red project and a poor score is
          real here by construction. In a live system it has to be earned by actually
          collecting the survey.</li>
      </ul>
    </div>`;
}
