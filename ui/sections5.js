/* =========================================================================
   Resource requirement by product line, and the diligence pack.

   Two views, one idea: every number shows the inputs it came from.

   The capacity plan reads its assumptions out of the stored plan row rather
   than out of a formula, because the first question anyone senior asks a
   resourcing model is "what did you assume" and the second is "where did that
   come from". The diligence pack keeps the verbatim comment beside the CSAT
   average, the reasons beside the red project, and the attributed cause
   beside the phase that slipped, because a summary whose evidence lives on
   another screen drifts from its own explanation within a week.
   ========================================================================= */
"use strict";

let resLine = null;
let digTab = "csat";
let commentFilter = "all";
let reviewPerson = null;

const BAND_CLASS = { short: "bad", balanced: "good", bench: "warn" };
const VERDICT_CLASS = {
  hire: "bad", subcontract: "warn", rephase: "bad", "reset target": "bad",
  "cross-train": "warn", "sell more": "warn", hold: "good",
};

/* =====================  Resource requirement  ========================== */
function renderResourcing(el) {
  const o = org(), r = o.resourcing;
  if (!r) {
    el.innerHTML = `<div class="card"><h3>No plan</h3>
      <p class="note">The resource model has not run against this
      organisation.</p></div>`;
    return;
  }
  const p = r.plan, lines = r.lines;
  if (!resLine || !lines.some(l => l.product_line === resLine)) {
    resLine = lines[0] && lines[0].product_line;
  }
  const totalHire = lines.reduce((a, l) => a + (l.hire_count || 0), 0);
  const totalBackfill = lines.reduce((a, l) => a + (l.attrition_backfill_fte || 0), 0);
  const totalFte = lines.reduce((a, l) => a + l.existing_fte, 0);
  const atRisk = lines.reduce((a, l) => a + (l.revenue_at_risk || 0), 0);

  el.innerHTML = `
    <div class="viewhead">
      <div class="eyebrow">Resource requirement by product line</div>
      <h1>How many consultants, in which line, by when</h1>
      <p>Demand is committed backlog plus probability-weighted pipeline, with
         the line's own trailing run rate as the floor in months the pipeline
         cannot yet see. Supply is the workforce as it stands, attributed to
         each line by the hours each individual actually booked to it, at the
         utilisation the work was priced at. Attrition is assumed backfilled
         and reported separately, so the gap below is net new capacity rather
         than the cost of standing still.</p>
    </div>

    <div class="grid g4" style="margin-bottom:22px">
      <div class="tile stripe ${totalHire > 4 ? 'bad' : totalHire ? 'warn' : 'good'}">
        <div class="label">Net new hires</div>
        <div class="value">${nf(totalHire)}</div>
        <div class="foot">across ${lines.length} product lines,
          ${p.horizon_months} months</div>
      </div>
      <div class="tile">
        <div class="label">Backfill</div>
        <div class="value">${nf(totalBackfill, 1)}</div>
        <div class="foot">FTE to replace leavers at
          ${pc(p.attrition_pct_annual, 0)} attrition</div>
      </div>
      <div class="tile">
        <div class="label">Attributed capacity</div>
        <div class="value">${nf(totalFte, 1)}</div>
        <div class="foot">billable FTE across the lines</div>
      </div>
      <div class="tile stripe ${atRisk > 0 ? 'warn' : 'good'}">
        <div class="label">Pipeline at risk</div>
        <div class="value sm">${moneyK(atRisk)}</div>
        <div class="foot">weighted value the current team cannot serve</div>
      </div>
    </div>

    <div class="card" style="margin-bottom:18px">
      <h3>Assumptions</h3>
      <p class="sub">Stored on the plan and read back by the model, so what you
        see is what it ran on.</p>
      <div class="grid g4">
        <div class="kv">
          <dt>Recruitment lead time</dt><dd>${nf(p.recruit_lead_weeks, 0)} weeks</dd>
          <dt>Annual attrition</dt><dd>${pc(p.attrition_pct_annual, 0)}</dd>
        </div>
        <div class="kv">
          <dt>Standard week</dt><dd>${nf(p.standard_hours_per_week, 1)} hours</dd>
          <dt>Working weeks a year</dt><dd>${nf(p.working_weeks_per_year, 0)}</dd>
        </div>
        <div class="kv">
          <dt>Horizon</dt><dd>${p.horizon_months} months from ${esc(p.as_of_date)}</dd>
          <dt>Ramp to productive</dt><dd>${nf(lines[0] ? lines[0].ramp_weeks : 0, 0)} weeks</dd>
        </div>
        <div class="kv">
          <dt>Training reserved</dt><dd>${nf(lines[0] ? lines[0].training_hours_per_year : 0, 0)} hours a year</dd>
          <dt>Plan run</dt><dd>${esc((p.created_at || "").slice(0, 16).replace("T", " "))}</dd>
        </div>
      </div>
    </div>

    <div class="card" style="margin-bottom:18px">
      <h3>The lines</h3>
      <p class="sub">Sorted by the peak shortfall. The verdict runs on two axes,
        how deep the gap is and how long it lasts, because the same number of
        FTE-months is a subcontract if it arrives in two months and a hire if
        it spreads over nine.</p>
      <div class="tblwrap"><table>
        <thead><tr>
          <th>Product line</th><th class="r">Go live</th>
          <th class="r">People</th><th class="r">FTE</th>
          <th class="r">Backlog</th><th class="r">Pipeline</th>
          <th class="r">vs target</th>
          <th class="r">Months short</th><th class="r">Avg gap</th>
          <th class="r">Peak</th><th class="r">Hire</th><th>Verdict</th>
        </tr></thead>
        <tbody>${lines.map(l => `
          <tr class="clickable" data-line="${esc(l.product_line)}"
              aria-selected="${l.product_line === resLine}">
            <td>${esc(l.product_line)}
              <div class="note">${esc(l.practice_label || "")}</div></td>
            <td class="r num">${nf(l.time_to_go_live_weeks, 1)}w</td>
            <td class="r num">${nf(l.existing_consultants, 0)}</td>
            <td class="r num">${nf(l.existing_fte, 1)}</td>
            <td class="r num">${nf(l.backlog_hours, 0)}</td>
            <td class="r num">${nf(l.pipeline_weighted_hours, 0)}</td>
            <td class="r num">${l.over_target_pct === null ? "—"
              : `<span class="delta ${l.over_target_pct > 12 ? 'down'
                : l.over_target_pct < -20 ? 'down' : 'flat'}">${l.over_target_pct > 0 ? '+' : ''}${nf(l.over_target_pct, 0)}%</span>`}</td>
            <td class="r num">${l.months_short}/${p.horizon_months}</td>
            <td class="r num">${nf(l.avg_gap_when_short, 1)}</td>
            <td class="r num">${nf(l.peak_gap_fte, 1)}</td>
            <td class="r num">${l.hire_count || "—"}</td>
            <td class="tight">${pill(VERDICT_CLASS[l.verdict] || "mute",
              l.verdict)}</td>
          </tr>`).join("")}
        </tbody>
        <caption>Go live is the measured median duration of a completed
          implementation on that line. FTE is attributed from hours actually
          delivered, so it is smaller than the head count and the two answer
          different questions. <b>vs target</b> is how far the line has been
          running from the utilisation the work was priced at, and where that
          number is large it explains most of the shortfall beside it: a line
          delivering thirty per cent above its own target will read short in
          every month, and the first question there is whether the target is
          still right rather than how many people to recruit.</caption>
      </table></div>
    </div>

    <div id="res-detail"></div>`;

  el.querySelectorAll("tr[data-line]").forEach(tr =>
    tr.addEventListener("click", () => {
      resLine = tr.dataset.line;
      render();
    }));
  drawResDetail(r);
}

