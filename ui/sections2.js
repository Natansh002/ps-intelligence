/* =========================================================================
   Interactive sections and the acquisition / intake views.
   ========================================================================= */
"use strict";

/* ===================== 39. What-if simulator =========================== */
const WHATIF_STATE = {
  UTILIZATION_CHANGE: { utilization_pct: 65 },
  HEADCOUNT_CHANGE: { headcount_delta: 10 },
  RATE_CHANGE: { rate_change_pct: 5 },
  CUSTOMER_LOSS: { customer_id: null },
  PROJECT_DELAY: { project_id: null, delay_days: 30 },
  NEW_DEAL: { contract_value: 2000000, hours: 11000 },
};
let whatifKind = "UTILIZATION_CHANGE";

/* The same arithmetic as py/scenarios.py, driven by the exported baseline. */
function simulateClient(b, kind, p) {
  const out = { revenue: b.revenue, cost: b.cost, billable_hours: b.billable_hours,
                capacity_hours: b.capacity_hours, headcount: b.headcount, notes: {} };
  if (kind === "UTILIZATION_CHANGE") {
    out.billable_hours = b.capacity_hours * p.utilization_pct / 100;
    out.revenue = out.billable_hours * b.realized_rate;
    out.notes.revenue = "Billable hours move to the chosen utilisation and are valued at " +
      "the current realised rate. Cost is unchanged because the consultants are already " +
      "on the payroll.";
  } else if (kind === "HEADCOUNT_CHANGE") {
    const n = p.headcount_delta;
    const u = b.billable_hours / b.capacity_hours;
    out.capacity_hours = b.capacity_hours * (1 + n / b.headcount);
    const rampLoss = Math.max(0, n) * (b.ramp_months / 12) * b.annual_hours * u;
    out.billable_hours = out.capacity_hours * u - rampLoss;
    out.revenue = out.billable_hours * b.realized_rate;
    out.cost = b.cost + n * b.avg_cost_rate * b.annual_hours;
    out.headcount = b.headcount + n;
    out.notes.cost = `${Math.abs(n)} consultants at ${money(b.avg_cost_rate)}/hr fully loaded ` +
      `over ${nf(b.annual_hours)} hours.`;
    out.notes.revenue = `New joiners reach current utilisation after ${nf(b.ramp_months)} ` +
      `months, so first-year billable hours are reduced accordingly.`;
  } else if (kind === "RATE_CHANGE") {
    out.revenue = b.revenue * (1 + (p.rate_change_pct / 100) * b.reprice_share);
    out.notes.revenue = `Only ${pc(b.reprice_share * 100, 0)} of the book can be repriced ` +
      `within a year. Signed fixed-price work is locked.`;
  } else if (kind === "CUSTOMER_LOSS") {
    out.revenue = b.revenue - (p.customer_revenue || 0);
    out.billable_hours = Math.max(0, b.billable_hours - (p.customer_hours || 0));
    out.notes.cost = "Cost is unchanged. Losing the account frees consultants, it does " +
      "not remove them from the payroll, so the whole revenue loss lands on margin " +
      "until the capacity is resold or removed.";
    out.notes.utilization_pct = `${hrs(p.customer_hours || 0)} return to the bench.`;
  } else if (kind === "PROJECT_DELAY") {
    const extra = (p.remaining_hours || 0) * b.delay_idle_factor * (p.delay_days / 30);
    out.cost = b.cost + extra * b.avg_cost_rate;
    const slipped = (p.project_revenue || 0) * Math.min(1, p.delay_days / 365);
    out.revenue = b.revenue - slipped;
    out.notes.cost = `${hrs(extra)} of additional cost at ${pc(b.delay_idle_factor * 100, 0)} ` +
      `of remaining work per month of delay, covering stand-down and re-testing.`;
    out.notes.revenue = `${moneyK(slipped)} of recognition slides beyond the window.`;
  } else if (kind === "NEW_DEAL") {
    const overrun = p.overrun || 0;
    out.revenue = b.revenue + p.contract_value;
    out.cost = b.cost + p.hours * b.avg_cost_rate * (1 + overrun);
    out.billable_hours = b.billable_hours + p.hours;
    out.notes.cost = `${hrs(p.hours)} at ${money(b.avg_cost_rate)}/hr` +
      (overrun ? `, uplifted ${pc(overrun * 100, 1)} for the overrun comparable projects show` : "");
  }
  out.margin_pct = out.revenue ? (out.revenue - out.cost) / out.revenue * 100 : null;
  out.utilization_pct = out.capacity_hours ? out.billable_hours / out.capacity_hours * 100 : null;
  return out;
}

