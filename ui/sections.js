/* =========================================================================
   Section renderers. One function per view; each is given a container and
   fills it from the exported payload.
   ========================================================================= */
"use strict";

const SECTIONS = [
  { id: "psos",     req: "41",    label: "PS Operating System" },
  { id: "brief",    req: "42",    label: "Weekly brief" },
  { id: "projects", req: "31–33", label: "Project intelligence" },
  { id: "margin",   req: "35",    label: "Profitability" },
  { id: "billing",  req: "PS",    label: "Billing vs rev rec" },
  { id: "models",   req: "PS",    label: "T&M vs fixed fee" },
  { id: "evm",      req: "PS",    label: "Earned value (CPI/SPI)" },
  { id: "capacity", req: "37–38", label: "Capacity & forecasting" },
  { id: "whatif",   req: "39",    label: "What-if simulator" },
  { id: "gonogo",   req: "40",    label: "Deal go / no-go" },
  { id: "acq",      req: "34–36", label: "Acquisition assessment" },
  { id: "intake",   req: "27–29", label: "Data intake & migration" },
  { id: "checks",   req: "PS",    label: "Delivery checks" },
  { id: "resource", req: "PS",    label: "Resource requirement" },
  { id: "dilig",    req: "PS",    label: "Diligence pack" },
  { id: "auto",     req: "PS",    label: "Automation & AI" },
  { id: "how",      req: "30–45", label: "How it works" },
  { id: "changes",  req: "PS",    label: "Pending changes" },
];