function drawResDetail(r) {
  const host = document.getElementById("res-detail");
  if (!host) return;
  const l = r.lines.find(x => x.product_line === resLine);
  if (!l) { host.innerHTML = ""; return; }
  const p = r.plan;
  const measured = (l.time_to_go_live_source || "").startsWith("measured");

  host.innerHTML = `
    <div class="grid g2" style="margin-bottom:18px">
      <div class="card">
        <h3>${esc(l.product_line)}: inputs</h3>
        <p class="sub">Everything the requirement is derived from, and whether
          it was measured or assumed.</p>
        <div class="tblwrap"><table><tbody>
          <tr><td>Time to go live</td>
            <td class="r num">${nf(l.time_to_go_live_weeks, 1)} weeks</td>
            <td>${pill(measured ? "good" : "warn", measured ? "measured" : "assumed")}
              <div class="note">${esc(l.time_to_go_live_source)}</div></td></tr>
          <tr><td>Existing consultants</td>
            <td class="r num">${nf(l.existing_consultants, 0)}</td>
            <td class="note">people who booked time to this line</td></tr>
          <tr><td>Attributed FTE</td>
            <td class="r num">${nf(l.existing_fte, 2)}</td>
            <td class="note">capacity-equivalent, from hours delivered</td></tr>
          <tr><td>Billable utilisation target</td>
            <td class="r num">${pc(l.billable_util_target_pct, 0)}</td>
            <td class="note">the rate the work was priced at</td></tr>
          <tr><td>Productive utilisation target</td>
            <td class="r num">${pc(l.productive_util_target_pct, 0)}</td>
            <td class="note">billable plus presales, enablement and internal
              delivery</td></tr>
          <tr><td>Training reserved</td>
            <td class="r num">${nf(l.training_hours_per_year, 0)} h/yr</td>
            <td class="note">removed from capacity before it is sold</td></tr>
          <tr><td>Ramp to full productivity</td>
            <td class="r num">${nf(l.ramp_weeks, 0)} weeks</td>
            <td class="note">at ${pc(l.ramp_util_pct, 0)} during ramp</td></tr>
          <tr><td>Committed backlog</td>
            <td class="r num">${nf(l.backlog_hours, 0)} h</td>
            <td class="note">${l.backlog_months_at_run_rate
              ? nf(l.backlog_months_at_run_rate, 1) + " months at the rate this line delivers"
              : ""}</td></tr>
          <tr><td>Pipeline</td>
            <td class="r num">${nf(l.pipeline_hours, 0)} h</td>
            <td class="note">${moneyK(l.pipeline_value)} unweighted</td></tr>
          <tr><td>Pipeline, weighted</td>
            <td class="r num">${nf(l.pipeline_weighted_hours, 0)} h</td>
            <td class="note">at stage probability</td></tr>
          <tr><td>Trailing run rate</td>
            <td class="r num">${nf(l.run_rate_hours_month, 0)} h/mo</td>
            <td class="note">the floor in months the pipeline cannot see</td></tr>
          <tr><td>Against the priced target</td>
            <td class="r num">${l.over_target_pct === null ? "—"
              : (l.over_target_pct > 0 ? "+" : "") + nf(l.over_target_pct, 0) + "%"}</td>
            <td class="note">run rate against what this team can bill at
              target</td></tr>
          <tr><td>Revenue per hour delivered</td>
            <td class="r num">${l.realised_rate ? money(l.realised_rate, 0) : "—"}</td>
            <td class="note">every hour on the line, billable or not — the
              basis the pipeline at risk is valued on</td></tr>
          <tr><td>Sellable hours per FTE</td>
            <td class="r num">${nf(l.productive_hours_per_fte, 0)} h/mo</td>
            <td class="note">after non-working weeks, training and the
              utilisation target</td></tr>
        </tbody></table></div>
      </div>
      <div class="grid" style="align-content:start">
        <div class="card">
          <h3>The requirement</h3>
          <p class="sub">${esc(l.statement)}</p>
          <div class="grid g3" style="margin-top:4px">
            <div class="tile stripe ${l.verdict === 'hold' ? 'good' : l.verdict === 'hire' || l.verdict === 'rephase' ? 'bad' : 'warn'}">
              <div class="label">Verdict</div>
              <div class="value sm">${esc(l.verdict)}</div>
              <div class="foot">${l.hire_count
                ? l.hire_count + " net new FTE" : "no net new hires"}</div>
            </div>
            <div class="tile">
              <div class="label">Required FTE</div>
              <div class="value">${nf(l.required_fte, 1)}</div>
              <div class="foot">against ${nf(l.existing_fte, 1)} attributed</div>
            </div>
            <div class="tile${l.verdict === "hire" ? " stripe warn" : ""}">
              <div class="label">${l.verdict === "hire" ? "Hire by"
                : "Recruitment window"}</div>
              <div class="value sm">${l.verdict === "hire"
                ? (l.hire_by_date ? esc(l.hire_by_date) : "—")
                : "not the lever"}</div>
              <div class="foot">${l.verdict === "hire"
                ? nf(p.recruit_lead_weeks + l.ramp_weeks, 0)
                  + " weeks behind the requisition"
                : "a requisition takes "
                  + nf(p.recruit_lead_weeks + l.ramp_weeks, 0)
                  + " weeks to become productive, which is longer than this "
                  + "gap lasts"}</div>
            </div>
          </div>
          <p class="note" style="margin-top:10px">Demand coverage:
            ${pc(l.coverage_committed_pct, 0)} contracted,
            ${pc(l.coverage_pipeline_pct, 0)} weighted pipeline,
            ${pc(l.coverage_runrate_pct, 0)} run-rate expectation. The last of
            those is the part of the plan that rests on the line continuing to
            sell what it has been selling.</p>
        </div>
        <div class="card">
          <h3>What would have to change</h3>
          <p class="sub">One lever at a time, measured as the average FTE short
            across the whole horizon.</p>
          <div class="tblwrap"><table>
            <thead><tr><th>Lever</th><th>Move</th><th class="r">Before</th>
              <th class="r">After</th><th class="r">Change</th></tr></thead>
            <tbody>${(l.sensitivity || []).map(s => `
              <tr><td>${esc(s.lever_label)}</td>
                  <td class="tight note">${esc(s.shift)}</td>
                  <td class="r num">${nf(s.fte_gap_before, 2)}</td>
                  <td class="r num">${nf(s.fte_gap_after, 2)}</td>
                  <td class="r num"><span class="delta ${s.fte_delta < -0.05 ? 'up'
                    : s.fte_delta > 0.05 ? 'down' : 'flat'}">${s.fte_delta > 0 ? '+' : ''}${nf(s.fte_delta, 2)}</span></td></tr>
              <tr><td colspan="5" class="note" style="padding-top:0">
                ${esc(s.statement)}</td></tr>`).join("")}
            </tbody></table></div>
        </div>
      </div>
    </div>

    <div class="card" style="margin-bottom:18px">
      <h3>Month by month</h3>
      <p class="sub">Demand split by how much of it is contracted, sold, or
        expected. Supply is what the current team can bill at target.</p>
      <div class="chartbox" id="res-chart"></div>
      <div class="tblwrap" style="margin-top:12px"><table>
        <thead><tr><th>Month</th><th class="r">Backlog</th>
          <th class="r">Pipeline</th><th class="r">Run rate</th>
          <th class="r">Demand</th><th class="r">Billable supply</th>
          <th class="r">Gap</th><th class="r">FTE</th>
          <th class="r">Visible</th><th>Band</th></tr></thead>
        <tbody>${(l.months || []).map(m => `
          <tr><td class="tight">${monthLabel(m.period_month)}</td>
            <td class="r num">${nf(m.backlog_hours, 0)}</td>
            <td class="r num">${nf(m.pipeline_hours, 0)}</td>
            <td class="r num">${nf(m.unsold_hours, 0)}</td>
            <td class="r num">${nf(m.demand_hours, 0)}</td>
            <td class="r num">${nf(m.billable_hours, 0)}</td>
            <td class="r num">${nf(m.gap_hours, 0)}</td>
            <td class="r num">${nf(m.gap_fte, 1)}</td>
            <td class="r num">${pc(m.visibility_pct, 0)}</td>
            <td class="tight">${pill(BAND_CLASS[m.band] || "mute", m.band)}</td>
          </tr>`).join("")}
        </tbody></table></div>
    </div>

    <div class="card">
      <h3>Actions</h3>
      <p class="sub">Ranked, with the date the decision stops being available.</p>
      <div class="grid g2">${(l.actions || []).map(a => `
        <div class="flag ${a.action_type === 'hire' ? 'High'
          : a.action_type === 'hold' ? 'Low' : 'Medium'}">
          <h4>${esc(a.headline)}</h4>
          <p class="detail">${esc(a.rationale)}</p>
          <div class="action">
            <b>Commitment</b>
            ${a.quantity ? nf(a.quantity, 1) + " " + esc(a.unit || "") : "—"}
            ${a.by_date ? " · by " + esc(a.by_date) : ""}
            ${a.value_at_stake ? " · " + moneyK(a.value_at_stake) + " at stake" : ""}
          </div>
        </div>`).join("")}
      </div>
    </div>`;

  const host2 = document.getElementById("res-chart");
  if (host2 && l.months && l.months.length) {
    const series = [
      { label: "Contracted backlog", color: "var(--s1)" },
      { label: "Weighted pipeline", color: "var(--s2)" },
      { label: "Run-rate expectation", color: "var(--s4)" },
      { label: "Billable supply", color: "var(--bad)" },
    ];
    groupedBars(host2, l.months.map(m => ({
      label: monthLabel(m.period_month).slice(0, 3),
      values: [m.backlog_hours, m.pipeline_hours, m.unsold_hours,
               m.billable_hours],
    })), series, { height: 240, tickFormat: v => nf(v) });
    host2.insertAdjacentHTML("afterbegin", legend(series));
  }
}