function renderWhatif(el) {
  const o = org(), b = o.baseline;
  el.innerHTML = `
    <div class="viewhead">
      <div class="eyebrow">Requirement 39</div>
      <h1>What-if simulator</h1>
      <p>Move one lever and see where revenue, cost, margin, utilisation and capacity
         land. The baseline is the trailing twelve complete months (${esc(b.period)}).
         Every scenario states its own assumptions, because the assumption is usually
         where the argument is.</p>
    </div>
    <div class="chips" style="margin-bottom:16px" id="wi-kinds">
      ${[["UTILIZATION_CHANGE", "Utilisation moves"], ["HEADCOUNT_CHANGE", "Hire or cut"],
         ["RATE_CHANGE", "Rates change"], ["CUSTOMER_LOSS", "A customer leaves"],
         ["PROJECT_DELAY", "A project slips"], ["NEW_DEAL", "Take a new deal"]]
        .map(([k, l]) => `<button class="chip" data-k="${k}" aria-pressed="${whatifKind === k}">${l}</button>`).join("")}
    </div>
    <div class="split controls">
      <div class="card"><h3>Lever</h3><div id="wi-controls" style="margin-top:12px"></div>
        <p class="note" id="wi-assume" style="margin-top:14px"></p></div>
      <div>
        <div class="grid g5" id="wi-tiles" style="margin-bottom:14px"></div>
        <div class="card"><h3>Where it lands</h3>
          <p class="sub">Baseline against scenario, with the reasoning for each measure.</p>
          <div id="wi-table"></div></div>
      </div>
    </div>
    <div class="card" style="margin-top:16px">
      <h3>Worked scenarios stored by the engine</h3>
      <p class="sub">The same model run server side on this org's position, saved with its
        parameters so a scenario can be reopened and argued with later.</p>
      <div class="tblwrap"><table>
        <thead><tr><th>Question</th><th class="r">Revenue</th><th class="r">Cost</th>
          <th class="r">Margin</th><th class="r">Utilisation</th></tr></thead>
        <tbody>${o.scenarios.map(s => {
          const g = m => s.results.find(r => r.measure === m) || {};
          const d = (m, f) => {
            const r = g(m);
            if (r.scenario_value === null || r.scenario_value === undefined) return "—";
            const dir = r.delta_value > 0 ? "up" : r.delta_value < 0 ? "down" : "flat";
            return `${f(r.scenario_value)}<div class="note delta ${dir}">${r.delta_value > 0 ? "+" : ""}${f(r.delta_value)}</div>`;
          };
          return `<tr><td>${esc(s.question)}</td>
            <td class="r num tight">${d("revenue", moneyK)}</td>
            <td class="r num tight">${d("cost", moneyK)}</td>
            <td class="r num">${d("margin_pct", v => pc(v))}</td>
            <td class="r num">${d("utilization_pct", v => pc(v))}</td></tr>`;
        }).join("")}</tbody>
      </table></div>
    </div>`;

  document.querySelectorAll("#wi-kinds button").forEach(btn =>
    btn.addEventListener("click", () => { whatifKind = btn.dataset.k; renderWhatif(el); }));
  drawWhatifControls();
}

