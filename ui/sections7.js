/* =========================================================================
   Earned value: cost and schedule performance.

   This view exists because consumption, progress and margin between them
   cannot separate a cost problem from a schedule one. An engagement at 60%
   consumed and 40% complete, and one at 40% consumed and 40% complete against
   a plan that expected 60% by now, look similar on a burn chart and need
   opposite responses. The first is over cost. The second is on cost and late.

   One definitional choice decides whether any of this is worth reading.
   Percent complete here is earned plan: the share of planned task hours whose
   tasks are actually finished. It is not a number somebody types in, and it is
   not hours spent over estimate at completion, which is how at least one major
   PSA product defines it. Under that definition earned value equals actual
   cost by construction, the cost index is identically 1.00, and the metric
   cannot report a cost problem however much money the engagement is losing.
   ========================================================================= */
"use strict";

let evmScope = "product";
let evmQuad = "all";

const QUAD_COLOR = {
  over: "var(--s3)",       // over cost
  behind: "var(--s2)",     // behind schedule
  both: "var(--s4)",       // both
  ok: "var(--s1)",
};

function evmGroup(p) {
  const over = p.cost_band === "over", behind = p.schedule_band === "behind";
  return over && behind ? "both" : over ? "over" : behind ? "behind" : "ok";
}

const GROUP_LABEL = {
  both: "Over cost and behind schedule",
  over: "Over cost",
  behind: "Behind schedule",
  ok: "Inside tolerance",
};