/* ===================== 41. PS Operating System ========================= */
function renderPsos(el) {
  const o = org(), s = o.psos, b = o.baseline;
  const util = s.utilization_pct, um = s.utilization_target_pct;
  const marginOk = s.margin_pct >= s.margin_target_pct;
  const monthly = o.monthly.filter(m => m.period_month >= "2024-09" && m.period_month <= "2026-07");

  el.innerHTML = `
    <div class="viewhead">
      <div class="eyebrow">Requirement 41</div>
      <h1>PS Operating System</h1>
      <p>Five questions, answered from live data on every engine run. Everything below
         is a query result: the trailing twelve complete months for money and
         utilisation, the current live book for delivery, and the next six months for
         demand. The month in progress is excluded so a part-booked month never reads
         as a decline.</p>
    </div>

    <div class="grid g4" style="margin-bottom:22px">
      <div class="tile stripe ${s.overall_health === 'Healthy' ? 'good' : s.overall_health === 'Watch' ? 'warn' : 'bad'}">
        <div class="label">Overall</div>
        <div class="value sm">${esc(s.overall_health)}</div>
        <div class="foot">${esc(o.org.org_name)}</div>
      </div>
      <div class="tile stripe ${marginOk ? 'good' : 'warn'}">
        <div class="label">Margin</div>
        <div class="value">${pc(s.margin_pct)}</div>
        <div class="foot">${pc(s.margin_target_pct, 0)} target ·
          <span class="delta ${marginOk ? 'up' : 'down'}">${(s.margin_pct - s.margin_target_pct >= 0 ? '+' : '')}${nf(s.margin_pct - s.margin_target_pct, 1)} pts</span></div>
      </div>
      <div class="tile stripe ${util >= um - 3 ? 'good' : 'warn'}">
        <div class="label">Utilisation</div>
        <div class="value">${pc(util)}</div>
        <div class="foot">${pc(um, 0)} target · ${hrs(s.bench_hours)} unsold</div>
      </div>
      <div class="tile stripe ${s.revenue_at_risk > s.revenue_ytd * 0.06 ? 'bad' : 'good'}">
        <div class="label">Revenue at risk</div>
        <div class="value">${moneyK(s.revenue_at_risk)}</div>
        <div class="foot">${pc(s.revenue_at_risk / s.revenue_ytd * 100)} of trailing revenue</div>
      </div>
    </div>

    <div class="grid g2" style="margin-bottom:14px">
      <div class="card">
        <h3>1. Is PS profitable?</h3>
        <p class="sub">Recognised revenue by month, and margin against target.</p>
        <div class="grid" style="grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-bottom:14px">
          <div><div class="label" style="font-family:var(--font-ui);font-size:11.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3)">Revenue T12</div>
            <div class="num" style="font-family:var(--font-ui);font-size:20px;font-weight:600">${moneyK(s.revenue_ytd)}</div></div>
          <div><div class="label" style="font-family:var(--font-ui);font-size:11.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3)">Backlog</div>
            <div class="num" style="font-family:var(--font-ui);font-size:20px;font-weight:600">${moneyK(s.revenue_forecast_fy - s.revenue_ytd)}</div></div>
          <div><div class="label" style="font-family:var(--font-ui);font-size:11.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3)">Rev / consultant</div>
            <div class="num" style="font-family:var(--font-ui);font-size:20px;font-weight:600">${moneyK(b.revenue / b.headcount)}</div></div>
        </div>
        <div id="ch-revenue" class="chartbox"></div>
        <div id="ch-margin" class="chartbox" style="margin-top:8px"></div>
      </div>

      <div class="card">
        <h3>2. Are projects healthy?</h3>
        <p class="sub">The PM's RAG on the left, what the engine predicts on the right.
           The gap between them is the point of the exercise.</p>
        <div id="ch-health"></div>
        <div class="grid g3" style="margin-top:14px">
          <div class="tile stripe bad"><div class="label">Budget risk</div>
            <div class="value sm">${s.projects_budget_risk}</div>
            <div class="foot">of ${s.projects_total} live, over 50% probability</div></div>
          <div class="tile stripe warn"><div class="label">Schedule risk</div>
            <div class="value sm">${s.projects_schedule_risk}</div>
            <div class="foot">over 50% probability of delay</div></div>
          <div class="tile stripe bad"><div class="label">Margin risk</div>
            <div class="value sm">${s.projects_margin_risk}</div>
            <div class="foot">predicted below target</div></div>
        </div>
      </div>
    </div>

    <div class="grid g2" style="margin-bottom:14px">
      <div class="card">
        <h3>3. Are team members utilised?</h3>
        <p class="sub">Billable hours over capacity, by month, against the blended target.</p>
        <div id="ch-util" class="chartbox"></div>
        <div class="grid g3" style="margin-top:12px">
          <div><dl class="kv"><dt>Capacity / month</dt><dd>${hrs(s.capacity_hours)}</dd>
            <dt>Unsold T12</dt><dd>${hrs(s.bench_hours)}</dd></dl></div>
          <div><dl class="kv"><dt>On the bench</dt><dd>${s.bench_headcount ?? '—'} people</dd>
            <dt>Over 100%</dt><dd>${s.overallocated_headcount} people</dd></dl></div>
          <div><dl class="kv"><dt>Billable heads</dt><dd>${b.headcount}</dd>
            <dt>Realised rate</dt><dd>${money(b.realized_rate)}/h</dd></dl></div>
        </div>
      </div>

      <div class="card">
        <h3>4. Can we deliver future demand?</h3>
        <p class="sub">Signed work at engine EAC plus probability-weighted pipeline,
           against billable capacity at practice targets.</p>
        <div id="ch-capacity" class="chartbox"></div>
        <div class="grid g3" style="margin-top:12px">
          <div><dl class="kv"><dt>Pipeline</dt><dd>${moneyK(s.pipeline_value)}</dd>
            <dt>Weighted</dt><dd>${moneyK(s.weighted_pipeline_value)}</dd></dl></div>
          <div><dl class="kv"><dt>Demand 6m</dt><dd>${hrs(s.resource_demand_hours)}</dd>
            <dt>Net gap</dt><dd class="${s.capacity_gap_hours < 0 ? 'delta down' : 'delta up'}">${hrs(s.capacity_gap_hours)}</dd></dl></div>
          <div><dl class="kv"><dt>Hiring, by practice</dt><dd>${nf(s.hiring_requirement_fte, 1)} FTE</dd>
            <dt>Hiring, net</dt><dd>${nf(s.hiring_requirement_fte_net ?? 0, 1)} FTE</dd></dl></div>
        </div>
        <p class="note" style="margin-top:10px">Two hiring numbers because both are true.
          The practice figure adds up shortfalls practice by practice, since a Finance
          consultant cannot cover an HRP gap. The net figure assumes everyone is
          interchangeable and is the floor.</p>
      </div>
    </div>

    <div class="card">
      <h3>5. Action items</h3>
      <p class="sub">Ranked by value at stake, not by how loudly the alert fired.</p>
      <div class="tblwrap">
        <table>
          <thead><tr><th style="width:34px">#</th><th>Action</th><th>Why</th>
            <th class="r">At stake</th><th>When</th></tr></thead>
          <tbody>${o.actions.map(a => `
            <tr>
              <td class="num">${a.rank}</td>
              <td><b style="font-family:var(--font-ui)">${esc(a.headline)}</b>
                  <div class="note">${esc(a.action_type.replace(/_/g, ' ').toLowerCase())}</div></td>
              <td style="max-width:460px">${esc(a.rationale)}</td>
              <td class="r tight">${a.value_at_stake ? moneyK(a.value_at_stake) : '—'}</td>
              <td class="tight">${pill(a.urgency === 'This week' ? 'bad' : a.urgency === 'This month' ? 'warn' : 'mute', a.urgency)}</td>
            </tr>`).join("")}</tbody>
        </table>
      </div>
    </div>`;

  // charts
  vBars(document.getElementById("ch-revenue"), monthly.map(m => ({
    label: monthLabel(m.period_month), value: m.revenue,
    tipTitle: monthLabel(m.period_month),
    tipRows: [["Recognised", moneyK(m.revenue)], ["Invoiced", moneyK(m.invoiced)],
              ["Cost", moneyK(m.cost)], ["Margin", pc(m.margin_pct)]]
  })), { height: 150, labelEvery: 4, tickFormat: v => moneyK(v), color: "var(--s1)" });

  lineChart(document.getElementById("ch-margin"), monthly.map(m => ({
    label: monthLabel(m.period_month), value: m.margin_pct,
    tipRows: [["Margin", pc(m.margin_pct)], ["Revenue", moneyK(m.revenue)]]
  })), {
    height: 130, labelEvery: 6, tickFormat: v => nf(v, 0) + "%",
    ref: s.margin_target_pct, refLabel: `target ${nf(s.margin_target_pct, 0)}%`,
    color: "var(--s3)"
  });

  const live = o.live_projects.filter(p => p.project_status === "In Flight");
  const counts = h => ({
    pm: live.filter(p => p.project_health === h).length,
    eng: live.filter(p => p.predicted_health === h).length,
  });
  groupedBars(document.getElementById("ch-health"),
    ["Green", "Yellow", "Red"].map(h => {
      const c = counts(h);
      return { label: h, values: [c.pm, c.eng] };
    }),
    [{ label: "PM-reported RAG", color: "var(--s1)" },
     { label: "Engine prediction", color: "var(--s2)" }],
    { height: 170, tickFormat: v => nf(v, 0), tipFormat: v => nf(v, 0) + " projects" });
  document.getElementById("ch-health").insertAdjacentHTML("beforebegin",
    legend([{ label: "PM-reported RAG", color: "var(--s1)" },
            { label: "Engine prediction", color: "var(--s2)" }]));

  lineChart(document.getElementById("ch-util"), monthly.map(m => ({
    label: monthLabel(m.period_month), value: m.utilization_pct,
    tipRows: [["Billable utilisation", pc(m.utilization_pct)], ["Billable hours", hrs(m.billable_hours)]]
  })), {
    height: 150, labelEvery: 4, tickFormat: v => nf(v, 0) + "%", zeroBase: true,
    ref: um, refLabel: `target ${nf(um, 0)}%`, color: "var(--s1)", area: true
  });

  const byMonth = {};
  o.capacity.forEach(c => {
    byMonth[c.period_month] = byMonth[c.period_month] ||
      { billable: 0, committed: 0, pipeline: 0 };
    byMonth[c.period_month].billable += c.available_hours *
      (o.practices.find(p => p.practice_id === c.practice_id)?.target_utilization_pct || 70) / 100;
    byMonth[c.period_month].committed += c.committed_hours;
    byMonth[c.period_month].pipeline += c.pipeline_hours;
  });
  const capRows = Object.keys(byMonth).sort().map(m => ({
    label: monthLabel(m),
    values: [byMonth[m].billable, byMonth[m].committed, byMonth[m].pipeline]
  }));
  document.getElementById("ch-capacity").insertAdjacentHTML("beforebegin",
    legend([{ label: "Billable capacity at target", color: "var(--s1)" },
            { label: "Signed work (engine EAC)", color: "var(--s2)" },
            { label: "Weighted pipeline", color: "var(--s3)" }]));
  groupedBars(document.getElementById("ch-capacity"), capRows,
    [{ label: "Billable capacity at target", color: "var(--s1)" },
     { label: "Signed work (engine EAC)", color: "var(--s2)" },
     { label: "Weighted pipeline", color: "var(--s3)" }],
    { height: 180, tickFormat: v => nf(v / 1000, 1) + "k", tipFormat: v => hrs(v) });
}