function drawWhatifControls() {
  const o = org(), b = o.baseline;
  const st = WHATIF_STATE[whatifKind];
  const host = document.getElementById("wi-controls");
  const customers = (o.profitability.customer || []).slice()
    .sort((a, b2) => b2.revenue - a.revenue);
  const projects = o.live_projects.filter(p => p.project_status === "In Flight")
    .sort((a, b2) => b2.failure_probability_pct - a.failure_probability_pct);

  const slider = (key, label, min, max, step, fmt) => `
    <label class="field"><span class="lbl">${label}: <span class="readout" id="ro-${key}">${fmt(st[key])}</span></span>
      <input type="range" id="in-${key}" min="${min}" max="${max}" step="${step}" value="${st[key]}"></label>`;

  if (whatifKind === "UTILIZATION_CHANGE") {
    host.innerHTML = slider("utilization_pct", "Billable utilisation", 40, 95, 0.5, v => pc(v)) +
      `<p class="note">Currently ${pc(b.utilization_pct)}, target ${pc(b.target_utilization_pct, 0)}.</p>`;
  } else if (whatifKind === "HEADCOUNT_CHANGE") {
    host.innerHTML = slider("headcount_delta", "Change in billable headcount", -30, 40, 1,
      v => (v > 0 ? "+" : "") + nf(v)) +
      `<p class="note">${b.headcount} billable consultants today.</p>`;
  } else if (whatifKind === "RATE_CHANGE") {
    host.innerHTML = slider("rate_change_pct", "Billing rate change", -10, 20, 0.5,
      v => (v > 0 ? "+" : "") + pc(v)) +
      `<p class="note">Realised rate ${money(b.realized_rate)}/h today.</p>`;
  } else if (whatifKind === "CUSTOMER_LOSS") {
    if (!st.customer_id && customers.length) st.customer_id = customers[0].dimension_key;
    host.innerHTML = `<label class="field"><span class="lbl">Customer</span>
      <select id="in-customer">${customers.map(c =>
        `<option value="${c.dimension_key}" ${c.dimension_key === st.customer_id ? "selected" : ""}>
          ${esc(c.dimension_label)} — ${moneyK(c.revenue)}</option>`).join("")}</select></label>
      <p class="note">Customer revenue and hours are the 24-month figures halved to a
        12-month basis, to match the baseline window.</p>`;
  } else if (whatifKind === "PROJECT_DELAY") {
    if (!st.project_id && projects.length) st.project_id = projects[0].project_id;
    host.innerHTML = `<label class="field"><span class="lbl">Project</span>
      <select id="in-project">${projects.map(p =>
        `<option value="${p.project_id}" ${p.project_id === st.project_id ? "selected" : ""}>
          ${esc(p.project_code)} — ${esc(p.customer_name)}</option>`).join("")}</select></label>` +
      slider("delay_days", "Delay", 0, 180, 5, v => nf(v) + " days");
  } else if (whatifKind === "NEW_DEAL") {
    host.innerHTML = `
      <label class="field"><span class="lbl">Contract value</span>
        <input type="number" id="in-value" value="${st.contract_value}" step="50000" min="0"></label>
      <label class="field"><span class="lbl">Proposed hours</span>
        <input type="number" id="in-hours" value="${st.hours}" step="250" min="0"></label>
      <p class="note">Cost is uplifted by the overrun completed projects across this org
        actually incurred (${pc((o.cohort_stats.org?.avg_overrun || 0) * 100, 1)}).</p>`;
  }

  host.querySelectorAll('input[type="range"]').forEach(inp => {
    inp.addEventListener("input", () => {
      const key = inp.id.replace("in-", "");
      st[key] = +inp.value;
      const ro = document.getElementById("ro-" + key);
      if (ro) {
        ro.textContent = key === "utilization_pct" ? pc(st[key])
          : key === "rate_change_pct" ? (st[key] > 0 ? "+" : "") + pc(st[key])
          : key === "delay_days" ? nf(st[key]) + " days"
          : (st[key] > 0 ? "+" : "") + nf(st[key]);
      }
      drawWhatifResult();
    });
  });
  const cs = document.getElementById("in-customer");
  if (cs) cs.addEventListener("change", () => { st.customer_id = cs.value; drawWhatifResult(); });
  const ps = document.getElementById("in-project");
  if (ps) ps.addEventListener("change", () => { st.project_id = +ps.value; drawWhatifResult(); });
  ["in-value", "in-hours"].forEach(id => {
    const e = document.getElementById(id);
    if (e) e.addEventListener("input", () => {
      st[id === "in-value" ? "contract_value" : "hours"] = +e.value || 0;
      drawWhatifResult();
    });
  });
  drawWhatifResult();
}