/* =========================  Diligence pack  ============================ */
const DIG_TABS = [
  ["csat", "Satisfaction and verbatims"],
  ["rag", "Red and amber"],
  ["phase", "Phase duration"],
  ["hypercare", "Hypercare"],
  ["rates", "Bill rates"],
  ["team", "Performance cycles"],
  ["product", "Bugs and enhancements"],
];

function renderDiligence(el) {
  const o = org(), d = o.diligence;
  if (!d) {
    el.innerHTML = `<div class="card"><h3>No diligence pack</h3></div>`;
    return;
  }
  const orgCsat = d.csat.find(c => c.scope === "org");
  const reds = d.rag.filter(r => r.reported_health === "Red").length;
  const ambers = d.rag.filter(r => r.reported_health === "Yellow").length;
  const orgHc = d.hypercare.find(h => h.scope === "org");
  const orgRate = d.rates.find(r => r.dimension === "org");
  const orgPerf = d.performance.find(p => p.scope === "org");

  el.innerHTML = `
    <div class="viewhead">
      <div class="eyebrow">Diligence pack</div>
      <h1>The questions asked on day one</h1>
      <p>Seven questions a PSA product will show you a chart for and none of
         which it will answer. Every derived number here carries the evidence
         it came from: the verbatim beside the average, the reasons beside the
         red project, the attributed cause beside the phase that slipped. A
         summary whose evidence lives on another screen stops agreeing with
         itself within a week.</p>
    </div>

    <div class="grid g4" style="margin-bottom:22px">
      <div class="tile stripe ${orgCsat && orgCsat.avg_score >= 4.2 ? 'good'
        : orgCsat && orgCsat.avg_score >= 3.8 ? 'warn' : 'bad'}">
        <div class="label">Satisfaction</div>
        <div class="value">${orgCsat ? nf(orgCsat.avg_score, 2) : "—"}</div>
        <div class="foot">${orgCsat ? nf(orgCsat.responses) + " responses · "
          + pc(orgCsat.reference_rate, 0) + " would reference" : ""}</div>
      </div>
      <div class="tile stripe ${reds ? 'bad' : ambers ? 'warn' : 'good'}">
        <div class="label">Red and amber</div>
        <div class="value">${reds} / ${ambers}</div>
        <div class="foot">${d.rag.length} engagements on the register</div>
      </div>
      <div class="tile stripe ${orgHc && orgHc.overrun_days_med > 10 ? 'bad' : 'warn'}">
        <div class="label">Hypercare</div>
        <div class="value">${orgHc ? nf(orgHc.actual_days_med, 0) : "—"}d</div>
        <div class="foot">${orgHc ? "planned " + nf(orgHc.planned_days_med, 0)
          + "d · " + pc(orgHc.billable_share_pct, 0) + " billable" : ""}</div>
      </div>
      <div class="tile">
        <div class="label">Average bill rate</div>
        <div class="value sm">${orgRate ? money(orgRate.booked_rate_avg, 0) : "—"}</div>
        <div class="foot">${orgRate ? "list " + money(orgRate.list_rate_avg, 0)
          + " · " + pc(orgRate.discount_pct, 1) + " discount" : ""}</div>
      </div>
    </div>

    <div class="chips" style="margin-bottom:16px">
      ${DIG_TABS.map(([id, label]) =>
        `<button class="chip" data-t="${id}"
           aria-pressed="${digTab === id}">${label}</button>`).join("")}
    </div>
    <div id="dig-body"></div>`;

  el.querySelectorAll(".chip[data-t]").forEach(b =>
    b.addEventListener("click", () => { digTab = b.dataset.t; render(); }));
  drawDiligenceTab(d);
}