/* ===================== 42. Weekly brief ================================ */
function renderBrief(el) {
  const o = org(), b = o.briefing;
  if (!b) { el.innerHTML = "<p>No briefing generated.</p>"; return; }
  const md = b.narrative_md
    .replace(/^# (.*)$/gm, "<h1>$1</h1>")
    .replace(/^## (.*)$/gm, "<h2>$1</h2>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .split("\n").reduce((acc, line) => {
      if (/^\d+\. /.test(line)) {
        if (!acc.inOl) { acc.out.push("<ol>"); acc.inOl = true; }
        acc.out.push("<li>" + line.replace(/^\d+\. /, "") + "</li>");
      } else if (/^- /.test(line)) {
        if (!acc.inUl) { acc.out.push("<ul>"); acc.inUl = true; }
        acc.out.push("<li>" + line.slice(2) + "</li>");
      } else {
        if (acc.inOl) { acc.out.push("</ol>"); acc.inOl = false; }
        if (acc.inUl) { acc.out.push("</ul>"); acc.inUl = false; }
        acc.out.push(line.startsWith("<") ? line : (line.trim() ? "<p>" + line + "</p>" : ""));
      }
      return acc;
    }, { out: [], inOl: false, inUl: false }).out.join("\n");

  const groups = {};
  (b.items || []).forEach(i => { (groups[i.section] = groups[i.section] || []).push(i); });

  el.innerHTML = `
    <div class="viewhead">
      <div class="eyebrow">Requirement 42</div>
      <h1>AI executive briefing</h1>
      <p>Written by the engine on every Monday run, from the same numbers the rest of
         the console shows. Nothing here is typed by a person, and nothing is rounded
         into a better story than the data supports.</p>
    </div>
    <div class="grid g2">
      <div class="card brief">${md}</div>
      <div class="grid" style="align-content:start">
        <div class="card">
          <h3>Headline measures</h3>
          <p class="sub">Most recent complete month against forecast, and the standing position.</p>
          <div class="tblwrap">
            <table><tbody>
              <tr><td>Overall health</td><td class="r">${pill(b.overall_health === 'Healthy' ? 'good' : b.overall_health === 'Watch' ? 'warn' : 'bad', b.overall_health)}</td></tr>
              <tr><td>Revenue, last complete month</td><td class="r num">${moneyK(b.revenue_actual)}</td></tr>
              <tr><td>Forecast for that month</td><td class="r num">${moneyK(b.revenue_forecast)}</td></tr>
              <tr><td>Margin</td><td class="r num">${pc(b.margin_pct)} <span class="note">vs ${pc(b.margin_target_pct, 0)}</span></td></tr>
              <tr><td>Utilisation</td><td class="r num">${pc(b.utilization_pct)} <span class="note">vs ${pc(b.utilization_target_pct, 0)}</span></td></tr>
              <tr><td>Revenue at risk</td><td class="r num">${moneyK(b.revenue_at_risk)}</td></tr>
              <tr><td>Projects at risk</td><td class="r num">${b.projects_at_risk}</td></tr>
            </tbody></table>
          </div>
        </div>
        <div class="card">
          <h3>Issues by severity</h3>
          <p class="sub">The narrative lists them in order. This is how hard each one is
            pushing, which the prose deliberately does not say.</p>
          <div class="tblwrap"><table><tbody>${o.red_flags.map(f => `
            <tr><td class="tight">${pill(f.severity === 'Critical' || f.severity === 'High' ? 'bad'
              : f.severity === 'Medium' ? 'warn' : 'mute', f.severity)}</td>
              <td>${esc(f.headline)}<div class="note">${esc(f.flag_category)}</div></td></tr>`).join("")}
            ${o.red_flags.length ? "" : `<tr><td class="note">No threshold breaches this week.</td></tr>`}
          </tbody></table></div>
        </div>
        ${groups.win ? `<div class="card">
          <h3>Going well</h3>
          <p class="sub">Projects running better than their comparable cohort.</p>
          <ol style="margin:6px 0 0;padding-left:20px">
            ${groups.win.map(i => `<li style="margin-bottom:6px">${esc(i.statement)}</li>`).join("")}
          </ol>
        </div>` : ""}
      </div>
    </div>`;
}

/* ===================== 31-33. Project intelligence ===================== */
let projSort = { key: "failure_probability_pct", dir: -1 };
let projFilter = "all";