function drawWhatifResult() {
  const o = org(), b = o.baseline;
  const st = { ...WHATIF_STATE[whatifKind] };
  if (whatifKind === "CUSTOMER_LOSS") {
    const c = (o.profitability.customer || []).find(x => x.dimension_key === st.customer_id);
    st.customer_revenue = (c?.revenue || 0) / 2;
    st.customer_hours = (c?.hours || 0) / 2;
    st.label = c?.dimension_label;
  }
  if (whatifKind === "PROJECT_DELAY") {
    const p = o.live_projects.find(x => x.project_id === st.project_id);
    st.remaining_hours = p?.predicted_hours_to_complete || 0;
    st.project_revenue = p?.revenue_total || 0;
    st.label = p?.project_code;
  }
  if (whatifKind === "NEW_DEAL") st.overrun = Math.max(0, o.cohort_stats.org?.avg_overrun || 0);

  const r = simulateClient(b, whatifKind, st);
  const measures = [
    ["revenue", "Revenue", moneyK, "higher"],
    ["cost", "Cost", moneyK, "lower"],
    ["margin_pct", "Margin", v => pc(v), "higher"],
    ["utilization_pct", "Utilisation", v => pc(v), "higher"],
    ["capacity_hours", "Capacity", v => nf(v / 1000, 1) + "k h", "neutral"],
    ["billable_hours", "Billable hours", v => nf(v / 1000, 1) + "k h", "higher"],
    ["headcount", "Headcount", v => nf(v), "neutral"],
  ];
  document.getElementById("wi-tiles").innerHTML = measures.slice(0, 4).map(([k, l, f, good]) => {
    const base = b[k], val = r[k];
    const delta = (val ?? 0) - (base ?? 0);
    const better = good === "higher" ? delta > 0 : good === "lower" ? delta < 0 : null;
    const flat = Math.abs(delta) < 1e-6;
    const cls = flat ? "flat" : better ? "up" : "down";
    return `<div class="tile stripe ${flat ? '' : better ? 'good' : 'bad'}">
      <div class="label">${l}</div>
      <div class="value sm">${f(val)}</div>
      <div class="foot">${flat ? "unchanged" :
        `from ${f(base)} <span class="delta ${cls}">${delta > 0 ? "+" : ""}${f(delta)}</span>`}</div>
    </div>`;
  }).join("");

  document.getElementById("wi-table").innerHTML = `
    <div class="tblwrap"><table>
      <thead><tr><th>Measure</th><th class="r">Baseline</th><th class="r">Scenario</th>
        <th class="r">Change</th><th>Reasoning</th></tr></thead>
      <tbody>${measures.map(([k, l, f]) => {
        const base = b[k], val = r[k];
        const delta = (val ?? 0) - (base ?? 0);
        return `<tr><td>${l}</td>
          <td class="r num tight">${f(base)}</td>
          <td class="r num tight"><b>${f(val)}</b></td>
          <td class="r num tight ${Math.abs(delta) < 1e-6 ? 'note' : delta > 0 ? 'delta up' : 'delta down'}">${
            Math.abs(delta) < 1e-6 ? "no change" : (delta > 0 ? "+" : "") + f(delta)}</td>
          <td class="note" style="max-width:420px">${esc(r.notes[k] || "")}</td></tr>`;
      }).join("")}</tbody>
    </table></div>`;

  document.getElementById("wi-assume").textContent = {
    UTILIZATION_CHANGE: "Cost is fixed in this scenario. Consultants are on the payroll whether or not the work is sold.",
    HEADCOUNT_CHANGE: `New joiners ramp over ${nf(b.ramp_months)} months. Cost lands immediately.`,
    RATE_CHANGE: `${pc(b.reprice_share * 100, 0)} of revenue can be repriced within a year.`,
    CUSTOMER_LOSS: "The account leaves, the people stay. Cost only falls if a decision is taken to reduce it.",
    PROJECT_DELAY: `Delay costs ${pc(b.delay_idle_factor * 100, 0)} of remaining work per month in stand-down and re-testing.`,
    NEW_DEAL: "The deal is costed at the blended cost rate with the historical overrun applied.",
  }[whatifKind];
}

/* ===================== 40. Deal go / no-go ============================= */
const DEAL_FORM = { value: 2000000, hours: 11000, months: 9, practice: null,
                    type: "Implementation", methodology: "Standard Waterfall", customer: null };