function drawDiligenceTab(d) {
  const host = document.getElementById("dig-body");
  if (!host) return;
  ({
    csat: digCsat, rag: digRag, phase: digPhase, hypercare: digHypercare,
    rates: digRates, team: digTeam, product: digProduct,
  })[digTab](host, d);
}

/* ---- satisfaction ---------------------------------------------------- */
function digCsat(host, d) {
  const byScope = s => d.csat.filter(c => c.scope === s);
  const comments = d.comments.filter(c =>
    commentFilter === "all" ||
    (commentFilter === "critical" && c.score <= 3) ||
    (commentFilter === "strong" && c.score >= 4.5) ||
    (commentFilter === c.theme));
  const themes = [...new Set(d.comments.map(c => c.theme).filter(Boolean))];

  host.innerHTML = `
    <div class="grid g2" style="margin-bottom:18px">
      <div class="card">
        <h3>Through the engagement</h3>
        <p class="sub">The same customers, asked at five points. The shape of
          this curve is the finding.</p>
        <div class="chartbox" id="csat-chart"></div>
        <p class="note" style="margin-top:8px">Kickoff scores optimism and
          hypercare exit scores reality. A book where the two are close is
          delivering what it sold; a book where they diverge is selling
          something it does not deliver, and the gap is measurable rather than
          a matter of opinion.</p>
      </div>
      <div class="card">
        <h3>By product line</h3>
        <p class="sub">Average, movement on the prior year, and the theme that
          dominates the critical comments.</p>
        <div class="tblwrap"><table>
          <thead><tr><th>Line</th><th class="r">Score</th><th class="r">Change</th>
            <th class="r">Detractors</th><th>Top critical theme</th></tr></thead>
          <tbody>${byScope("product").sort((a, b) => a.avg_score - b.avg_score)
            .map(c => `
            <tr><td>${esc(c.scope_label)}</td>
              <td class="r num">${nf(c.avg_score, 2)}
                <div class="note">${nf(c.responses)} resp.</div></td>
              <td class="r num">${c.delta === null ? "—"
                : `<span class="delta ${c.delta > 0.05 ? 'up' : c.delta < -0.05 ? 'down' : 'flat'}">${c.delta > 0 ? '+' : ''}${nf(c.delta, 2)}</span>`}</td>
              <td class="r num">${pc(c.pct_detractors, 0)}</td>
              <td>${c.top_theme
                ? esc(c.top_theme.replace(/_/g, " ")) + ` <span class="note">(${c.top_theme_count})</span>`
                : `<span class="note">none</span>`}</td></tr>`).join("")}
          </tbody></table></div>
      </div>
    </div>

    <div class="card">
      <h3>What customers actually said</h3>
      <p class="sub">Verbatim, with the score and the engagement it came from.
        The average is a summary of these, not a replacement for them.</p>
      <div class="chips" style="margin-bottom:12px">
        <button class="chip" data-cf="all" aria-pressed="${commentFilter === 'all'}">All</button>
        <button class="chip" data-cf="critical" aria-pressed="${commentFilter === 'critical'}">Three and below</button>
        <button class="chip" data-cf="strong" aria-pressed="${commentFilter === 'strong'}">Four and a half up</button>
        ${themes.map(t => `<button class="chip" data-cf="${t}"
          aria-pressed="${commentFilter === t}">${esc(t.replace(/_/g, " "))}</button>`).join("")}
      </div>
      <div class="grid g2">${comments.slice(0, 40).map(c => `
        <div class="flag ${c.score <= 3 ? 'High' : c.score >= 4.5 ? 'Low' : 'Medium'}">
          <h4 style="display:flex;justify-content:space-between;gap:10px">
            <span>${esc(c.customer_name)}</span>
            <span class="num">${nf(c.score, 1)}</span></h4>
          <p class="detail">${esc(c.comment)}</p>
          <div class="action">
            <b>Context</b>
            ${esc(c.project_code || "—")} ${esc(c.project_name || "")} ·
            ${esc(c.survey_point.replace(/_/g, " "))} ·
            ${esc(c.respondent_role.replace(/_/g, " "))} ·
            ${esc(c.responded_on)}
            ${c.would_reference === 1 ? " · would reference"
              : c.would_reference === 0 ? " · would not reference" : ""}
          </div>
        </div>`).join("")}
      </div>
      ${comments.length > 40 ? `<p class="note" style="margin-top:10px">
        Showing 40 of ${comments.length} matching comments.</p>` : ""}
      ${comments.length ? "" : `<p class="note">No comment matches that
        filter.</p>`}
    </div>`;

  host.querySelectorAll(".chip[data-cf]").forEach(b =>
    b.addEventListener("click", () => {
      commentFilter = b.dataset.cf;
      drawDiligenceTab(d);
    }));

  const pts = ["kickoff", "design", "go_live", "hypercare_exit", "annual"];
  const rows = pts.map(p => {
    const c = d.csat.find(x => x.scope === "survey_point" && x.scope_key === p);
    return c ? { label: c.scope_label, value: c.avg_score,
                 tip: nf(c.responses) + " responses" } : null;
  }).filter(Boolean);
  const el2 = document.getElementById("csat-chart");
  if (el2 && rows.length) {
    vBars(el2, rows, { height: 220, tickCount: 4, labelEvery: 1,
                       color: "var(--s1)",
                       tipFormat: v => nf(v, 2), valueLabel: "Average score" });
  }
}