function renderProjects(el) {
  const o = org();
  el.innerHTML = `
    <div class="viewhead">
      <div class="eyebrow">Requirements 31, 32 and 33</div>
      <h1>Project intelligence</h1>
      <p>A failure probability, a predicted final margin and a peer comparison for every
         live project, each with the drivers that produced it. The list is sorted by
         probability of an adverse outcome, so the projects that have not turned red yet
         appear before they do.</p>
    </div>
    <div class="card" style="margin-bottom:14px;padding:12px 16px">
      <p class="note" style="margin:0">
        Probabilities are calibrated against this organisation's own outturns.
        Of ${nf(o.run.base_rates.sample)} completed projects,
        <b>${pc(o.run.base_rates.budget * 100, 0)}</b> exceeded their hours budget,
        <b>${pc(o.run.base_rates.schedule * 100, 0)}</b> delivered more than ten days late, and
        <b>${pc(o.run.base_rates.margin * 100, 0)}</b> landed below target margin. The blended
        base rate is <b>${pc(o.run.base_rates.failure * 100, 0)}</b>, marked on each bar below,
        so a project only reads as red when it is materially worse than normal here rather
        than simply above an arbitrary threshold.</p>
    </div>
    <div class="row" style="margin-bottom:12px">
      <div class="chips" id="proj-filters"></div>
      <div class="spacer"></div>
      <div class="note" id="proj-count"></div>
    </div>
    <div class="split detail" id="proj-layout">
      <div id="proj-table"></div>
      <div id="proj-detail"></div>
    </div>`;

  const filters = [
    ["all", "All live"], ["red", "Predicted red"], ["margin", "Margin risk"],
    ["late", "Behind schedule"], ["worse", "Worse than peers"], ["hidden", "Green but predicted worse"],
  ];
  const fc = document.getElementById("proj-filters");
  fc.innerHTML = filters.map(([k, l]) =>
    `<button class="chip" data-f="${k}" aria-pressed="${projFilter === k}">${l}</button>`).join("");
  fc.querySelectorAll("button").forEach(btn => btn.addEventListener("click", () => {
    projFilter = btn.dataset.f;
    selectedProject = null;
    renderProjects(el);
  }));

  drawProjectTable();
  drawProjectDetail();
}

function filteredProjects() {
  let list = org().live_projects.filter(p => p.project_status === "In Flight");
  switch (projFilter) {
    case "red": list = list.filter(p => p.predicted_health === "Red"); break;
    case "margin": list = list.filter(p => ["High", "Critical"].includes(p.margin_risk)); break;
    case "late": list = list.filter(p => (p.critical_milestones_late || 0) > 0 || (p.milestones_late || 0) > 0); break;
    case "worse": list = list.filter(p => ["Worse", "Materially worse"].includes(p.tracking_verdict)); break;
    case "hidden": list = list.filter(p => p.project_health === "Green" && p.predicted_health !== "Green"); break;
  }
  const k = projSort.key;
  return list.sort((a, b) => ((a[k] ?? -1e12) - (b[k] ?? -1e12)) * projSort.dir);
}

function drawProjectTable() {
  const list = filteredProjects();
  document.getElementById("proj-count").textContent =
    `${list.length} of ${org().live_projects.filter(p => p.project_status === "In Flight").length} live projects`;
  // RAG sits second so it is never the column that gets clipped when the
  // table is squeezed.
  const cols = [
    ["project_code", "Project", false],
    ["__rag", "RAG", false],
    ["failure_probability_pct", "Failure", true],
    ["predicted_margin_pct", "Pred. margin", true],
    ["revenue_total", "Revenue", true],
    ["hours_consumed_pct", "Hours", true],
    ["tracking_variance_pct", "vs peers", true],
  ];
  document.getElementById("proj-table").innerHTML = `
    <div class="tblwrap" style="max-height:74vh;overflow-y:auto">
      <table>
        <thead><tr>
          ${cols.map(([k, l, num]) =>
            `<th class="${num ? 'r' : ''}"${k === '__rag' ? '' : ` data-k="${k}" style="cursor:pointer"`}>${l}${projSort.key === k ? (projSort.dir < 0 ? ' ↓' : ' ↑') : ''}</th>`).join("")}
        </tr></thead>
        <tbody>${list.map(p => `
          <tr class="clickable" data-id="${p.project_id}" aria-selected="${selectedProject === p.project_id}">
            <td style="min-width:210px"><b style="font-family:var(--font-ui)">${esc(p.project_code)}</b>
              <div class="note ellip" title="${esc(p.customer_name)} · ${esc(p.product)}">${esc(p.customer_name)} · ${esc(p.product)}</div></td>
            <td class="tight">
              <span class="ragpair" title="PM reports ${esc(p.project_health)}, engine predicts ${esc(p.predicted_health)}">
                <i class="rag ${healthClass(p.project_health)}"></i>
                <span class="arrow">→</span>
                <i class="rag ${healthClass(p.predicted_health)}"></i>
              </span></td>
            <td class="r num"><b>${pc(p.failure_probability_pct, 0)}</b></td>
            <td class="r num">${pc(p.predicted_margin_pct)}
              <div class="note">tgt ${pc(p.target_margin_pct, 0)}</div></td>
            <td class="r num tight">${moneyK(p.revenue_total)}</td>
            <td class="r num">${pc(p.hours_consumed_pct, 0)}</td>
            <td class="r num">${p.tracking_variance_pct === null ? '—' :
              `<span class="delta ${p.tracking_variance_pct > 10 ? 'down' : p.tracking_variance_pct < -10 ? 'up' : 'flat'}">${(p.tracking_variance_pct > 0 ? '+' : '')}${nf(p.tracking_variance_pct, 0)}%</span>`}</td>
          </tr>`).join("")}</tbody>
      </table>
    </div>`;
  document.querySelectorAll("#proj-table thead th[data-k]").forEach(th =>
    th.addEventListener("click", () => {
      const k = th.dataset.k;
      projSort = { key: k, dir: projSort.key === k ? -projSort.dir : -1 };
      drawProjectTable();
    }));
  document.querySelectorAll("#proj-table tbody tr").forEach(tr =>
    tr.addEventListener("click", () => {
      selectedProject = +tr.dataset.id;
      drawProjectTable();
      drawProjectDetail();
    }));
}

