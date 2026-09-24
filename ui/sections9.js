/* =========================================================================
   Automation and AI opportunities.

   The usual version of this screen is a workshop output: things somebody
   thinks could be automated, each with a percentage beside it. It reads well
   and cannot be defended, because the baseline is a guess and the saving is
   two guesses multiplied.

   So this view is laid out as the three parts of the arithmetic, in order and
   labelled: the measured baseline, the rates applied to it, and the modelled
   result. The inputs panel is first rather than buried at the bottom, because
   the rates are the part a reader will want to argue with and hiding them
   reads as hoping they will not.
   ========================================================================= */
"use strict";

let autoArea = "all";
let autoOpen = null;

const AUTO_TYPE_LABEL = {
  rules: "Deterministic rules",
  integration: "Integration",
  ai_assisted: "AI-assisted · person confirms",
  ai_autonomous: "AI-autonomous",
};
const AUTO_TYPE_CLASS = {
  rules: "mute", integration: "mute",
  ai_assisted: "accent", ai_autonomous: "warn",
};
const AUTO_VERDICT_CLASS = {
  "do now": "good", next: "accent", investigate: "warn", no: "bad",
};
const AUTO_AREA_LABEL = {
  migration: "Data migration and intake", delivery: "Delivery execution",
  commercial: "Commercial", support: "Support and hypercare",
  people: "People",
};
const READY_CLASS = { ready: "good", partial: "warn", "not held": "bad" };

function autoUnit(v, unit) {
  if (v === null || v === undefined) return "—";
  if (unit === "currency") return moneyK(v);
  if (unit === "pct") return pc(v, 0);
  if (unit === "hours") return hrs(v);
  if (unit === "days") return nf(v, 0) + " d";
  if (unit === "minutes") return nf(v, 0) + " min";
  return nf(v, v % 1 ? 1 : 0);
}

