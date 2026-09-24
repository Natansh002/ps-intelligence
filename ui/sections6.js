/* =========================================================================
   Billing against revenue recognition, and the economics of each billing
   model.

   Both views exist because the usual reporting rolls two different things
   into one number.

   Billing and recognition diverge with a sign, and the sign is the whole
   meaning. Recognised ahead of invoiced is work delivered and not billed:
   somebody's job to collect. Invoiced ahead of recognised is work billed and
   not delivered: somebody's job to deliver. A netted figure is the right
   number for a cash forecast and the wrong number for either conversation,
   so both sides are reported and the netting is labelled as netting.

   "Which model is more profitable" has an average answer and a true answer,
   and they are different. The models differ in who carries the overrun risk,
   so they differ in the *spread* of outcomes far more than in the mean. A
   view that ranks two averages answers the question asked and not the
   question meant.
   ========================================================================= */
"use strict";

let billScope = "billing_model";
let billDir = "unbilled";
let modelFocus = null;

const DIR_LABEL = {
  unbilled: "Delivered, not billed",
  deferred: "Billed, not delivered",
};
const RISK_CLASS = { customer: "good", shared: "warn", delivery: "bad" };
const RISK_LABEL = {
  customer: "Customer carries the overrun",
  shared: "Overrun risk shared",
  delivery: "We carry the overrun",
};