function drawProjectDetail() {
  const host = document.getElementById("proj-detail");
  const list = filteredProjects();
  const p = org().live_projects.find(x => x.project_id === selectedProject) || list[0];
  if (!p) { host.innerHTML = `<div class="card"><p class="note">No projects match this filter.</p></div>`; return; }
  selectedProject = p.project_id;

  const drivers = p.risk_drivers || [];
  const md = p.margin_drivers || [];
  host.innerHTML = `
    <div class="card" style="position:sticky;top:96px">
      <div class="row" style="align-items:flex-start">
        <div>
          <h3 style="font-size:17px">${esc(p.project_code)} · ${esc(p.project_name)}</h3>
          <p class="sub">${esc(p.practice_name || '')} · ${esc(p.project_type)} · ${esc(p.methodology)} ·
             ${esc(p.billing_model)} · PM ${esc(p.project_manager_name || 'unassigned')}</p>
        </div>
        <div class="spacer"></div>
        <div style="text-align:right">
          ${pill(healthClass(p.project_health), "PM: " + p.project_health)}
          ${pill(healthClass(p.predicted_health), "Engine: " + p.predicted_health)}
          <div class="note" style="margin-top:4px">${esc(p.confidence)} confidence ·
            ${p.peer_sample_size} peers</div>
        </div>
      </div>

      <h4 style="margin:16px 0 4px;font-size:13px">Probability of an adverse outcome</h4>
      <div id="ch-prob"></div>

      <h4 style="margin:18px 0 2px;font-size:13px">Why</h4>
      <p class="note" style="margin:0 0 6px">Each line is a measured condition on this
        project. The weight is its contribution to the probabilities above.</p>
      <div>${drivers.length ? drivers.map(d => `
        <div class="driver">
          <div class="tag">${esc(d.affects)}</div>
          <div>
            <div class="txt">${esc(d.statement)}</div>
            <div class="wt">${d.driver_code.replace(/_/g, ' ').toLowerCase()} · weight ${nf(d.weight, 2)}${d.direction === 'favourable' ? ' · favourable' : ''}</div>
          </div>
        </div>`).join("") : '<p class="note">No adverse conditions detected.</p>'}</div>

      <h4 style="margin:20px 0 2px;font-size:13px">Margin prediction</h4>
      <p class="note" style="margin:0 0 8px">Predicted cost is built additively from the
        estimate, so the lines below sum exactly to the distance travelled. The PM's own
        forecast sits alongside for comparison rather than inside the build-up.</p>
      <div class="grid g5" style="margin-bottom:12px">
        <div><dl class="kv"><dt>At estimate</dt><dd>${pc(p.plan_margin_pct)}</dd></dl></div>
        <div><dl class="kv"><dt>Margin to date</dt><dd>${pc(p.current_margin_pct)}</dd></dl></div>
        <div><dl class="kv"><dt>PM forecast</dt><dd>${pc(p.forecast_margin_pct)}</dd></dl></div>
        <div><dl class="kv"><dt>Engine predicted</dt><dd><b>${pc(p.predicted_margin_pct)}</b></dd></dl></div>
        <div><dl class="kv"><dt>Risk</dt><dd>${pill(riskClass(p.margin_risk), p.margin_risk)}</dd></dl></div>
      </div>
      <div id="ch-waterfall"></div>

      <div class="grid g2" style="margin-top:18px">
        <div>
          <h4 style="font-size:13px;margin-bottom:6px">Position</h4>
          <dl class="kv">
            <dt>Revenue at completion</dt><dd>${money(p.revenue_total)}</dd>
            <dt>Cost to date</dt><dd>${money(p.actual_cost)}</dd>
            <dt>Predicted cost</dt><dd>${money(p.predicted_cost)}</dd>
            <dt>Hours used</dt><dd>${hrs(p.actual_hours)} of ${hrs(p.total_budget_hours)}</dd>
            <dt>Physical progress</dt><dd>${pc(p.avg_percent_complete, 0)}</dd>
            <dt>Hours to complete</dt><dd>${hrs(p.predicted_hours_to_complete)}</dd>
            <dt>Team capacity left</dt><dd>${hrs(p.hours_available)}</dd>
            <dt>Open issues</dt><dd>${p.open_items || 0} (${p.open_high_items || 0} high)</dd>
            <dt>Change orders</dt><dd>${p.co_count || 0} · ${moneyK(p.co_value_approved)}</dd>
          </dl>
        </div>
        <div>
          <h4 style="font-size:13px;margin-bottom:6px">Against ${p.peer_count || 0} comparable completed projects</h4>
          <p class="note" style="margin:0 0 6px">Matched on ${esc(p.match_basis || '')}.</p>
          <dl class="kv">
            <dt>Tracking</dt><dd>${p.tracking_variance_pct === null ? '—' :
              `<b class="${p.tracking_variance_pct > 10 ? 'delta down' : p.tracking_variance_pct < -10 ? 'delta up' : ''}">${(p.tracking_variance_pct > 0 ? '+' : '')}${nf(p.tracking_variance_pct, 1)}%</b> ${esc(p.tracking_verdict || '')}`}</dd>
            <dt>Peer avg hours</dt><dd>${hrs(p.peer_avg_hours)}</dd>
            <dt>Peer avg revenue</dt><dd>${moneyK(p.peer_avg_revenue)}</dd>
            <dt>Peer avg margin</dt><dd>${pc(p.peer_avg_margin_pct)}</dd>
            <dt>Peer avg duration</dt><dd>${nf(p.peer_avg_duration_days, 0)} days</dd>
            <dt>Peer avg delay</dt><dd>${nf(p.peer_avg_delay_days, 0)} days</dd>
            <dt>Peer avg change orders</dt><dd>${nf(p.peer_avg_change_orders, 1)} · ${moneyK(p.peer_avg_change_order_value)}</dd>
            <dt>Peer avg team</dt><dd>${nf(p.peer_avg_team_size, 1)} people</dd>
            <dt>Peer budget overrun</dt><dd>${pc(p.peer_budget_overrun_pct)}</dd>
          </dl>
          ${(p.peers || []).length ? `<details class="evidence"><summary>Show the ${p.peers.length} closest peers</summary>
            <div class="tblwrap" style="margin-top:8px"><table><tbody>
              ${p.peers.map(q => `<tr><td>${esc(q.project_code)}<div class="note">${esc(q.project_name)}</div></td>
                <td class="r num">${nf(q.similarity_pct, 0)}%</td></tr>`).join("")}
            </tbody></table></div></details>` : ""}
        </div>
      </div>

      ${overridePanel(p)}
    </div>`;

  wireOverridePanel(p);

  const br = org().run.base_rates || {};
  const baseFail = (br.failure || 0) * 100;
  probBars(document.getElementById("ch-prob"), [
    { label: "Project failure probability", value: p.failure_probability_pct,
      base: baseFail,
      color: p.failure_probability_pct >= baseFail + 18 ? "var(--bad)"
        : p.failure_probability_pct >= baseFail + 7 ? "var(--warn)" : "var(--good)",
      note: "Blend of 45% margin, 35% budget, 20% schedule. Dashed line is the base rate." },
    { label: "Budget overrun", value: p.p_budget_overrun_pct, base: (br.budget || 0) * 100, color: "var(--s2)" },
    { label: "Schedule delay", value: p.p_schedule_delay_pct, base: (br.schedule || 0) * 100, color: "var(--s3)" },
    { label: "Margin below target", value: p.p_margin_below_target_pct, base: (br.margin || 0) * 100, color: "var(--s4)" },
  ]);

  if (md.length && p.plan_margin_pct !== null && p.plan_margin_pct !== undefined) {
    waterfall(document.getElementById("ch-waterfall"), p.plan_margin_pct,
      md.map(d => ({ label: d.statement, value: d.margin_impact_pts })),
      "Engine predicted margin", { startLabel: "Margin in the estimate" });
  } else {
    document.getElementById("ch-waterfall").innerHTML =
      `<p class="note">No margin drivers recorded for this project.</p>`;
  }
}

