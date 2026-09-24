/* =========================================================================
   Wiring: nav, org switch, and the render dispatch.
   ========================================================================= */
"use strict";

const RENDERERS = {
  psos: renderPsos, brief: renderBrief, projects: renderProjects, margin: renderMargin,
  capacity: renderCapacity, whatif: renderWhatif, gonogo: renderGonogo,
  acq: renderAcq, intake: renderIntake, how: renderHow,
  checks: renderChecks, resource: renderResourcing, dilig: renderDiligence,
  billing: renderBilling, models: renderModels, evm: renderEvm,
  auto: renderAutomation, changes: renderChanges,
};

function buildChrome() {
  document.getElementById("nav").innerHTML = SECTIONS.map(s =>
    `<button class="navbtn" data-s="${s.id}" aria-current="${s.id === SECTION}">
       <span class="req">${s.req}</span><span>${s.label}</span></button>`).join("");
  document.querySelectorAll("#nav .navbtn").forEach(b =>
    b.addEventListener("click", () => { SECTION = b.dataset.s; render(); window.scrollTo(0, 0); }));

  // One target is named, not selected: the operating company is out of the
  // payload entirely and a switcher with one entry is a control that does
  // nothing. More than one only happens after somebody loads a second target
  // with load_target.py, and then the switcher is the only way to reach it.
  const o = org();
  const subject = document.getElementById("subject");
  if (ORGS.length > 1) {
    subject.innerHTML =
      `<select id="orgpick" aria-label="Acquisition target">
         ${ORGS.map(k => `<option value="${k}" ${k === ORG ? "selected" : ""}>
            ${esc(DATA.orgs[k].org.org_name)}</option>`).join("")}
       </select>
       <span class="role">Acquisition target · ${ORGS.length} loaded</span>`;
    document.getElementById("orgpick").addEventListener("change", ev => {
      ORG = ev.target.value;
      buildChrome();
      render();
      window.scrollTo(0, 0);
    });
  } else {
    subject.innerHTML =
      `<span class="name">${esc(o.org.org_name)}</span>
       <span class="role">Acquisition target</span>`;
  }

  document.getElementById("views").innerHTML = SECTIONS.map(s =>
    `<section class="view" id="view-${s.id}" hidden></section>`).join("");
}

function updateTopbar() {
  const o = org();
  document.getElementById("m-asof").textContent =
    new Date(DATA.as_of + "T00:00:00").toLocaleDateString("en-CA",
      { day: "numeric", month: "short", year: "numeric" });
  document.getElementById("m-run").textContent =
    `${nf(o.run.projects_scored)} projects · ${nf(o.run.runtime_ms)} ms`;
  document.getElementById("m-records").textContent =
    `${nf(o.counts.time_entry)} time entries · ${nf(o.counts.project)} projects`;
  document.getElementById("demo-text").textContent =
    `Every figure is generated for ${o.org.org_name}, the acquisition target, to ` +
    `exercise the engine. Benchmark figures come from the acquirer's own book on the ` +
    `same definitions. The data is internally consistent and deliberately contains ` +
    `the problems the requirements describe, but it is not real customer, financial ` +
    `or acquisition data and nothing here should be quoted.`;
  document.querySelectorAll("#nav .navbtn").forEach(b =>
    b.setAttribute("aria-current", b.dataset.s === SECTION));
}

function render() {
  updateTopbar();
  buildExchangeBar();
  SECTIONS.forEach(s => {
    const el = document.getElementById("view-" + s.id);
    if (s.id === SECTION) {
      el.hidden = false;
      try {
        RENDERERS[s.id](el);
      } catch (err) {
        el.innerHTML = `<div class="card"><h3>This view could not be rendered</h3>
          <p class="note">${esc(err && err.message ? err.message : String(err))}</p></div>`;
        console.error(s.id, err);
      }
    } else {
      el.hidden = true;
      el.innerHTML = "";
    }
  });
  hideTip();
}

buildChrome();
render();