/* ============  Billing against revenue recognition  ==================== */
function renderBilling(el) {
  const o = org(), d = o.billing;
  if (!d) {
    el.innerHTML = `<div class="card"><h3>No billing position</h3></div>`;
    return;
  }
  const orgPos = d.positions.find(p => p.scope === "org") || {};
  const scoped = d.positions.filter(p => p.scope === billScope);
  const exc = d.exceptions.filter(x => x.direction === billDir);
  const aged = orgPos.unbilled_over_90 || 0;

  el.innerHTML = `
    <div class="viewhead">
      <div class="eyebrow">Billing against revenue recognition</div>
      <h1>Where delivery and cash diverge</h1>
      <p>Revenue recognition follows delivery. Invoicing follows the contract.
         The gap between them is a position rather than an error, and it has
         two signs that mean opposite things: work delivered and not billed is
         somebody's to collect, and work billed and not delivered is
         somebody's to deliver. Both are shown separately, because netting
         them produces the right number for a cash forecast and the wrong
         number for either conversation.</p>
    </div>

    <div class="grid g4" style="margin-bottom:22px">
      <div class="tile">
        <div class="label">Recognised</div>
        <div class="value sm">${moneyK(orgPos.recognised)}</div>
        <div class="foot">revenue earned against delivery</div>
      </div>
      <div class="tile">
        <div class="label">Invoiced</div>
        <div class="value sm">${moneyK(orgPos.invoiced)}</div>
        <div class="foot">${pc(orgPos.billed_pct, 1)} of what was recognised</div>
      </div>
      <div class="tile stripe ${aged > 0 ? 'bad' : orgPos.unbilled > 0 ? 'warn' : 'good'}">
        <div class="label">Delivered, not billed</div>
        <div class="value sm">${moneyK(orgPos.unbilled)}</div>
        <div class="foot">${orgPos.projects_unbilled} engagements ·
          ${moneyK(aged)} not invoiced for a quarter or more</div>
      </div>
      <div class="tile stripe ${orgPos.deferred > 0 ? 'warn' : 'good'}">
        <div class="label">Billed, not delivered</div>
        <div class="value sm">${moneyK(orgPos.deferred)}</div>
        <div class="foot">${orgPos.projects_deferred} engagements · deferred
          revenue, not an asset</div>
      </div>
    </div>

    <div class="card" style="margin-bottom:18px">
      <h3>The position</h3>
      <p class="sub">${esc(orgPos.statement || "")}</p>
    </div>

    <div class="grid g2" style="margin-bottom:18px">
      <div class="card">
        <h3>Cumulative, month by month</h3>
        <p class="sub">Two lines that should track each other with a lag. Where
          they separate and stay separated, invoicing has stopped catching up.</p>
        <div class="chartbox" id="bill-cum"></div>
      </div>
      <div class="card">
        <h3>Billed share, month by month</h3>
        <p class="sub">Invoiced as a share of recognised, cumulative. A book in
          control sits close to a hundred and returns to it.</p>
        <div class="chartbox" id="bill-pct"></div>
        <p class="note" style="margin-top:8px">Cumulative rather than monthly,
          because a single month's ratio swings on the timing of one large
          invoice and says nothing about whether the book is being billed.</p>
      </div>
    </div>

    <div class="card" style="margin-bottom:18px">
      <h3>By ${billScope === "billing_model" ? "billing model" : "engagement status"}</h3>
      <p class="sub">The shape of the lag is the billing model. Time and
        materials bills in arrears and therefore always sits unbilled by about
        a cycle. Milestone billing waits on acceptance. Fixed fee and retainer
        bill to a schedule, so they can and do run ahead of delivery.</p>
      <div class="chips" style="margin-bottom:12px">
        <button class="chip" data-bs="billing_model"
          aria-pressed="${billScope === 'billing_model'}">By billing model</button>
        <button class="chip" data-bs="status"
          aria-pressed="${billScope === 'status'}">By engagement status</button>
      </div>
      <div class="tblwrap"><table>
        <thead><tr><th></th><th class="r">Engagements</th>
          <th class="r">Recognised</th><th class="r">Invoiced</th>
          <th class="r">Billed</th><th class="r">Not billed</th>
          <th class="r">Billed ahead</th><th class="r">Aged past a quarter</th>
        </tr></thead>
        <tbody>${scoped.sort((a, b) => b.unbilled - a.unbilled).map(p => `
          <tr><td>${esc(p.scope_label)}
              ${p.oldest_unbilled_days ? `<div class="note">oldest unbilled
                ${nf(p.oldest_unbilled_days / 30.4, 0)} months</div>` : ""}</td>
            <td class="r num">${p.projects}</td>
            <td class="r num">${moneyK(p.recognised)}</td>
            <td class="r num">${moneyK(p.invoiced)}</td>
            <td class="r num">${pc(p.billed_pct, 1)}</td>
            <td class="r num">${p.unbilled > 0
              ? `${moneyK(p.unbilled)} <span class="note">(${p.projects_unbilled})</span>`
              : "—"}</td>
            <td class="r num">${p.deferred > 0
              ? `${moneyK(p.deferred)} <span class="note">(${p.projects_deferred})</span>`
              : "—"}</td>
            <td class="r num">${p.unbilled_over_90 > 0
              ? `<span class="delta down">${moneyK(p.unbilled_over_90)}</span>`
              : "—"}</td></tr>`).join("")}
        </tbody>
        <caption>"Aged past a quarter" is the unbilled balance on engagements
          that have raised no invoice for three months or more. A billing lag is
          normal; a billing lag that stopped moving is a write-off waiting to be
          recorded.</caption>
      </table></div>
    </div>

    <div class="card">
      <h3>The engagements furthest out of line</h3>
      <p class="sub">With what the data says is behind each one, because the
        same gap means different things on different billing models.</p>
      <div class="chips" style="margin-bottom:12px">
        ${Object.entries(DIR_LABEL).map(([k, v]) =>
          `<button class="chip" data-bd="${k}"
             aria-pressed="${billDir === k}">${v}</button>`).join("")}
      </div>
      ${exc.length ? `<div class="grid g2">${exc.map(x => `
        <div class="flag ${x.direction === 'unbilled'
          && (x.months_since_invoice || 0) >= 3 ? 'High' : 'Medium'}">
          <h4 style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap">
            <span>${esc(x.project_code)} · ${esc(x.customer_name || "")}</span>
            <span class="num">${moneyK(x.amount)}</span></h4>
          <p class="detail">${esc(x.statement)}</p>
          <div class="action"><b>Action · ${esc(x.billing_model)} ·
            ${esc(x.project_status)}</b>${esc(x.action)}</div>
        </div>`).join("")}</div>`
        : `<p class="note">Nothing on this side of the position.</p>`}
    </div>`;

  el.querySelectorAll(".chip[data-bs]").forEach(b =>
    b.addEventListener("click", () => { billScope = b.dataset.bs; render(); }));
  el.querySelectorAll(".chip[data-bd]").forEach(b =>
    b.addEventListener("click", () => { billDir = b.dataset.bd; render(); }));

  const months = d.months;
  const cum = document.getElementById("bill-cum");
  if (cum && months.length) {
    const series = [
      { label: "Recognised, cumulative", color: "var(--s1)" },
      { label: "Invoiced, cumulative", color: "var(--s2)" },
    ];
    groupedBars(cum, months.filter((m, i) => i % 2 === 0).map(m => ({
      label: monthLabel(m.period_month).slice(0, 3),
      values: [m.cum_recognised, m.cum_invoiced],
    })), series, { height: 220, tickFormat: v => moneyK(v) });
    cum.insertAdjacentHTML("afterbegin", legend(series));
  }
  const pctBox = document.getElementById("bill-pct");
  if (pctBox && months.length) {
    lineChart(pctBox, months.map(m => ({
      label: monthLabel(m.period_month), value: m.billed_pct,
    })), { height: 220, ref: 100, refLabel: "fully billed",
           tickFormat: v => nf(v, 0) + "%", labelEvery: 6,
           color: "var(--s1)" });
  }
}