/* ===================== 35. Profitability =============================== */
let profDim = "customer";
function renderMargin(el) {
  const o = org();
  const dims = Object.keys(o.profitability);
  const labels = {
    customer: "Customer", project: "Project", product: "Product", practice: "Practice",
    geography: "Geography", project_type: "Project type", billing_model: "Billing model",
    project_manager: "Project manager", industry: "Industry", methodology: "Methodology",
  };
  el.innerHTML = `
    <div class="viewhead">
      <div class="eyebrow">Requirement 35</div>
      <h1>Historical profitability</h1>
      <p>Twenty-four complete months, cut ten ways. Revenue and cost both come from the
         monthly ledger, so every cut adds up to the same org total. Net hourly rate is
         recognised revenue over hours actually booked.</p>
    </div>
    <div class="chips" id="prof-dims" style="margin-bottom:14px">
      ${dims.map(d => `<button class="chip" data-d="${d}" aria-pressed="${d === profDim}">${labels[d] || d}</button>`).join("")}
    </div>
    <div class="grid g2" style="margin-bottom:16px">
      <div class="card"><h3 id="prof-title"></h3>
        <p class="sub">Gross profit by <span id="prof-dim-label"></span>, best and worst.</p>
        <div id="ch-prof"></div></div>
      <div class="card"><h3>Flagged</h3>
        <p class="sub">Automatic flags from the same rows: negative gross profit,
          material revenue at thin margin, and repeat over-budget delivery.</p>
        <div id="prof-flags"></div></div>
    </div>
    <div id="prof-table"></div>`;

  document.querySelectorAll("#prof-dims button").forEach(b =>
    b.addEventListener("click", () => { profDim = b.dataset.d; renderMargin(el); }));
  drawProfitability(labels);
}

function drawProfitability(labels) {
  const rows = (org().profitability[profDim] || []).slice();
  document.getElementById("prof-title").textContent = (labels[profDim] || profDim) + " profitability";
  document.getElementById("prof-dim-label").textContent = (labels[profDim] || profDim).toLowerCase();

  const sorted = rows.slice().sort((a, b) => b.gross_profit - a.gross_profit);
  const top = sorted.slice(0, 8), bottom = sorted.slice(-8).reverse();
  const chartRows = top.concat([{ dimension_label: "…", gross_profit: null }]).concat(bottom)
    .map(r => ({
      label: r.dimension_label, value: r.gross_profit,
      color: r.gross_profit < 0 ? "var(--bad)" : "var(--s1)",
      tipRows: r.gross_profit === null ? [] : [
        ["Revenue", moneyK(r.revenue)], ["Cost", moneyK(r.cost)],
        ["Gross profit", moneyK(r.gross_profit)], ["Margin", pc(r.margin_pct)],
        ["Projects", nf(r.project_count)]]
    }));
  hBars(document.getElementById("ch-prof"), chartRows,
    { rowH: 22, labelW: 260, valueFormat: v => moneyK(v), tipFormat: v => moneyK(v) });

  const flagged = rows.filter(r => r.flag && r.flag !== "top10");
  const flagLabel = {
    margin_destroyer: ["bad", "Destroying margin"],
    high_rev_low_margin: ["warn", "High revenue, low margin"],
    chronic_over_budget: ["warn", "Repeatedly over budget"],
    bottom10: ["mute", "Bottom 10"],
  };
  document.getElementById("prof-flags").innerHTML = flagged.length ? `
    <div class="tblwrap"><table><tbody>${flagged.slice(0, 12).map(r => `
      <tr><td>${esc(r.dimension_label)}
            <div class="note">${moneyK(r.revenue)} revenue · ${pc(r.margin_pct)} margin ·
              ${r.project_count} projects${r.over_budget_count ? ` · ${r.over_budget_count} over budget` : ''}</div></td>
          <td class="r tight">${r.flag.split(",").filter(f => flagLabel[f])
            .map(f => pill(flagLabel[f][0], flagLabel[f][1])).join(" ")}</td></tr>`).join("")}
    </tbody></table></div>` : `<p class="note">Nothing flagged on this dimension.</p>`;

  document.getElementById("prof-table").innerHTML = `
    <div class="tblwrap" style="max-height:60vh;overflow-y:auto"><table>
      <thead><tr><th>${labels[profDim] || profDim}</th><th class="r">Revenue</th>
        <th class="r">Cost</th><th class="r">Gross profit</th><th class="r">Margin</th>
        <th class="r">Hours</th><th class="r">Net rate</th><th class="r">Rev/cost</th>
        <th class="r">Projects</th><th>Flags</th></tr></thead>
      <tbody>${sorted.map(r => `
        <tr><td>${esc(r.dimension_label)}</td>
          <td class="r num tight">${moneyK(r.revenue)}</td>
          <td class="r num tight">${moneyK(r.cost)}</td>
          <td class="r num tight ${r.gross_profit < 0 ? 'delta down' : ''}">${moneyK(r.gross_profit)}</td>
          <td class="r num">${pc(r.margin_pct)}</td>
          <td class="r num">${nf(r.hours, 0)}</td>
          <td class="r num">${money(r.net_hourly_rate)}</td>
          <td class="r num">${nf(r.revenue_cost_ratio, 2)}</td>
          <td class="r num">${r.project_count}</td>
          <td class="tight note">${esc((r.flag || "").split(",").join(", ").replace(/_/g, " "))}</td>
        </tr>`).join("")}</tbody>
      <caption>Rows are capped at the top and bottom 20 per dimension in the export;
        the database holds every row.</caption>
    </table></div>`;
}