/* ---- red and amber --------------------------------------------------- */
function digRag(host, d) {
  const rag = d.rag;
  const overridden = rag.filter(r => r.is_overridden && !r.override_note);
  const diverged = rag.filter(r => r.divergence_bands >= 2);
  const atRisk = rag.reduce((a, r) => a + (r.revenue_at_risk || 0), 0);

  host.innerHTML = `
    <div class="grid g3" style="margin-bottom:18px">
      <div class="tile stripe ${atRisk ? 'bad' : 'good'}">
        <div class="label">Margin at risk</div>
        <div class="value sm">${moneyK(atRisk)}</div>
        <div class="foot">across ${rag.length} engagements on the register</div>
      </div>
      <div class="tile stripe ${diverged.length ? 'bad' : 'good'}">
        <div class="label">Two bands out</div>
        <div class="value">${diverged.length}</div>
        <div class="foot">reported colour against the computed one</div>
      </div>
      <div class="tile stripe ${overridden.length ? 'bad' : 'good'}">
        <div class="label">Overrides with no note</div>
        <div class="value">${overridden.length}</div>
        <div class="foot">an override with no reason is indistinguishable from
          a mistake</div>
      </div>
    </div>

    <div class="card">
      <h3>The register</h3>
      <p class="sub">An engagement is here if the project manager says so or the
        model does. Taking only the manager's word makes the register a
        restatement of an opinion; taking only the model's makes it something
        nobody recognises.</p>
      <div class="grid">${rag.map(r => `
        <div class="flag ${r.reported_health === 'Red' ? 'Critical'
          : r.divergence_bands >= 2 ? 'High' : 'Medium'}">
          <h4 style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap">
            <span>${esc(r.project_code)} · ${esc(r.customer_name || "")}
              <span class="note">${esc(r.product || "")}</span></span>
            <span class="ragpair" title="Reported ${esc(r.reported_health)}, computed ${esc(r.computed_health || "not scored")}">
              <i class="rag ${healthClass(r.reported_health)}"></i>
              <span class="note">reported</span>
              <i class="rag ${healthClass(r.computed_health)}"></i>
              <span class="note">model${r.failure_probability_pct
                ? " " + pc(r.failure_probability_pct, 0) + " failure risk" : ""}</span>
            </span>
          </h4>
          <p class="detail">${esc(r.statement)}</p>
          <details class="evidence" ${r.reported_health === 'Red' ? "open" : ""}>
            <summary>${(r.reasons || []).length} reason${(r.reasons || []).length === 1 ? "" : "s"} on the record</summary>
            <div class="tblwrap" style="margin-top:8px"><table>
              <thead><tr><th></th><th>Reason</th><th class="r">Measure</th>
                <th class="r">Threshold</th></tr></thead>
              <tbody>${(r.reasons || []).map(x => `
                <tr><td class="tight">${pill(SEV_CLASS[x.severity] || "mute",
                    x.severity)}</td>
                  <td><b>${esc(x.reason_label)}</b>
                    <div class="note">${esc(x.statement)}</div></td>
                  <td class="r num">${x.metric_value === null ? "—"
                    : nf(x.metric_value, 1) + " " + esc(x.metric_unit || "")}</td>
                  <td class="r num">${x.threshold_value === null ? "—"
                    : nf(x.threshold_value, 1)}</td></tr>`).join("")}
              </tbody></table></div>
          </details>
          <div class="action" style="margin-top:10px">
            <b>Recovery · ${esc(r.recovery_owner || "")} · by ${esc(r.recovery_by || "")}</b>
            ${esc(r.recovery_action || "")}
          </div>
          ${r.latest_csat_comment ? `<div class="action" style="margin-top:8px">
            <b>Lowest score this customer has given ·
              ${nf(r.latest_csat, 1)} of 5${r.csat_on ? " · " + esc(r.csat_on) : ""}</b>
            ${esc(r.latest_csat_comment)}</div>` : ""}
        </div>`).join("")}
      </div>
    </div>`;
}

/* ---- phase duration -------------------------------------------------- */
function digPhase(host, d) {
  const orgPhases = d.phases.filter(p => p.scope === "org")
    .sort((a, b) => a.phase_order - b.phase_order);
  const products = [...new Set(d.phases.filter(p => p.scope === "product")
    .map(p => p.scope_label))];

  host.innerHTML = `
    <div class="card" style="margin-bottom:18px">
      <h3>Which phase runs long</h3>
      <p class="sub">Phase slip is heavily skewed: most engagements run most
        phases close to plan and a minority run them very long. So the share
        that slipped is reported beside the size of the slip among those that
        did, because a one-day median can sit on top of a three-week
        problem.</p>
      <div class="chartbox" id="phase-chart"></div>
      <div class="tblwrap" style="margin-top:12px"><table>
        <thead><tr><th>Phase</th><th class="r">Planned</th>
          <th class="r">Ran long</th><th class="r">By</th>
          <th class="r">90th pct</th><th class="r">Effort</th>
          <th class="r">Share of slip</th></tr></thead>
        <tbody>${orgPhases.map(p => `
          <tr><td>${esc(p.phase)}
              <div class="note">${p.projects} completed engagements</div></td>
            <td class="r num">${nf(p.planned_days_med, 0)}d</td>
            <td class="r num">${pc(p.pct_slipped, 0)}</td>
            <td class="r num">${nf(p.slip_days_when_slipped, 0)}d</td>
            <td class="r num">${nf(p.slip_days_p90, 0)}d</td>
            <td class="r num">${p.effort_overrun_pct === null ? "—"
              : `<span class="delta ${p.effort_overrun_pct > 8 ? 'down'
                : p.effort_overrun_pct < -8 ? 'up' : 'flat'}">${p.effort_overrun_pct > 0 ? '+' : ''}${nf(p.effort_overrun_pct, 0)}%</span>`}</td>
            <td class="r num">${pc(p.share_of_slip_pct, 0)}</td></tr>`).join("")}
        </tbody>
        <caption>Effort against plan separates two different problems. Over
          plan means more work appeared. Under plan means the team was waiting,
          which is a dependency rather than a capacity question.</caption>
      </table></div>
    </div>

    <div class="card" style="margin-bottom:18px">
      <h3>And why</h3>
      <p class="sub">Each cause is a countable thing in the database, measured
        over the engagements that actually slipped in that phase. The incidence
        is what separates a finding from a memorable anecdote, so it is stored
        beside the claim.</p>
      <div class="grid g2">${orgPhases.filter(p => (p.causes || []).length)
        .map(p => `
        <div class="card" style="background:var(--surface-sunk)">
          <h3 style="font-size:15px">${esc(p.phase)}</h3>
          <p class="sub">${esc(p.statement)}</p>
          ${(p.causes || []).map(c => `
            <div class="driver">
              <div class="tag">${pc(c.incidence_pct, 0)}</div>
              <div>
                <div class="txt"><b>${esc(c.cause_label)}</b></div>
                <div class="note">${esc(c.evidence)}</div>
                ${c.attributed_days ? `<div class="wt">apportions about
                  ${nf(c.attributed_days, 1)} days of the median slip</div>` : ""}
              </div>
            </div>`).join("")}
          <p class="note" style="margin-top:8px">The day apportionment splits
            the median slip across the causes in proportion to incidence. It is
            an apportionment, not a measurement, and it is here because five
            bare percentages leave a reader with no sense of scale.</p>
        </div>`).join("")}
      </div>
    </div>

    ${products.length ? `<div class="card">
      <h3>By product line</h3>
      <p class="sub">The phase that carries the most slip, per line. Where a
        line differs from the organisation, that is where the implementation
        method differs too.</p>
      <div class="tblwrap"><table>
        <thead><tr><th>Line</th><th>Worst phase</th><th class="r">Ran long</th>
          <th class="r">By</th><th class="r">Share of that line's slip</th></tr></thead>
        <tbody>${products.map(pl => {
          const p = d.phases.find(x => x.scope === "product"
            && x.scope_label === pl && x.rank_by_slip === 1);
          return p ? `<tr><td>${esc(pl)}</td><td>${esc(p.phase)}</td>
            <td class="r num">${pc(p.pct_slipped, 0)}</td>
            <td class="r num">${nf(p.slip_days_when_slipped, 0)}d</td>
            <td class="r num">${pc(p.share_of_slip_pct, 0)}</td></tr>` : "";
        }).join("")}
        </tbody></table></div>
    </div>` : ""}`;

  const host2 = document.getElementById("phase-chart");
  if (host2 && orgPhases.length) {
    const series = [
      { label: "Share of all schedule slip", color: "var(--bad)" },
      { label: "Share of engagements that ran long", color: "var(--s2)" },
    ];
    groupedBars(host2, orgPhases.map(p => ({
      label: p.phase,
      values: [p.share_of_slip_pct, p.pct_slipped],
    })), series, { height: 220, tickFormat: v => nf(v) + "%" });
    host2.insertAdjacentHTML("afterbegin", legend(series));
  }
}