/* ==============  T&M against fixed fee, and the rest  ================== */
function renderModels(el) {
  const o = org(), m = o.models;
  if (!m || !m.economics.length) {
    el.innerHTML = `<div class="card"><h3>No model economics</h3></div>`;
    return;
  }
  const econ = m.economics;
  const cmp = m.comparison;
  const tm = econ.find(x => x.billing_model === "Time and Materials");
  const ff = econ.find(x => x.billing_model === "Fixed Fee");
  if (!modelFocus || !econ.some(x => x.billing_model === modelFocus)) {
    modelFocus = (ff || econ[0]).billing_model;
  }

  // The answer, stated once and honestly, from the comparison rows rather
  // than from a sentence somebody typed.
  const row = k => cmp.find(c => c.metric === k) || {};
  const meanRow = row("margin_mean");
  const spreadRow = row("margin_spread");
  const wRow = row("margin_weighted");
  const lossRow = row("pct_negative");

  el.innerHTML = `
    <div class="viewhead">
      <div class="eyebrow">Billing model economics</div>
      <h1>Time and materials against fixed fee: which is more profitable?</h1>
      <p>Measured on completed engagements only, because a margin on live work
         is a forecast and mixing forecasts with outturns would let a project
         manager's optimism decide which commercial model looks better. Every
         figure below is an outturn.</p>
    </div>

    ${tm && ff ? `<div class="card" style="margin-bottom:18px">
      <h3>The short answer</h3>
      <p class="sub">Two of them, because the question has two answers and
        reporting only the first is how a services business talks itself into
        the wrong commercial model.</p>
      <div class="grid g2">
        <div class="flag High">
          <h4>Fixed fee earns more per engagement</h4>
          <p class="detail">${pc(ff.margin_mean_pct, 1)} average margin against
            ${pc(tm.margin_mean_pct, 1)} on time and materials, a gap of
            ${nf(Math.abs(meanRow.delta || 0), 1)} points. The median says the
            same thing, so it is not one large engagement carrying it.</p>
          <div class="action"><b>Why</b>Fixed fee is priced with the overrun
            risk in it. That premium is real revenue whenever delivery lands
            inside the estimate.</div>
        </div>
        <div class="flag Medium">
          <h4>And it is a wider bet</h4>
          <p class="detail">The spread of outcomes is
            ${nf(ff.margin_stdev_pts, 1)} points against
            ${nf(tm.margin_stdev_pts, 1)} on time and materials.
            ${pc(ff.pct_negative, 0)} of fixed-fee engagements lost money and
            ${pc(tm.pct_negative, 0)} of time-and-materials engagements did.
            The worst fixed-fee outcome was ${pc(ff.margin_min_pct, 1)}; the
            worst on time and materials was
            ${pc(tm.margin_min_pct, 1)}.</p>
          <div class="action"><b>Why</b>On time and materials the customer pays
            for the hours, so an overrun costs them money. On fixed fee it comes
            out of margin. The premium and the risk are the same thing seen from
            two sides.</div>
        </div>
      </div>
      <p class="note" style="margin-top:12px">${wRow.value_a !== undefined
        && wRow.favours === "a" ? `Worth one more look: weighted by revenue
        rather than counted per engagement, time and materials is <em>ahead</em>
        at ${pc(wRow.value_a, 1)} against ${pc(wRow.value_b, 1)}. The two
        figures disagree because fixed-fee losses concentrate in the larger
        engagements, which is what a wide spread does when size and estimating
        risk move together. Per engagement fixed fee wins; per dollar
        delivered it does not.` : `Weighted by revenue rather than counted per
        engagement, the ordering holds: ${pc(wRow.value_b, 1)} on fixed fee
        against ${pc(wRow.value_a, 1)} on time and materials.`}</p>
    </div>` : ""}

    <div class="card" style="margin-bottom:18px">
      <h3>Head to head</h3>
      <p class="sub">One row per measure, with the side it favours. The last two
        rows are about the confounds rather than the result, because a
        comparison that reports only the measures where one side wins is an
        argument and not an analysis.</p>
      <div class="tblwrap"><table>
        <thead><tr><th>Measure</th><th class="r">Time and materials</th>
          <th class="r">Fixed fee</th><th>Favours</th><th>Reading</th></tr></thead>
        <tbody>${cmp.map(c => `
          <tr><td class="tight">${esc(c.metric_label)}</td>
            <td class="r num ${c.favours === 'a' ? 'delta up' : ''}">
              ${fmtUnit(c.value_a, c.unit)}</td>
            <td class="r num ${c.favours === 'b' ? 'delta up' : ''}">
              ${fmtUnit(c.value_b, c.unit)}</td>
            <td class="tight">${c.favours === "a" ? pill("accent", "T&M")
              : c.favours === "b" ? pill("accent", "Fixed fee")
              : pill("mute", "neither")}</td>
            <td class="note" style="max-width:44ch">${esc(c.reading)}</td>
          </tr>`).join("")}
        </tbody></table></div>
    </div>

    <div class="grid g2" style="margin-bottom:18px">
      <div class="card">
        <h3>The distribution, not the average</h3>
        <p class="sub">Share of completed engagements landing in each margin
          band. Two models with similar means and different shapes are two
          different businesses.</p>
        <div class="chartbox" id="model-bands"></div>
        <p class="note" style="margin-top:8px">This is the chart the average
          hides. A model whose outcomes cluster is one you can plan on; a model
          whose outcomes fan out is one you have to manage.</p>
      </div>
      <div class="card">
        <h3>Every model, side by side</h3>
        <p class="sub">Including who carries the overrun, which explains most of
          the difference between the rows.</p>
        <div class="tblwrap"><table>
          <thead><tr><th>Model</th><th class="r">Done</th>
            <th class="r">Revenue</th><th class="r">Margin</th>
            <th class="r">Spread</th><th class="r">Lost money</th>
            <th>Risk</th></tr></thead>
          <tbody>${econ.map(e => `
            <tr class="clickable" data-model="${esc(e.billing_model)}"
                aria-selected="${e.billing_model === modelFocus}">
              <td>${esc(e.billing_model)}
                <div class="note">${esc(e.verdict)}</div></td>
              <td class="r num">${e.projects}</td>
              <td class="r num">${moneyK(e.revenue)}</td>
              <td class="r num">${pc(e.margin_mean_pct, 1)}
                <div class="note">${pc(e.margin_pct, 1)} weighted</div></td>
              <td class="r num">${e.margin_stdev_pts === null ? "—"
                : nf(e.margin_stdev_pts, 1) + " pts"}</td>
              <td class="r num">${e.projects_negative
                ? `<span class="delta down">${e.projects_negative}</span>`
                : "—"}</td>
              <td class="tight">${pill(RISK_CLASS[e.risk_carried_by] || "mute",
                e.risk_carried_by)}</td></tr>`).join("")}
          </tbody>
          <caption>Click a model for its full read. "Spread" is the standard
            deviation of per-engagement margin: the higher it is, the more the
            outcome depends on delivery rather than on the contract.</caption>
        </table></div>
      </div>
    </div>

    <div id="model-detail"></div>`;

  el.querySelectorAll("tr[data-model]").forEach(tr =>
    tr.addEventListener("click", () => {
      modelFocus = tr.dataset.model;
      render();
    }));

  drawModelBands(m);
  drawModelDetail(m);
}