function renderEvm(el) {
  const o = org(), d = o.evm;
  if (!d) {
    el.innerHTML = `<div class="card"><h3>No earned value run</h3>
      <p class="note">Nothing live carries a current plan with planned hours
        and planned dates, so no index can be computed.</p></div>`;
    return;
  }
  const orgRow = d.summary.find(s => s.scope === "org") || {};
  const live = d.projects.filter(p => p.is_reportable);
  const held = d.projects.filter(p => !p.is_reportable);
  const scoped = d.summary.filter(s => s.scope === evmScope);
  const shown = evmQuad === "all" ? live
    : live.filter(p => evmGroup(p) === evmQuad);

  const cpiBad = (orgRow.cpi || 1) < 0.95;
  const spiBad = (orgRow.spi || 1) < 0.95;

  el.innerHTML = `
    <div class="viewhead">
      <div class="eyebrow">Earned value</div>
      <h1>Are we over cost, or behind, or both</h1>
      <p>Two indices, read together. The cost index is earned value over actual
         cost: below one, the work completed cost more than it was worth. The
         schedule index is earned value over planned value: below one, less has
         been earned than the plan expected by now. Neither answers the question
         alone, which is why the quadrant is named on every engagement rather
         than left to the reader to assemble from two decimals.</p>
    </div>

    <div class="grid g4" style="margin-bottom:22px">
      <div class="tile stripe ${cpiBad ? ((orgRow.cpi || 1) < 0.85 ? 'bad' : 'warn') : 'good'}">
        <div class="label">Cost index, budget weighted</div>
        <div class="value">${nf(orgRow.cpi, 2)}</div>
        <div class="foot">median engagement ${nf(orgRow.cpi_median, 2)}</div>
      </div>
      <div class="tile stripe ${spiBad ? ((orgRow.spi || 1) < 0.85 ? 'bad' : 'warn') : 'good'}">
        <div class="label">Schedule index, budget weighted</div>
        <div class="value">${nf(orgRow.spi, 2)}</div>
        <div class="foot">median engagement ${nf(orgRow.spi_median, 2)}</div>
      </div>
      <div class="tile stripe ${orgRow.projects_both > 0 ? 'bad' : 'good'}">
        <div class="label">Over cost and behind</div>
        <div class="value sm">${orgRow.projects_both} of ${orgRow.projects}</div>
        <div class="foot">${orgRow.projects_over_cost} over cost ·
          ${orgRow.projects_behind} behind schedule</div>
      </div>
      <div class="tile stripe ${(orgRow.vac_total || 0) < 0 ? 'bad' : 'good'}">
        <div class="label">Variance at completion</div>
        <div class="value sm">${moneyK(orgRow.vac_total)}</div>
        <div class="foot">at the efficiency achieved to date, against
          ${moneyK(orgRow.bac)} of budget</div>
      </div>
    </div>

    <div class="card" style="margin-bottom:18px">
      <h3>The portfolio reading</h3>
      <p class="sub">${esc(orgRow.statement || "")}</p>
      <p class="note" style="margin-top:10px">Budget weighted and unweighted are
        both shown because they answer different questions. The weighted figure
        is what the portfolio is doing with the money; the median is what a
        typical engagement is doing. Where they separate, the large engagements
        are not behaving like the small ones, and that is itself the finding.
        Indices are measured on labour, because budget at completion here is
        budget hours at a planned cost rate and carries no expense allowance.</p>
    </div>

    <div class="card" style="margin-bottom:18px">
      <h3>Cost against schedule, one point per engagement</h3>
      <p class="sub">Circle area is budget at completion. The reading is which
        quadrant a point sits in: the four combinations imply genuinely
        different actions, and either index on its own implies none of them.</p>
      <div class="chartbox" id="evm-scatter"></div>
      <div class="legend" style="margin-top:10px">
        ${Object.entries(GROUP_LABEL).map(([k, v]) =>
          `<span><i style="background:${QUAD_COLOR[k]}"></i>${v}</span>`).join("")}
      </div>
      <p class="note" style="margin-top:8px">Points are clamped to the axis
        range so an extreme index stays visible at the edge rather than
        stretching the scale for everything else. ${held.length} live
        ${held.length === 1 ? "engagement is" : "engagements are"} not plotted:
        too early to carry an index, listed at the foot of this view.</p>
    </div>

    <div class="card" style="margin-bottom:18px">
      <h3>By ${evmScope === "product" ? "product line"
             : evmScope === "practice" ? "practice" : "billing model"}</h3>
      <p class="sub">Rolled up on budget rather than averaged, so a
        forty-hour engagement cannot offset a four-thousand-hour one.</p>
      <div class="chips" style="margin-bottom:12px">
        <button class="chip" data-es="product"
          aria-pressed="${evmScope === 'product'}">By product line</button>
        <button class="chip" data-es="practice"
          aria-pressed="${evmScope === 'practice'}">By practice</button>
        <button class="chip" data-es="billing_model"
          aria-pressed="${evmScope === 'billing_model'}">By billing model</button>
      </div>
      <div class="tblwrap"><table>
        <thead><tr><th></th><th class="r">Live</th><th class="r">Budget</th>
          <th class="r">Cost index</th><th class="r">Schedule index</th>
          <th class="r">Over cost</th><th class="r">Behind</th>
          <th class="r">Both</th><th class="r">Variance at completion</th>
        </tr></thead>
        <tbody>${scoped.sort((a, b) => (a.cpi || 9) - (b.cpi || 9)).map(s => `
          <tr><td>${esc(s.scope_label)}
              ${s.projects_excluded ? `<div class="note">${s.projects_excluded}
                too early to measure</div>` : ""}</td>
            <td class="r num">${s.projects}</td>
            <td class="r num">${moneyK(s.bac)}</td>
            <td class="r num ${(s.cpi || 1) < 0.85 ? "delta down" : ""}">${nf(s.cpi, 2)}
              <div class="note">med ${nf(s.cpi_median, 2)}</div></td>
            <td class="r num ${(s.spi || 1) < 0.85 ? "delta down" : ""}">${nf(s.spi, 2)}
              <div class="note">med ${nf(s.spi_median, 2)}</div></td>
            <td class="r num">${s.projects_over_cost || "—"}</td>
            <td class="r num">${s.projects_behind || "—"}</td>
            <td class="r num">${s.projects_both
              ? `<span class="delta down">${s.projects_both}</span>` : "—"}</td>
            <td class="r num">${(s.vac_total || 0) < -500
              ? `<span class="delta down">${moneyK(s.vac_total)}</span>`
              : moneyK(s.vac_total)}</td></tr>`).join("")}
        </tbody>
        <caption>Variance at completion is budget less the independent estimate
          at completion, which is budget divided by the cost index achieved so
          far. It assumes the remaining work goes exactly as well as the work
          done, which is the least optimistic assumption available and still
          more optimistic than most recovery plans.</caption>
      </table></div>
    </div>

    <div class="card" style="margin-bottom:18px">
      <h3>Engagement by engagement</h3>
      <p class="sub">Ordered by the combined index, worst first. The quadrant
        is stated because the pair is the reading.</p>
      <div class="chips" style="margin-bottom:12px">
        <button class="chip" data-eq="all"
          aria-pressed="${evmQuad === 'all'}">All ${live.length}</button>
        ${["both", "over", "behind", "ok"].map(k => {
          const n = live.filter(p => evmGroup(p) === k).length;
          return n ? `<button class="chip" data-eq="${k}"
            aria-pressed="${evmQuad === k}">${GROUP_LABEL[k]} · ${n}</button>` : "";
        }).join("")}
      </div>
      <div class="tblwrap"><table>
        <thead><tr><th></th><th class="r">Complete</th><th class="r">Plan says</th>
          <th class="r">CPI</th><th class="r">SPI</th>
          <th class="r">Needs</th><th class="r">Lands at</th><th>Reading</th>
        </tr></thead>
        <tbody>${shown.slice(0, 40).map(p => `
          <tr><td><b>${esc(p.project_code)}</b>
              <div class="note">${esc(p.customer_name || "")} ·
                ${esc(p.product || "")} · ${esc(p.pm_name || "")}</div></td>
            <td class="r num">${pc(p.earned_pct, 0)}</td>
            <td class="r num">${pc(p.planned_pct, 0)}</td>
            <td class="r num ${(p.cpi || 1) < 0.85 ? "delta down" : ""}">${nf(p.cpi, 2)}</td>
            <td class="r num ${(p.spi || 1) < 0.85 ? "delta down" : ""}">${nf(p.spi, 2)}</td>
            <td class="r num">${p.tcpi ? nf(p.tcpi, 2) : "—"}</td>
            <td class="r num">${moneyK(p.eac_cpi)}
              <div class="note">budget ${moneyK(p.bac)}</div></td>
            <td>${esc(p.quadrant)}
              <div class="note">PM has it as ${esc(p.project_health)}</div></td>
          </tr>`).join("")}
        </tbody>
        <caption>"Needs" is the efficiency the remaining work must be delivered
          at to finish on budget. Compared against the cost index achieved so
          far it is the difference between a recovery plan and a hope.</caption>
      </table></div>
      ${shown.length > 40 ? `<p class="note" style="margin-top:8px">Showing 40
        of ${shown.length}.</p>` : ""}
    </div>

    <div class="grid g2">
      <div class="card">
        <h3>The engagements that need a decision</h3>
        <p class="sub">Both indices below tolerance, worst combined index first.
          Either one alone usually has a recoverable explanation; together they
          mean both levers are already spent.</p>
        ${live.filter(p => evmGroup(p) === "both").slice(0, 6).map(p => `
          <div class="flag High">
            <h4 style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap">
              <span>${esc(p.project_code)} · ${esc(p.customer_name || "")}</span>
              <span class="num">${moneyK(p.vac)}</span></h4>
            <p class="detail">${esc(p.statement)}</p>
          </div>`).join("") ||
          `<p class="note">Nothing in that quadrant.</p>`}
      </div>
      <div class="card">
        <h3>Too early to publish an index</h3>
        <p class="sub">Kept and shown rather than dropped. An index is a ratio,
          and a ratio taken on a fortnight of a six-month plan is arithmetic
          rather than information. These engagements are still measured on
          consumption and margin like any other.</p>
        ${held.length ? `<div class="tblwrap"><table>
          <thead><tr><th></th><th class="r">Complete</th>
            <th class="r">Plan says</th><th>Held back because</th></tr></thead>
          <tbody>${held.map(p => `
            <tr><td><b>${esc(p.project_code)}</b>
                <div class="note">${esc(p.customer_name || "")}</div></td>
              <td class="r num">${pc(p.earned_pct, 0)}</td>
              <td class="r num">${pc(p.planned_pct, 0)}</td>
              <td>${esc(p.exclusion_reason || "")}</td></tr>`).join("")}
          </tbody></table></div>`
          : `<p class="note">Every live engagement is far enough along to
             carry an index.</p>`}
      </div>
    </div>`;

  el.querySelectorAll(".chip[data-es]").forEach(b =>
    b.addEventListener("click", () => { evmScope = b.dataset.es; render(); }));
  el.querySelectorAll(".chip[data-eq]").forEach(b =>
    b.addEventListener("click", () => { evmQuad = b.dataset.eq; render(); }));

  const box = document.getElementById("evm-scatter");
  if (box && live.length) {
    scatter(box, live.map(p => ({
      x: p.spi, y: p.cpi, size: p.bac,
      color: QUAD_COLOR[evmGroup(p)],
      label: `${p.project_code} · ${p.customer_name || ""}`,
      tipRows: [
        ["Cost index", nf(p.cpi, 2)],
        ["Schedule index", nf(p.spi, 2)],
        ["Complete", pc(p.earned_pct, 0) + " against a plan of " + pc(p.planned_pct, 0)],
        ["Budget", moneyK(p.bac)],
        ["Reading", p.quadrant],
      ],
    })), {
      height: 320, xRef: 1, yRef: 1,
      xFormat: v => nf(v, 2), yFormat: v => nf(v, 2),
      quadrantLabels: [
        { label: "over cost, ahead", top: false, right: true },
        { label: "over cost, behind", top: false, right: false },
        { label: "under cost, behind", top: true, right: false },
        { label: "under cost, ahead", top: true, right: true },
      ],
    });
    box.insertAdjacentHTML("beforeend",
      `<p class="note" style="margin-top:6px">Horizontal: schedule index.
        Vertical: cost index. Both dividers sit at 1.00.</p>`);
  }
}