/* ===================== 37-38. Capacity & forecasting =================== */
function renderCapacity(el) {
  const o = org();
  const fa = o.forecast_accuracy;
  const byScope = s => fa.filter(f => f.scope === s);
  el.innerHTML = `
    <div class="viewhead">
      <div class="eyebrow">Requirements 37 and 38</div>
      <h1>Capacity and forecast discipline</h1>
      <p>What the next six months look like on signed work plus weighted pipeline, and
         how good this organisation's own forecasts have historically been. The second
         part is what makes the first believable.</p>
    </div>

    <div class="card" style="margin-bottom:16px">
      <h3>Capacity by practice, next six months</h3>
      <p class="sub">Supply is billable capacity at each practice's utilisation target.
        Demand is the engine's estimate to complete on live work, plus not-started
        backlog, plus pipeline weighted by probability.</p>
      <div class="tblwrap"><table>
        <thead><tr><th>Practice</th>${o.capacity.filter(c => c.practice_id === o.capacity[0].practice_id)
          .map(c => `<th class="r">${monthLabel(c.period_month)}</th>`).join("")}
          <th class="r">Total gap</th><th class="r">FTE</th></tr></thead>
        <tbody>${o.practices.map(pr => {
          const cells = o.capacity.filter(c => c.practice_id === pr.practice_id);
          const total = cells.reduce((a, c) => a + c.gap_hours, 0);
          const fte = cells.reduce((a, c) => a + c.implied_headcount_gap, 0) / (cells.length || 1);
          return `<tr><td><b style="font-family:var(--font-ui)">${esc(pr.practice_code)}</b>
            <div class="note">${esc(pr.practice_name)} · ${pc(pr.target_utilization_pct, 0)} target</div></td>
            ${cells.map(c => `<td class="r num ${c.gap_hours < 0 ? 'delta down' : 'delta up'}">${nf(c.gap_hours, 0)}</td>`).join("")}
            <td class="r num ${total < 0 ? 'delta down' : 'delta up'}"><b>${nf(total, 0)}</b></td>
            <td class="r num">${fte > 0.05 ? nf(fte, 1) : "—"}</td></tr>`;
        }).join("")}</tbody>
        <caption>Hours of surplus (positive) or shortfall (negative). The FTE column
          counts only the months that are short, converted at target utilisation, because
          a surplus in February does not staff a shortfall in October. That is why a
          practice can show a small net gap and still need people.</caption>
      </table></div>
    </div>

    <div class="card" style="margin-bottom:16px">
      <h3>What the engine expects to go wrong next</h3>
      <p class="sub">Each statement carries the data it was derived from.</p>
      <div class="grid g2">${o.future_risks.map(r => `
        <div class="tile stripe ${r.prediction_type.includes('SHORTFALL') || r.prediction_type.includes('MISS') ? 'warn' :
          r.prediction_type.includes('SURPLUS') ? 'warn' : 'mute'}">
          <div class="label">${esc(r.prediction_type.replace(/_/g, ' ').toLowerCase())}${r.horizon_month ? ' · ' + monthLabel(r.horizon_month) : ''}</div>
          <div style="margin:6px 0 6px">${esc(r.statement)}</div>
          <div class="foot">${esc(r.basis)} · ${esc(r.confidence)} confidence</div>
        </div>`).join("")}</div>
    </div>

    <div class="grid g2">
      <div class="card">
        <h3>Forecast accuracy by practice</h3>
        <p class="sub">Original forecast hours against actual, on completed projects.
          Negative means the estimate was light.</p>
        <div id="ch-fa-practice"></div>
      </div>
      <div class="card">
        <h3>Forecast accuracy by project manager</h3>
        <p class="sub">Only PMs with enough completed projects to say anything.</p>
        <div id="ch-fa-pm"></div>
      </div>
    </div>

    <div class="card" style="margin-top:16px">
      <h3>Forecast accuracy detail</h3>
      <div class="tblwrap" style="max-height:50vh;overflow-y:auto"><table>
        <thead><tr><th>Scope</th><th>Name</th><th class="r">Projects</th>
          <th class="r">Original h</th><th class="r">Revised h</th><th class="r">Actual h</th>
          <th class="r">Variance</th><th class="r">Accuracy</th><th>Bias</th></tr></thead>
        <tbody>${["org", "practice", "project_manager", "customer"].flatMap(sc =>
          byScope(sc).filter(f => f.sample_size >= (sc === "customer" ? 5 : 1))
            .sort((a, b) => a.hours_variance_pct - b.hours_variance_pct)
            .map(f => `<tr>
              <td class="note">${esc(sc.replace(/_/g, ' '))}</td>
              <td>${esc(f.scope_label)}</td>
              <td class="r num">${f.sample_size}</td>
              <td class="r num">${nf(f.original_forecast_hours, 0)}</td>
              <td class="r num">${nf(f.revised_forecast_hours, 0)}</td>
              <td class="r num">${nf(f.actual_hours, 0)}</td>
              <td class="r num ${f.hours_variance_pct < -5 ? 'delta down' : f.hours_variance_pct > 5 ? 'delta up' : ''}">${pc(f.hours_variance_pct)}</td>
              <td class="r num">${pc(f.accuracy_pct)}</td>
              <td class="tight">${pill(f.bias === 'Accurate' ? 'good' : 'warn', f.bias || '—')}</td>
            </tr>`)).join("")}</tbody>
      </table></div>
    </div>`;

  const pracRows = byScope("practice").sort((a, b) => a.hours_variance_pct - b.hours_variance_pct)
    .map(f => ({
      label: f.scope_label, value: f.hours_variance_pct,
      color: f.hours_variance_pct < -5 ? "var(--bad)" : f.hours_variance_pct > 5 ? "var(--s3)" : "var(--good)",
      tipRows: [["Variance", pc(f.hours_variance_pct)], ["Projects", nf(f.sample_size)],
                ["Original", hrs(f.original_forecast_hours)], ["Actual", hrs(f.actual_hours)]]
    }));
  hBars(document.getElementById("ch-fa-practice"), pracRows,
    { rowH: 26, labelW: 210, valueFormat: v => pc(v), tipFormat: v => pc(v) });

  const pmRows = byScope("project_manager").filter(f => f.sample_size >= 5)
    .sort((a, b) => a.hours_variance_pct - b.hours_variance_pct).slice(0, 12)
    .map(f => ({
      label: `${f.scope_label} (${f.sample_size})`, value: f.hours_variance_pct,
      color: f.hours_variance_pct < -5 ? "var(--bad)" : f.hours_variance_pct > 5 ? "var(--s3)" : "var(--good)",
      tipRows: [["Variance", pc(f.hours_variance_pct)], ["Projects", nf(f.sample_size)]]
    }));
  hBars(document.getElementById("ch-fa-pm"), pmRows,
    { rowH: 26, labelW: 230, valueFormat: v => pc(v), tipFormat: v => pc(v) });
}