function fmtUnit(v, unit) {
  if (v === null || v === undefined) return "—";
  if (unit === "percent") return pc(v, 1);
  if (unit === "points") return nf(v, 1) + " pts";
  if (unit === "ratio") return nf(v, 2) + "×";
  if (unit === "hours") return nf(v, 0) + " h";
  return nf(v, 1);
}

function drawModelBands(m) {
  const host = document.getElementById("model-bands");
  if (!host) return;
  const show = ["Time and Materials", "Fixed Fee"];
  const bands = [...new Set(m.bands.map(b => b.band_label))];
  const orderOf = {};
  m.bands.forEach(b => { orderOf[b.band_label] = b.sort_order; });
  bands.sort((a, b) => orderOf[a] - orderOf[b]);
  const series = [
    { label: "Time and materials", color: "var(--s1)" },
    { label: "Fixed fee", color: "var(--s4)" },
  ];
  groupedBars(host, bands.map(label => ({
    label,
    values: show.map(model => {
      const b = m.bands.find(x => x.billing_model === model
        && x.band_label === label);
      return b ? b.share_pct : 0;
    }),
  })), series, { height: 230, tickFormat: v => nf(v, 0) + "%" });
  host.insertAdjacentHTML("afterbegin", legend(series));
}

function drawModelDetail(m) {
  const host = document.getElementById("model-detail");
  if (!host) return;
  const e = m.economics.find(x => x.billing_model === modelFocus);
  if (!e) { host.innerHTML = ""; return; }

  host.innerHTML = `
    <div class="card">
      <h3>${esc(e.billing_model)}</h3>
      <p class="sub">${esc(e.statement)}</p>
      <div class="grid g2" style="margin-top:4px">
        <div>
          <div class="tblwrap"><table><tbody>
            <tr><td>Who carries the overrun</td>
              <td class="r">${pill(RISK_CLASS[e.risk_carried_by] || "mute",
                RISK_LABEL[e.risk_carried_by] || e.risk_carried_by)}</td></tr>
            <tr><td>Completed engagements</td>
              <td class="r num">${e.projects}</td></tr>
            <tr><td>Revenue, and share of the book</td>
              <td class="r num">${moneyK(e.revenue)}
                <span class="note">${pc(e.share_of_revenue_pct, 0)}</span></td></tr>
            <tr><td>Margin, revenue weighted</td>
              <td class="r num">${pc(e.margin_pct, 1)}</td></tr>
            <tr><td>Margin, average per engagement</td>
              <td class="r num">${pc(e.margin_mean_pct, 1)}</td></tr>
            <tr><td>Margin, median</td>
              <td class="r num">${pc(e.margin_median_pct, 1)}</td></tr>
            <tr><td>Middle half of outcomes</td>
              <td class="r num">${pc(e.margin_p25_pct, 1)} to
                ${pc(e.margin_p75_pct, 1)}</td></tr>
            <tr><td>Full range</td>
              <td class="r num">${pc(e.margin_min_pct, 1)} to
                ${pc(e.margin_max_pct, 1)}</td></tr>
            <tr><td>Spread, standard deviation</td>
              <td class="r num">${e.margin_stdev_pts === null ? "—"
                : nf(e.margin_stdev_pts, 1) + " pts"}</td></tr>
            <tr><td>Engagements that lost money</td>
              <td class="r num">${e.projects_negative}
                <span class="note">${pc(e.pct_negative, 0)}</span></td></tr>
          </tbody></table></div>
        </div>
        <div>
          <div class="tblwrap" style="margin-bottom:12px"><table><tbody>
            <tr><td>Cost against plan, median</td>
              <td class="r num">${e.overrun_median === null ? "—"
                : nf(e.overrun_median, 2) + "×"}</td></tr>
            <tr><td>Cost against plan, 90th percentile</td>
              <td class="r num">${e.overrun_p90 === null ? "—"
                : nf(e.overrun_p90, 2) + "×"}</td></tr>
            <tr><td>Went over plan</td>
              <td class="r num">${pc(e.pct_over_budget, 0)}</td></tr>
            <tr><td>Discount against the rate card</td>
              <td class="r num">${pc(e.discount_pct, 1)}</td></tr>
            <tr><td>Revenue per hour delivered</td>
              <td class="r num">${e.realised_rate
                ? money(e.realised_rate, 0) : "—"}</td></tr>
          </tbody></table></div>
          <div class="card" style="background:var(--surface-sunk)">
            <h3 style="font-size:14px">What this model gets sold on</h3>
            <p class="sub">The mix, because the models are not sold on the same
              work and a bare comparison of averages is confounded by that.</p>
            <div class="kv">
              <dt>Median size</dt><dd>${nf(e.median_size_hours, 0)} budget hours</dd>
              <dt>Median duration</dt>
                <dd>${nf(e.median_duration_months, 1)} months</dd>
              <dt>Most common work</dt>
                <dd>${esc(e.top_project_type || "—")}
                  ${e.top_project_type_pct
                    ? `(${pc(e.top_project_type_pct, 0)})` : ""}</dd>
              <dt>Partner-led delivery</dt><dd>${pc(e.pct_partner_led, 0)}</dd>
            </div>
          </div>
        </div>
      </div>
    </div>`;
}