function assessClient(o, f) {
  const b = o.baseline;
  const practice = o.practices.find(p => p.practice_id === +f.practice) || o.practices[0];
  const target = practice.target_margin_pct;
  const cohort = o.cohort_stats.by_type_method[`${f.type}|${f.methodology}`] ||
                 o.cohort_stats.org || {};
  const peerOverrun = Math.max(0, cohort.avg_overrun || 0);
  const costRate = b.avg_cost_rate;
  const expectedHours = f.hours * (1 + peerOverrun);
  const expectedCost = expectedHours * costRate * 1.03;
  const margin = f.value ? (f.value - expectedCost) / f.value * 100 : null;

  const caps = o.capacity.filter(c => c.practice_id === practice.practice_id);
  const free = Math.max(0, caps.reduce((a, c) => a + c.gap_hours, 0));
  const horizon = 6;
  const hoursInHorizon = f.hours * Math.min(horizon, f.months) / Math.max(f.months, 1e-9);
  const availability = Math.min(100, hoursInHorizon ? free / hoursInHorizon * 100 : 0);

  const cust = f.customer ? o.cohort_stats.by_customer[String(f.customer)] : null;
  const impliedRate = f.hours ? f.value / f.hours : 0;

  const factors = [
    { code: "MARGIN", label: "Expected margin",
      verdict: margin >= target - 2 ? "pass" : margin >= target - 12 ? "caution" : "fail",
      statement: `Expected margin ${pc(margin)} against a ${pc(target, 0)} target, after applying the ` +
        `${pc(peerOverrun * 100, 1)} overrun that ${nf(cohort.n || 0)} comparable completed projects actually incurred` },
    { code: "RESOURCE", label: "Resource availability",
      verdict: availability >= 85 ? "pass" : availability >= 55 ? "caution" : "fail",
      statement: `${pc(availability, 0)} of the ${hrs(hoursInHorizon)} falling in the next six months is ` +
        `uncommitted in ${esc(practice.practice_name)} (${hrs(free)} free against a ${hrs(f.hours)} engagement)` },
    { code: "DELIVERY", label: "Delivery risk",
      verdict: (cohort.over_budget_rate || 0) < 0.4 ? "pass" : (cohort.over_budget_rate || 0) < 0.65 ? "caution" : "fail",
      statement: `${pc((cohort.over_budget_rate || 0) * 100, 0)} of ${nf(cohort.n || 0)} comparable projects exceeded their hours budget` },
    { code: "SCHEDULE", label: "Schedule risk",
      verdict: (cohort.late_rate || 0) < 0.35 ? "pass" : (cohort.late_rate || 0) < 0.6 ? "caution" : "fail",
      statement: `${pc((cohort.late_rate || 0) * 100, 0)} of comparable projects delivered more than 10 days late` },
    { code: "CUSTOMER", label: "Customer history",
      verdict: !cust ? "caution" : (cust.avg_margin_pct >= target - 3 ? "pass" : cust.avg_margin_pct >= target - 12 ? "caution" : "fail"),
      statement: cust ? `${nf(cust.n)} completed projects for this customer averaged ${pc(cust.avg_margin_pct)} margin and ` +
        `${nf(cust.avg_change_orders, 1)} change orders`
        : "No completed project history for this customer, so no basis for a customer-specific adjustment" },
    { code: "RATE", label: "Implied rate",
      verdict: impliedRate >= b.realized_rate * 0.97 ? "pass" : impliedRate >= b.realized_rate * 0.88 ? "caution" : "fail",
      statement: `Implied rate ${money(impliedRate)}/hr against a portfolio realised rate of ${money(b.realized_rate)}/hr` },
  ];
  const fails = factors.filter(x => x.verdict === "fail").length;
  const cautions = factors.filter(x => x.verdict === "caution").length;
  const rec = (fails >= 2 || margin < target - 20 || availability < 40) ? "NO-GO"
    : (fails || cautions >= 2) ? "REVIEW" : "GO";

  const recs = [];
  if (margin < target) {
    const needed = expectedCost / (1 - target / 100);
    recs.push(`Increase the SOW by ${money(needed - f.value)} to ${money(needed)} to reach the ${pc(target, 0)} target margin`);
    const reduce = f.hours - (f.value * (1 - target / 100) / (costRate * 1.03 * (1 + peerOverrun)));
    if (reduce > 0) recs.push(`Or take ${hrs(reduce)} out of scope at the same price`);
  }
  if (availability < 85) {
    const short = hoursInHorizon * (1 - availability / 100);
    recs.push(`Secure ${hrs(short)} of additional capacity, about ${nf(short / (b.annual_hours * horizon / 12), 1)} FTE across the next six months, before signing`);
  }
  if ((cohort.over_budget_rate || 0) >= 0.5) {
    recs.push(`Build the observed ${pc(peerOverrun * 100, 1)} overrun into the estimate rather than the risk register`);
  }
  if (!recs.length) recs.push("No material adjustment indicated on current evidence");

  return { rec, margin, target, expectedHours, expectedCost, expectedProfit: f.value - expectedCost,
           availability, peerOverrun, cohort, factors, recs, practice, impliedRate };
}