function renderAutomation(el) {
  const o = org(), d = o.automation;
  if (!d) {
    el.innerHTML = `<div class="card"><h3>No automation analysis</h3></div>`;
    return;
  }
  const orgRow = d.summary.find(s => s.scope === "org") || {};
  const areas = d.summary.filter(s => s.scope === "area");
  const types = d.summary.filter(s => s.scope === "automation_type");
  const shown = autoArea === "all" ? d.opportunities
    : d.opportunities.filter(x => x.area === autoArea);
  const measured = d.inputs.filter(i => i.origin === "measured");
  const assumed = d.inputs.filter(i => i.origin === "assumption");
  const doNow = d.opportunities.filter(x => x.verdict === "do now");
  const ready = d.opportunities.filter(x => x.data_readiness === "ready");
  const rateSens = d.sensitivity.filter(s => s.lever === "automation_pct");

  el.innerHTML = `
    <div class="viewhead">
      <div class="eyebrow">Automation and AI opportunities</div>
      <h1>Where a tool would actually pay</h1>
      <p>Every opportunity below is three separable things: a baseline measured
         from this book, a set of rates that are assumptions and are named as
         such, and the product of the two, labelled as modelled. That
         separation is the point. It means the baseline can be accepted and
         the rate argued with, which is a conversation that converges, unlike
         a single percentage with nothing underneath it.</p>
    </div>

    <div class="grid g4" style="margin-bottom:22px">
      <div class="tile">
        <div class="label">Hours back, per year</div>
        <div class="value">${nf(orgRow.hours_saved)}</div>
        <div class="foot">against ${nf(orgRow.baseline_hours)} measured over
          24 months</div>
      </div>
      <div class="tile stripe good">
        <div class="label">Value, per year</div>
        <div class="value sm">${moneyK((orgRow.cost_saved || 0)
          + (orgRow.value_unlocked || 0))}</div>
        <div class="foot">${moneyK(orgRow.cost_saved)} cost ·
          ${moneyK(orgRow.value_unlocked)} recovered or avoided</div>
      </div>
      <div class="tile">
        <div class="label">Build</div>
        <div class="value sm">${moneyK(orgRow.implementation_cost)}</div>
        <div class="foot">blended payback
          ${orgRow.payback_months ? nf(orgRow.payback_months, 0) + " months"
            : "—"}</div>
      </div>
      <div class="tile stripe ${(orgRow.ai_share_pct || 0) > 50 ? 'warn' : 'good'}">
        <div class="label">Behind an AI step</div>
        <div class="value">${pc(orgRow.ai_share_pct, 0)}</div>
        <div class="foot">of the value · each carries a guardrail</div>
      </div>
    </div>

    <div class="card" style="margin-bottom:18px">
      <h3>The reading</h3>
      <p class="sub">${esc(orgRow.statement || "")}</p>
      <p class="note" style="margin-top:10px">Cost saved and value recovered
        are kept apart on purpose. One is an efficiency that repeats every
        year; the other is cash that was already earned and is collected once.
        Adding them into a single headline is how an automation case gets
        approved and then misses.</p>
    </div>

    <div class="grid g2" style="margin-bottom:18px">
      <div class="card">
        <h3>Inputs · measured from this book</h3>
        <p class="sub">Nothing here is a choice. Each figure is a query result
          and moves when the data does.</p>
        <div class="tblwrap"><table>
          <thead><tr><th></th><th class="r">Value</th><th>From</th></tr></thead>
          <tbody>${measured.map(i => `
            <tr><td>${esc(i.input_label)}</td>
              <td class="r num">${autoUnit(i.value, i.unit)}</td>
              <td class="note">${esc(i.basis)}</td></tr>`).join("")}
          </tbody></table></div>
      </div>
      <div class="card">
        <h3>Inputs · assumptions</h3>
        <p class="sub">These are choices, and they are somebody's to defend.
          Each carries the range the sensitivity below runs over.</p>
        <div class="tblwrap"><table>
          <thead><tr><th></th><th class="r">Value</th><th class="r">Range</th>
            <th>Why that number</th></tr></thead>
          <tbody>${assumed.map(i => `
            <tr><td>${esc(i.input_label)}</td>
              <td class="r num">${autoUnit(i.value, i.unit)}</td>
              <td class="r num note">${i.low === null ? "—"
                : `${autoUnit(i.low, i.unit)} – ${autoUnit(i.high, i.unit)}`}</td>
              <td class="note">${esc(i.basis)}</td></tr>`).join("")}
          </tbody>
          <caption>An assumption with no range is a number nobody has tested.
            Every rate that moves the answer has one.</caption>
        </table></div>
      </div>
    </div>

    <div class="card" style="margin-bottom:18px">
      <h3>The opportunities</h3>
      <p class="sub">Ranked on first-year net, then payback, then feasibility.
        Ranking on the saving alone puts a two-year build at the top of a list
        somebody is meant to act on this quarter. Select a row for the measured
        evidence underneath it.</p>
      <div class="chips" style="margin-bottom:12px">
        <button class="chip" data-aa="all"
          aria-pressed="${autoArea === 'all'}">All ${d.opportunities.length}</button>
        ${areas.map(a => `<button class="chip" data-aa="${a.scope_key}"
          aria-pressed="${autoArea === a.scope_key}">${esc(a.scope_label)} ·
          ${a.opportunities}</button>`).join("")}
      </div>
      <div class="tblwrap"><table>
        <thead><tr><th>Process</th><th>Kind</th>
          <th class="r">Baseline, per year</th><th class="r">Rates</th>
          <th class="r">Hours back</th><th class="r">Value</th>
          <th class="r">Build</th><th class="r">Payback</th>
          <th>Verdict</th></tr></thead>
        <tbody>${shown.map(x => `
          <tr class="clickable" data-opp="${x.opportunity_id}"
              aria-selected="${autoOpen === x.opportunity_id}">
            <td><b>${esc(x.process)}</b>
              <div class="note">${esc(x.opp_code)} ·
                ${esc(AUTO_AREA_LABEL[x.area] || x.area)}</div></td>
            <td class="tight"><span class="pill ${AUTO_TYPE_CLASS[x.automation_type]}">
              ${esc(AUTO_TYPE_LABEL[x.automation_type])}</span></td>
            <td class="r num">${x.annual_baseline_hours
              ? hrs(x.annual_baseline_hours) : "—"}
              ${x.baseline_count ? `<div class="note">${nf(x.baseline_count)}
                ${esc(x.baseline_unit || "")}</div>` : ""}</td>
            <td class="r num note">${nf(x.addressable_pct, 0)} ·
              ${nf(x.automation_pct, 0)} · ${nf(x.review_pct, 0)}</td>
            <td class="r num">${x.hours_saved > 1 ? hrs(x.hours_saved) : "—"}</td>
            <td class="r num">${moneyK((x.cost_saved || 0)
              + (x.value_unlocked || 0))}</td>
            <td class="r num">${moneyK(x.implementation_cost)}</td>
            <td class="r num">${x.payback_months
              ? nf(x.payback_months, 0) + " mo" : "—"}</td>
            <td class="tight"><span class="pill ${AUTO_VERDICT_CLASS[x.verdict]}">
              ${esc(x.verdict)}</span></td></tr>
          ${autoOpen === x.opportunity_id ? autoDetail(x) : ""}`).join("")}
        </tbody>
        <caption>Rates are addressable · automated · reviewed, in percent. The
          hours back figure is already net of the review time, because an
          AI opportunity that ignores its own review burden overstates itself
          by exactly that share.</caption>
      </table></div>
    </div>

    <div class="grid g2" style="margin-bottom:18px">
      <div class="card">
        <h3>By kind of automation</h3>
        <p class="sub">Not one word. A scheduled rule is a procurement
          decision; an AI step with a person confirming is a governance one,
          and they do not go to the same meeting.</p>
        <div class="tblwrap"><table>
          <thead><tr><th></th><th class="r">Count</th><th class="r">Hours</th>
            <th class="r">Value</th><th class="r">Build</th></tr></thead>
          <tbody>${types.map(t => `
            <tr><td>${esc(t.scope_label)}</td>
              <td class="r num">${t.opportunities}</td>
              <td class="r num">${t.hours_saved > 1 ? hrs(t.hours_saved) : "—"}</td>
              <td class="r num">${moneyK((t.cost_saved || 0)
                + (t.value_unlocked || 0))}</td>
              <td class="r num">${moneyK(t.implementation_cost)}</td></tr>`).join("")}
          </tbody></table></div>
        <p class="note" style="margin-top:10px">${doNow.length} of
          ${d.opportunities.length} are ready to start now, and
          ${ready.length} need no data this system does not already hold.</p>
      </div>
      <div class="card">
        <h3>If the rates are wrong</h3>
        <p class="sub">The automation rate is the assumption the whole model
          rests on, so it is tested rather than asserted. Only the two rates
          that move the answer are shown: running every input produces a table
          nobody reads and hides which two decisions matter.</p>
        <div class="tblwrap"><table>
          <thead><tr><th>Shift</th><th class="r">Hours back</th>
            <th class="r">First-year net</th><th class="r">vs base</th></tr></thead>
          <tbody>${rateSens.map(s => `
            <tr><td>${esc(s.shift_label)}</td>
              <td class="r num">${hrs(s.hours_saved)}</td>
              <td class="r num">${moneyK(s.net_year_one)}</td>
              <td class="r num ${s.delta_vs_base < 0 ? "delta down" : "delta up"}">
                ${s.delta_vs_base > 0 ? "+" : ""}${moneyK(s.delta_vs_base)}</td>
            </tr>`).join("")}
          </tbody>
          <caption>Fifteen points off every rate is a deliberately harsh test.
            The reading that matters is whether the programme still pays back
            inside a year at the bottom of that range.</caption>
        </table></div>
        ${d.sensitivity.filter(s => s.lever === "billable_recovery_pct").length
          ? `<p class="note" style="margin-top:10px">
            On conversion of freed hours to billable work:
            ${d.sensitivity.filter(s => s.lever === "billable_recovery_pct")
              .map(s => `${nf(s.shift, 0)}% → ${moneyK(s.net_year_one)}`)
              .join(" · ")}. The hours do not change, only what they are
            worth.</p>` : ""}
      </div>
    </div>

    <div class="card">
      <h3>What is not on this list</h3>
      <p class="sub">Proposal writing, pre-sales qualification and recruitment
        screening are real opportunities in a services business and none of
        them is here. This database holds no hours against them, and an
        opportunity with an invented baseline is worse than a missing one: it
        is the row that gets quoted in a business case and then cannot be
        traced.</p>
      <p class="note">Nothing on this list models AI writing to financial or
        project data. Every AI-typed opportunity names what stays with a
        person, and its saving is taken net of the time that review costs.</p>
    </div>`;

  el.querySelectorAll(".chip[data-aa]").forEach(b =>
    b.addEventListener("click", () => { autoArea = b.dataset.aa; render(); }));
  el.querySelectorAll("tr[data-opp]").forEach(r =>
    r.addEventListener("click", () => {
      const id = Number(r.dataset.opp);
      autoOpen = autoOpen === id ? null : id;
      render();
    }));
}