/* ---- hypercare ------------------------------------------------------- */
function digHypercare(host, d) {
  const orgHc = d.hypercare.find(h => h.scope === "org");
  const byProduct = d.hypercare.filter(h => h.scope === "product");

  host.innerHTML = `
    <div class="card" style="margin-bottom:18px">
      <h3>How long hypercare actually takes</h3>
      <p class="sub">${orgHc ? esc(orgHc.statement) : ""}</p>
      <div class="grid g4" style="margin-top:4px">
        <div class="tile"><div class="label">Planned</div>
          <div class="value">${orgHc ? nf(orgHc.planned_days_med, 0) : "—"}d</div>
          <div class="foot">median across go-lives</div></div>
        <div class="tile stripe warn"><div class="label">Actual</div>
          <div class="value">${orgHc ? nf(orgHc.actual_days_med, 0) : "—"}d</div>
          <div class="foot">${orgHc ? nf(orgHc.actual_days_p90, 0)
            + "d at the ninetieth percentile" : ""}</div></div>
        <div class="tile stripe bad"><div class="label">Unbilled cost</div>
          <div class="value sm">${orgHc ? moneyK(orgHc.unbilled_cost) : "—"}</div>
          <div class="foot">${orgHc ? pc(orgHc.billable_share_pct, 0)
            + " of hypercare hours are billable" : ""}</div></div>
        <div class="tile"><div class="label">Clean exits</div>
          <div class="value">${orgHc ? pc(orgHc.clean_exit_pct, 0) : "—"}</div>
          <div class="foot">the rest extended or still open</div></div>
      </div>
    </div>

    <div class="grid g2" style="margin-bottom:18px">
      <div class="card">
        <h3>By product line</h3>
        <div class="tblwrap"><table>
          <thead><tr><th>Line</th><th class="r">Go-lives</th>
            <th class="r">Planned</th><th class="r">Actual</th>
            <th class="r">Over</th><th class="r">Billable</th>
            <th class="r">Unbilled cost</th></tr></thead>
          <tbody>${byProduct.sort((a, b) => (b.overrun_days_med || 0)
              - (a.overrun_days_med || 0)).map(h => `
            <tr><td>${esc(h.scope_label)}</td>
              <td class="r num">${h.projects}</td>
              <td class="r num">${nf(h.planned_days_med, 0)}d</td>
              <td class="r num">${nf(h.actual_days_med, 0)}d</td>
              <td class="r num">${nf(h.overrun_days_med, 0)}d</td>
              <td class="r num">${pc(h.billable_share_pct, 0)}</td>
              <td class="r num">${moneyK(h.unbilled_cost)}</td></tr>`).join("")}
          </tbody>
          <caption>Unbilled cost values the non-billable share of hypercare
            effort at the organisation's own average cost rate. It is the
            number that makes hypercare a commercial conversation rather than
            a support one.</caption>
        </table></div>
      </div>
      <div class="card">
        <h3>Longest overruns</h3>
        <p class="sub">Where hypercare ran furthest past its planned exit, and
          what was recorded as the reason.</p>
        <div class="tblwrap"><table>
          <thead><tr><th>Engagement</th><th class="r">Planned</th>
            <th class="r">Actual</th><th>Reason recorded</th></tr></thead>
          <tbody>${d.hypercare_worst.map(h => `
            <tr><td class="tight">${esc(h.project_code)}
                <div class="note">${esc(h.customer_name || "")}</div></td>
              <td class="r num">${nf(h.planned_days, 0)}d</td>
              <td class="r num">${nf(h.actual_days, 0)}d</td>
              <td>${esc(h.extension_reason || "—")}</td></tr>`).join("")}
          </tbody></table></div>
      </div>
    </div>`;
}