/* ---------------------------------------------------------------------
   Overriding the engine on one engagement.

   The engine's health score and the project manager's sit side by side
   everywhere in this console, and the divergence between them is treated as
   the finding rather than as an error. This is where a person settles it.

   Three things are deliberate. The comment is mandatory, because an override
   with no reason is indistinguishable from a mistake by the time anybody
   reads it. The previous value is recorded with the change, so an applier can
   refuse a change somebody else has already overtaken. And nothing is written
   by this page: the override queues, and a person exports it and runs it.
   --------------------------------------------------------------------- */
const HEALTH_OPTIONS = ["Green", "Yellow", "Red"];

function overridePanel(p) {
  const pending = (typeof loadOverrides === "function" ? loadOverrides() : [])
    .filter(c => c.entity_table === "project" && c.entity_pk === p.project_id);
  return `
    <div class="card" style="margin-top:14px">
      <h3>Override the health on this engagement</h3>
      <p class="sub">The engine has this at
        <b>${esc(p.predicted_health || "—")}</b> and the project manager has it
        at <b>${esc(p.project_health || "—")}</b>. An override is a human
        decision that departs from the computed value, so it needs a reason
        and it is recorded with one.</p>
      ${pending.length ? `<p class="note" style="margin-bottom:10px">
        <b>${pending.length} override${pending.length === 1 ? "" : "s"}
        already queued on this engagement.</b> See Pending changes.</p>` : ""}
      <div class="row" style="gap:10px;align-items:flex-end">
        <label class="field" style="margin:0;min-width:150px">
          <span class="lbl">Set health to</span>
          <select id="ov-health">
            ${HEALTH_OPTIONS.map(h => `<option value="${h}"
              ${h === p.project_health ? "selected" : ""}>${h}</option>`).join("")}
          </select></label>
        <label class="field" style="margin:0;min-width:160px">
          <span class="lbl">Your name</span>
          <input type="text" id="ov-who" placeholder="who is deciding"
                 value="${esc(localStorage.getItem("psa.who") || "")}"></label>
        <label class="field" style="margin:0;flex:1 1 260px">
          <span class="lbl">Reason (required)</span>
          <input type="text" id="ov-why"
                 placeholder="why the engine is wrong on this one"></label>
        <button class="btn primary" id="ov-save">Record override</button>
      </div>
      <p class="note" style="margin-top:10px">Queued in this browser. Nothing
        reaches the database until somebody exports the change file and runs
        <span class="mono">apply_changes.py</span> against it.</p>
    </div>`;
}

function wireOverridePanel(p) {
  const btn = document.getElementById("ov-save");
  if (!btn) return;
  btn.addEventListener("click", () => {
    const who = (document.getElementById("ov-who").value || "").trim();
    const why = document.getElementById("ov-why").value || "";
    const to = document.getElementById("ov-health").value;
    if (!who) {
      toast("An override needs a name against it");
      return;
    }
    try { localStorage.setItem("psa.who", who); } catch (err) { /* fine */ }
    const ok = captureOverride({
      entity_table: "project",
      entity_pk: p.project_id,
      entity_label: `${p.project_code} ${p.project_name}`,
      field_name: "project_health",
      old_value: p.project_health,
      new_value: to,
      changed_by: who,
      reason: why,
      engine_value: p.predicted_health,
    });
    if (ok) render();
  });
}