/* The evidence row: the measured figures the modelled saving came from, and
   the two things a reader needs in order to disagree with it. */
function autoDetail(x) {
  return `
    <tr><td colspan="9" style="background:var(--surface-2)">
      <div class="grid g2" style="padding:6px 0 10px">
        <div>
          <h4 style="font-family:var(--font-ui);font-size:13px;margin-bottom:6px">
            Measured evidence</h4>
          <div class="tblwrap"><table>
            <tbody>${(x.evidence || []).map(e => `
              <tr><td style="width:44%">${esc(e.measure)}
                  ${e.source_view ? `<div class="note">see
                    ${esc(e.source_view)}</div>` : ""}</td>
                <td class="r num" style="width:16%">
                  ${autoUnit(e.value, e.unit)}</td>
                <td class="note">${esc(e.detail)}</td></tr>`).join("")}
            </tbody></table></div>
        </div>
        <div style="display:flex;flex-direction:column;gap:10px">
          <div>
            <h4 style="font-family:var(--font-ui);font-size:13px;margin-bottom:6px">
              How the baseline was measured</h4>
            <p class="note">${esc(x.baseline_basis)}</p>
          </div>
          ${x.guardrail ? `<div class="action">
            <b>Guardrail</b>${esc(x.guardrail)}</div>` : ""}
          <div class="row" style="gap:8px">
            <span class="pill ${READY_CLASS[x.data_readiness]}">data
              ${esc(x.data_readiness)}</span>
            <span class="pill mute">feasibility ${esc(x.feasibility)}</span>
            <span class="pill mute">confidence ${esc(x.confidence)}</span>
          </div>
          <p style="margin:0">${esc(x.statement)}</p>
          ${x.prerequisite ? `<p class="note"><b>Prerequisite.</b>
            ${esc(x.prerequisite)}</p>` : ""}
        </div>
      </div>
    </td></tr>`;
}