/* ---- rates ----------------------------------------------------------- */
function digRates(host, d) {
  const orgRate = d.rates.find(r => r.dimension === "org");
  const dims = ["role", "product", "billing_model", "practice", "customer"];
  const dimLabel = {
    role: "By role", product: "By product line",
    billing_model: "By billing model", practice: "By practice",
    customer: "By customer, largest twelve",
  };

  host.innerHTML = `
    <div class="card" style="margin-bottom:18px">
      <h3>Three numbers, three conversations</h3>
      <p class="sub">${orgRate ? esc(orgRate.statement) : ""}</p>
      <div class="grid g4" style="margin-top:4px">
        <div class="tile"><div class="label">Rate card</div>
          <div class="value">${orgRate ? money(orgRate.list_rate_avg, 0) : "—"}</div>
          <div class="foot">hour-weighted list rate</div></div>
        <div class="tile stripe warn"><div class="label">Sold at</div>
          <div class="value">${orgRate ? money(orgRate.booked_rate_avg, 0) : "—"}</div>
          <div class="foot">${orgRate ? pc(orgRate.discount_pct, 1)
            + " discount — a sales conversation" : ""}</div></div>
        <div class="tile"><div class="label">Per billable hour</div>
          <div class="value">${orgRate ? money(orgRate.realised_rate, 0) : "—"}</div>
          <div class="foot">revenue recognised, divided by billable hours</div></div>
        <div class="tile stripe ${orgRate && orgRate.absorption_pct > 3 ? 'bad' : 'good'}">
          <div class="label">Per hour delivered</div>
          <div class="value">${orgRate ? money(orgRate.net_rate, 0) : "—"}</div>
          <div class="foot">${orgRate ? pc(Math.abs(orgRate.absorption_pct), 1)
            + (orgRate.absorption_pct > 0 ? " below" : " above")
            + " the rate it was sold at" : ""}</div></div>
      </div>
      <p class="note" style="margin-top:10px">Revenue per <em>billable</em>
        hour sits above the booked rate on fixed-price work and usually does,
        because the fee is fixed and the non-billable hours delivered alongside
        it are invisible to that division. Revenue per hour <em>delivered</em>
        is not flattered by anything. Reporting only the first is how a book
        with an absorption problem reads as recovering above its own rate
        card.</p>
    </div>

    ${dims.map(dim => {
      const set = d.rates.filter(r => r.dimension === dim);
      if (!set.length) return "";
      return `<div class="card" style="margin-bottom:18px">
        <h3>${dimLabel[dim]}</h3>
        <div class="tblwrap"><table>
          <thead><tr><th></th><th class="r">Hours</th><th class="r">List</th>
            <th class="r">Sold</th><th class="r">Discount</th>
            <th class="r">Per billable hour</th><th class="r">Per hour delivered</th>
            <th class="r">Gap to sold</th><th class="r">Margin</th></tr></thead>
          <tbody>${set.sort((a, b) => (b.discount_pct || 0) - (a.discount_pct || 0))
            .map(r => `
            <tr><td>${esc(r.dimension_label)}</td>
              <td class="r num">${nf(r.billable_hours, 0)}</td>
              <td class="r num">${money(r.list_rate_avg, 0)}</td>
              <td class="r num">${money(r.booked_rate_avg, 0)}</td>
              <td class="r num">${pc(r.discount_pct, 1)}</td>
              <td class="r num">${r.realised_rate ? money(r.realised_rate, 0) : "—"}</td>
              <td class="r num">${r.net_rate ? money(r.net_rate, 0) : "—"}</td>
              <td class="r num">${r.absorption_pct === null ? "—"
                : `<span class="delta ${r.absorption_pct > 2 ? 'down'
                  : r.absorption_pct < -2 ? 'up' : 'flat'}">${r.absorption_pct > 0 ? '−' : '+'}${nf(Math.abs(r.absorption_pct), 1)}%</span>`}</td>
              <td class="r num">${r.margin_pct === null ? "—" : pc(r.margin_pct, 1)}</td>
            </tr>`).join("")}
          </tbody>
          ${dim === "role" ? `<caption>Revenue cannot be attributed to a role,
            so the discount is the only comparable figure in this cut.</caption>`
            : ""}
        </table></div>
      </div>`;
    }).join("")}`;
}

/* ---- performance ----------------------------------------------------- */
function cycleOrder(cycles, row) {
  // Cycles have to read in the order they happened. Sorting the labels
  // alphabetically put "First half 2026" between "First half 2025" and
  // "Second half 2025", which makes a trend column meaningless.
  const c = cycles.find(x => x.cycle_code === row.scope_key);
  return c ? c.sort_order : 99;
}

function digTeam(host, d) {
  const orgPerf = d.performance.find(p => p.scope === "org");
  const cycles = d.cycles;
  const people = [...new Map(d.reviews.map(r =>
    [r.employee_id, r])).values()];
  if (!reviewPerson || !people.some(p => p.employee_id === reviewPerson)) {
    reviewPerson = people[0] && people[0].employee_id;
  }
  const mine = d.reviews.filter(r => r.employee_id === reviewPerson)
    .sort((a, b) => b.sort_order - a.sort_order);

  host.innerHTML = `
    <div class="card" style="margin-bottom:18px">
      <h3>The last three cycles</h3>
      <p class="sub">${orgPerf ? esc(orgPerf.statement) : ""}</p>
      <div class="tblwrap" style="margin-top:8px"><table>
        <thead><tr><th>Cycle</th><th class="r">Reviews</th>
          <th class="r">Average</th><th class="r">Top</th><th class="r">Bottom</th>
          <th class="r">Flight risk</th><th class="r">Promotion ready</th>
          <th class="r">Calibrated</th><th class="r">Rating vs utilisation</th>
        </tr></thead>
        <tbody>${d.performance.filter(p => p.scope === "cycle")
          .sort((a, b) => cycleOrder(cycles, a) - cycleOrder(cycles, b)).map(p => `
          <tr><td>${esc(p.scope_label)}</td>
            <td class="r num">${p.reviews}</td>
            <td class="r num">${nf(p.avg_rating, 2)}</td>
            <td class="r num">${pc(p.pct_top, 0)}</td>
            <td class="r num">${pc(p.pct_bottom, 0)}</td>
            <td class="r num">${p.high_flight_risk}</td>
            <td class="r num">${p.promotion_ready}</td>
            <td class="r num">${pc(p.calibration_pct, 0)}</td>
            <td class="r num">${p.rating_vs_utilization_r === null ? "—"
              : (p.rating_vs_utilization_r > 0 ? "+" : "")
                + nf(p.rating_vs_utilization_r, 2)}</td></tr>`).join("")}
        </tbody>
        <caption>The correlation is the interesting column. A cycle whose
          ratings track billable utilisation closely is rewarding chargeable
          hours, which is defensible if it is deliberate and a problem if it is
          not, because the people carrying presales and enablement are the ones
          it penalises.</caption>
      </table></div>
    </div>

    <div class="grid g2" style="margin-bottom:18px">
      <div class="card">
        <h3>By practice</h3>
        <div class="tblwrap"><table>
          <thead><tr><th>Practice</th><th class="r">Reviews</th>
            <th class="r">Average</th><th class="r">Change</th>
            <th class="r">Flight risk</th><th class="r">Training</th></tr></thead>
          <tbody>${d.performance.filter(p => p.scope === "practice")
            .sort((a, b) => b.avg_rating - a.avg_rating).map(p => `
            <tr><td>${esc(p.scope_label)}</td>
              <td class="r num">${p.reviews}</td>
              <td class="r num">${nf(p.avg_rating, 2)}</td>
              <td class="r num">${p.rating_delta === null ? "—"
                : `<span class="delta ${p.rating_delta > 0.03 ? 'up'
                  : p.rating_delta < -0.03 ? 'down' : 'flat'}">${p.rating_delta > 0 ? '+' : ''}${nf(p.rating_delta, 2)}</span>`}</td>
              <td class="r num">${p.high_flight_risk}</td>
              <td class="r num">${nf(p.avg_training_hours, 0)}h
                ${p.training_target_hours ? `<span class="note">/ ${nf(p.training_target_hours, 0)}</span>` : ""}</td>
            </tr>`).join("")}
          </tbody></table></div>
      </div>
      <div class="card">
        <h3>By role</h3>
        <div class="tblwrap"><table>
          <thead><tr><th>Role</th><th class="r">Reviews</th>
            <th class="r">Average</th><th class="r">Top</th>
            <th class="r">Rating vs utilisation</th></tr></thead>
          <tbody>${d.performance.filter(p => p.scope === "role")
            .sort((a, b) => b.avg_rating - a.avg_rating).map(p => `
            <tr><td>${esc(p.scope_label)}</td>
              <td class="r num">${p.reviews}</td>
              <td class="r num">${nf(p.avg_rating, 2)}</td>
              <td class="r num">${pc(p.pct_top, 0)}</td>
              <td class="r num">${p.rating_vs_utilization_r === null ? "—"
                : (p.rating_vs_utilization_r > 0 ? "+" : "")
                  + nf(p.rating_vs_utilization_r, 2)}</td></tr>`).join("")}
          </tbody></table></div>
      </div>
    </div>

    <div class="card">
      <h3>Review forms</h3>
      <p class="sub">The last three cycles for the people a new leader would ask
        about first: the highest and lowest rated, and anyone flagged as a
        retention risk. The measures the rating was set against sit beside it,
        so the rating is checkable rather than assertable.</p>
      <div class="chips" style="margin-bottom:12px">
        ${people.map(p => `<button class="chip" data-emp="${p.employee_id}"
          aria-pressed="${reviewPerson === p.employee_id}">${esc(p.employee_name)}
          <span class="note">${esc(p.role)}</span></button>`).join("")}
      </div>
      <div class="grid g3">${mine.map(r => `
        <div class="card" style="background:var(--surface-sunk)">
          <h3 style="font-size:15px;display:flex;justify-content:space-between;gap:8px">
            <span>${esc(r.cycle_label)}</span>
            <span class="num">${nf(r.overall_rating, 2)}</span></h3>
          <p class="sub">${esc(r.rating_label)} ·
            reviewed by ${esc(r.reviewer_name || "—")} ·
            ${r.is_calibrated ? "calibrated" : "not calibrated"}</p>
          <div class="tblwrap" style="margin-bottom:10px"><table><tbody>
            <tr><td>Billable utilisation</td>
              <td class="r num">${pc(r.utilization_pct, 1)}
                <span class="note">/ ${pc(r.utilization_target_pct, 0)}</span></td></tr>
            <tr><td>Customer satisfaction</td>
              <td class="r num">${r.csat_avg ? nf(r.csat_avg, 2) : "—"}</td></tr>
            <tr><td>Engagements delivered</td>
              <td class="r num">${r.projects_delivered}</td></tr>
            <tr><td>Delivered on time</td>
              <td class="r num">${r.on_time_pct === null ? "—" : pc(r.on_time_pct, 0)}</td></tr>
            <tr><td>Timesheet compliance</td>
              <td class="r num">${r.timesheet_compliance_pct === null ? "—"
                : pc(r.timesheet_compliance_pct, 0)}</td></tr>
            <tr><td>Training hours</td>
              <td class="r num">${nf(r.training_hours, 0)}</td></tr>
            <tr><td>Certifications</td>
              <td class="r num">${r.certifications}</td></tr>
            <tr><td>Retention risk</td>
              <td class="r">${pill(r.flight_risk === "high" ? "bad"
                : r.flight_risk === "medium" ? "warn" : "good",
                r.flight_risk)}</td></tr>
          </tbody></table></div>
          <div class="action" style="margin-bottom:8px"><b>Strengths</b>
            ${esc(r.strengths || "")}</div>
          <div class="action" style="margin-bottom:8px"><b>Development</b>
            ${esc(r.development || "")}</div>
          <div class="action" style="margin-bottom:8px"><b>Goals</b>
            ${esc(r.goals || "")}</div>
          <div class="action"><b>Manager comment</b>
            ${esc(r.manager_comment || "")}</div>
        </div>`).join("")}
      </div>
    </div>`;

  host.querySelectorAll(".chip[data-emp]").forEach(b =>
    b.addEventListener("click", () => {
      reviewPerson = Number(b.dataset.emp);
      drawDiligenceTab(d);
    }));
}