function renderGonogo(el) {
  const o = org();
  if (!DEAL_FORM.practice) DEAL_FORM.practice = o.practices[0].practice_id;
  const types = [...new Set(o.portfolio.map(p => p.project_type))].filter(Boolean).sort();
  const methods = [...new Set(o.portfolio.map(p => p.methodology))].filter(Boolean).sort();
  const customers = (o.profitability.customer || []).slice().sort((a, b) => b.revenue - a.revenue);

  el.innerHTML = `
    <div class="viewhead">
      <div class="eyebrow">Requirement 40</div>
      <h1>Deal go / no-go</h1>
      <p>Score a deal before it is signed, against what comparable work actually cost and
         what capacity is actually free. The recommendation is a rule over the factors
         below, not a single score, so a disagreement can be about the factor rather than
         about the model.</p>
    </div>
    <div class="split controls wide">
      <div class="card">
        <h3>Deal</h3>
        <div style="margin-top:12px">
          <label class="field"><span class="lbl">Contract value</span>
            <input type="number" id="d-value" value="${DEAL_FORM.value}" step="50000" min="0"></label>
          <label class="field"><span class="lbl">Proposed hours</span>
            <input type="number" id="d-hours" value="${DEAL_FORM.hours}" step="250" min="0"></label>
          <label class="field"><span class="lbl">Timeline (months)</span>
            <input type="number" id="d-months" value="${DEAL_FORM.months}" step="1" min="1" max="36"></label>
          <label class="field"><span class="lbl">Practice</span>
            <select id="d-practice">${o.practices.map(p =>
              `<option value="${p.practice_id}" ${p.practice_id === DEAL_FORM.practice ? "selected" : ""}>${esc(p.practice_name)}</option>`).join("")}</select></label>
          <label class="field"><span class="lbl">Project type</span>
            <select id="d-type">${types.map(t =>
              `<option ${t === DEAL_FORM.type ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></label>
          <label class="field"><span class="lbl">Methodology</span>
            <select id="d-method">${methods.map(m =>
              `<option ${m === DEAL_FORM.methodology ? "selected" : ""}>${esc(m)}</option>`).join("")}</select></label>
          <label class="field"><span class="lbl">Customer</span>
            <select id="d-customer"><option value="">New or unknown customer</option>
              ${customers.map(c => `<option value="${c.dimension_key}" ${String(c.dimension_key) === String(DEAL_FORM.customer) ? "selected" : ""}>${esc(c.dimension_label)}</option>`).join("")}</select></label>
        </div>
      </div>
      <div id="deal-out"></div>
    </div>
    <div class="card" style="margin-top:16px">
      <h3>Open opportunities already assessed</h3>
      <p class="sub">The engine scores the largest open pipeline on every run.</p>
      <div class="tblwrap"><table>
        <thead><tr><th>Opportunity</th><th class="r">Value</th><th class="r">Hours</th>
          <th class="r">Expected margin</th><th class="r">Availability</th>
          <th class="r">Peer overrun</th><th>Recommendation</th></tr></thead>
        <tbody>${o.deals.map(d => `
          <tr><td>${esc(d.deal_name)}<div class="note">${esc(d.customer_name || 'new customer')} · ${esc(d.billing_model || '')}</div></td>
            <td class="r num tight">${moneyK(d.contract_value)}</td>
            <td class="r num">${nf(d.proposed_hours, 0)}</td>
            <td class="r num">${pc(d.expected_margin_pct)}<div class="note">tgt ${pc(d.target_margin_pct, 0)}</div></td>
            <td class="r num">${pc(d.resource_availability_pct, 0)}</td>
            <td class="r num">${pc(d.peer_overrun_pct)}</td>
            <td>${pill(d.recommendation === 'GO' ? 'good' : d.recommendation === 'REVIEW' ? 'warn' : 'bad', d.recommendation)}
              <details class="evidence"><summary>Factors</summary>
                <div style="margin-top:6px">${d.factors.map(f =>
                  `<div style="margin-bottom:5px"><span class="verdict ${f.verdict}">${f.verdict}</span>
                   <span class="note">${esc(f.statement)}</span></div>`).join("")}
                  <div style="margin-top:8px"><b style="font-family:var(--font-ui);font-size:12px">Recommended</b>
                  ${d.recommendations.map(r => `<div class="note">${esc(r.statement)}</div>`).join("")}</div>
                </div></details></td>
          </tr>`).join("")}</tbody>
      </table></div>
    </div>`;

  const bind = (id, key, num) => {
    const e = document.getElementById(id);
    e.addEventListener("input", () => {
      DEAL_FORM[key] = num ? (+e.value || 0) : e.value;
      drawDeal();
    });
    e.addEventListener("change", () => {
      DEAL_FORM[key] = num ? (+e.value || 0) : e.value;
      drawDeal();
    });
  };
  bind("d-value", "value", true); bind("d-hours", "hours", true);
  bind("d-months", "months", true); bind("d-practice", "practice", true);
  bind("d-type", "type", false); bind("d-method", "methodology", false);
  bind("d-customer", "customer", false);
  drawDeal();
}

function drawDeal() {
  const o = org();
  const a = assessClient(o, DEAL_FORM);
  const cls = a.rec === "GO" ? "good" : a.rec === "REVIEW" ? "warn" : "bad";
  document.getElementById("deal-out").innerHTML = `
    <div class="card" style="border-left:3px solid var(--${cls === 'good' ? 'good' : cls === 'warn' ? 'warn' : 'bad'})">
      <div class="row">
        <div>
          <div class="label" style="font-family:var(--font-ui);font-size:11.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3)">Recommendation</div>
          <div style="font-family:var(--font-ui);font-size:30px;font-weight:700;color:var(--${cls === 'good' ? 'good' : cls === 'warn' ? 'warn' : 'bad'})">${a.rec}</div>
        </div>
        <div class="spacer"></div>
        <div class="grid g4" style="flex:1 1 420px">
          <div><dl class="kv"><dt>Expected margin</dt><dd><b>${pc(a.margin)}</b></dd>
            <dt>Target</dt><dd>${pc(a.target, 0)}</dd></dl></div>
          <div><dl class="kv"><dt>Expected revenue</dt><dd>${moneyK(DEAL_FORM.value)}</dd>
            <dt>Expected profit</dt><dd>${moneyK(a.expectedProfit)}</dd></dl></div>
          <div><dl class="kv"><dt>Expected hours</dt><dd>${nf(a.expectedHours, 0)}</dd>
            <dt>Expected cost</dt><dd>${moneyK(a.expectedCost)}</dd></dl></div>
          <div><dl class="kv"><dt>Availability</dt><dd>${pc(a.availability, 0)}</dd>
            <dt>Implied rate</dt><dd>${money(a.impliedRate)}/h</dd></dl></div>
        </div>
      </div>

      <h4 style="margin:18px 0 8px;font-size:13px">Factors</h4>
      <div class="tblwrap"><table><tbody>${a.factors.map(f => `
        <tr><td style="width:150px"><b style="font-family:var(--font-ui);font-size:12.5px">${f.label}</b></td>
          <td style="width:74px"><span class="verdict ${f.verdict}">${f.verdict}</span></td>
          <td>${f.statement}</td></tr>`).join("")}</tbody></table></div>

      <h4 style="margin:18px 0 8px;font-size:13px">What to do about it</h4>
      <ul style="margin:0;padding-left:20px">${a.recs.map(r => `<li style="margin-bottom:4px">${esc(r)}</li>`).join("")}</ul>

      <p class="note" style="margin-top:14px">Comparable cohort: ${nf(a.cohort.n || 0)} completed
        ${esc(DEAL_FORM.type)} projects using ${esc(DEAL_FORM.methodology)}, average overrun
        ${pc(a.peerOverrun * 100, 1)}, average margin ${pc(a.cohort.avg_margin_pct)}.
        ${(a.cohort.n || 0) < 5 ? "Fewer than five comparable projects, so the org-wide cohort was used instead." : ""}</p>
    </div>`;
}