/* ---- product tickets ------------------------------------------------- */
function digProduct(host, d) {
  const growing = d.tickets.filter(t => t.backlog_verdict === "growing");
  const cost = d.tickets.reduce((a, t) => a + (t.delivery_effort_cost || 0), 0);
  const blockers = d.tickets.reduce((a, t) => a + t.open_blockers, 0);

  host.innerHTML = `
    <div class="grid g3" style="margin-bottom:18px">
      <div class="tile stripe ${cost > 0 ? 'bad' : 'good'}">
        <div class="label">Delivery cost of product defects</div>
        <div class="value sm">${moneyK(cost)}</div>
        <div class="foot">absorbed in services margin, caused in the product</div>
      </div>
      <div class="tile stripe ${growing.length ? 'bad' : 'good'}">
        <div class="label">Growing backlogs</div>
        <div class="value">${growing.length}</div>
        <div class="foot">of ${d.tickets.length} product lines, on 90-day flow</div>
      </div>
      <div class="tile stripe ${blockers ? 'bad' : 'good'}">
        <div class="label">Open blockers</div>
        <div class="value">${blockers}</div>
        <div class="foot">across every line</div>
      </div>
    </div>

    <div class="card" style="margin-bottom:18px">
      <h3>By product</h3>
      <div class="tblwrap"><table>
        <thead><tr><th>Product</th><th class="r">Open defects</th>
          <th class="r">Blockers</th><th class="r">Open enhancements</th>
          <th class="r">Raised 90d</th><th class="r">Resolved 90d</th>
          <th class="r">Net</th><th class="r">Median TTR</th>
          <th class="r">Delivery hours</th><th>Backlog</th></tr></thead>
        <tbody>${d.tickets.map(t => `
          <tr><td>${esc(t.product)}
              <div class="note">${t.projects_affected} engagements affected ·
                ${pc(t.customer_raised_pct, 0)} customer raised</div></td>
            <td class="r num">${t.open_bugs}</td>
            <td class="r num">${t.open_blockers || "—"}</td>
            <td class="r num">${t.open_enhancements}</td>
            <td class="r num">${t.raised_last_90}</td>
            <td class="r num">${t.resolved_last_90}</td>
            <td class="r num"><span class="delta ${t.net_flow_90 > 0 ? 'down'
              : t.net_flow_90 < 0 ? 'up' : 'flat'}">${t.net_flow_90 > 0 ? '+' : ''}${t.net_flow_90}</span></td>
            <td class="r num">${nf(t.median_ttr_days, 0)}d</td>
            <td class="r num">${nf(t.delivery_effort_hours, 0)}</td>
            <td class="tight">${pill(t.backlog_verdict === "growing" ? "bad"
              : t.backlog_verdict === "shrinking" ? "good" : "warn",
              t.backlog_verdict)}</td></tr>`).join("")}
        </tbody>
        <caption>Net flow is tickets raised less tickets resolved over ninety
          days. A positive number means the backlog is growing, and a growing
          backlog is not worked down by adding delivery capacity.</caption>
      </table></div>
    </div>

    <div class="grid g2" style="margin-bottom:18px">
      ${d.tickets.filter(t => t.open_bugs > 0)
        .sort((a, b) => b.delivery_effort_cost - a.delivery_effort_cost)
        .slice(0, 4).map(t => `
        <div class="flag ${t.backlog_verdict === 'growing' ? 'High' : 'Medium'}">
          <h4>${esc(t.product)}</h4>
          <p class="detail">${esc(t.statement)}</p>
        </div>`).join("")}
    </div>

    <div class="card">
      <h3>The open list</h3>
      <p class="sub">Highest severity and oldest first. Where a ticket blocks a
        go-live it is named, because that is the one that reaches a customer
        conversation.</p>
      <div class="tblwrap"><table>
        <thead><tr><th>Ref</th><th>Title</th><th>Product</th><th>Module</th>
          <th>Severity</th><th class="r">Age</th><th>Raised by</th>
          <th>Workaround</th></tr></thead>
        <tbody>${d.ticket_detail.map(t => `
          <tr><td class="tight">${esc(t.ticket_ref)}
              ${t.blocks_go_live ? "<br>" + pill("bad", "blocks go live") : ""}
              ${t.is_regression ? "<br>" + pill("warn", "regression") : ""}</td>
            <td>${esc(t.title)}
              ${t.project_code ? `<div class="note">${esc(t.project_code)}
                · ${esc(t.customer_name || "")}</div>` : ""}</td>
            <td class="tight">${esc(t.product)}</td>
            <td class="tight">${esc(t.module || "—")}</td>
            <td class="tight">${t.severity
              ? pill(t.severity === "blocker" || t.severity === "high" ? "bad"
                : t.severity === "medium" ? "warn" : "mute", t.severity)
              : pill("accent", "enhancement")}</td>
            <td class="r num">${nf(t.age_days, 0)}d</td>
            <td class="tight">${esc((t.raised_by || "").replace(/_/g, " "))}</td>
            <td class="note">${esc(t.workaround || "—")}</td></tr>`).join("")}
        </tbody></table></div>
    </div>`;
}
