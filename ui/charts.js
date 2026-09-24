/* =========================================================================
   Charts. Hand-rolled SVG: no library, no external requests, and the marks
   are specified rather than inherited from a default theme.

   Shared conventions:
     - 2px lines, 4px rounded data-ends anchored to the baseline
     - a 2px surface gap between adjacent fills so bars read as separate marks
     - recessive grid, one axis per chart, never two y-scales
     - hover on every plotted mark; values labelled selectively, not on
       every point
   ========================================================================= */
"use strict";

const SERIES = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)"];
const PAD = { t: 14, r: 14, b: 26, l: 52 };

function svgEl(name, attrs = {}) {
  const e = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [k, v] of Object.entries(attrs)) {
    if (v !== null && v !== undefined) e.setAttribute(k, v);
  }
  return e;
}

function niceTicks(min, max, count = 4) {
  if (min === max) { max = min + 1; }
  const span = max - min;
  const raw = span / count;
  const mag = Math.pow(10, Math.floor(Math.log10(Math.abs(raw) || 1)));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw) || mag * 10;
  const lo = Math.floor(min / step) * step;
  const hi = Math.ceil(max / step) * step;
  const out = [];
  for (let v = lo; v <= hi + step / 1e6; v += step) out.push(+v.toFixed(10));
  return out;
}

function mount(host, w, h) {
  host.innerHTML = "";
  const svg = svgEl("svg", {
    class: "chart", viewBox: `0 0 ${w} ${h}`,
    preserveAspectRatio: "none", role: "img", style: `height:${h}px`
  });
  host.appendChild(svg);
  return svg;
}

function axes(svg, ticks, plot, fmt, opts = {}) {
  const g = svgEl("g");
  ticks.forEach(t => {
    const y = plot.y(t);
    g.appendChild(svgEl("line", {
      class: t === 0 && opts.zeroLine ? "axis-line" : "grid-line",
      x1: PAD.l, x2: plot.w - PAD.r, y1: y, y2: y
    }));
    const lab = svgEl("text", { x: PAD.l - 8, y: y + 3.5, "text-anchor": "end" });
    lab.textContent = fmt(t);
    g.appendChild(lab);
  });
  svg.appendChild(g);
}

/* ---------- vertical bars, optionally with a line overlay of the same unit */
function vBars(host, rows, opts = {}) {
  const h = opts.height || 190, w = 800;
  const svg = mount(host, w, h);
  const vals = rows.map(r => r.value).filter(v => v !== null && v !== undefined);
  const ticks = niceTicks(Math.min(0, ...vals), Math.max(0, ...vals), opts.tickCount || 4);
  const y0 = ticks[0], y1 = ticks[ticks.length - 1];
  const plotH = h - PAD.t - PAD.b;
  const plot = {
    w, h,
    y: v => PAD.t + plotH - (v - y0) / (y1 - y0) * plotH
  };
  axes(svg, ticks, plot, opts.tickFormat || (v => nf(v)), { zeroLine: y0 < 0 });

  const inner = w - PAD.l - PAD.r;
  const step = inner / rows.length;
  const bw = Math.max(2, step - Math.max(2, step * 0.22));
  const base = plot.y(Math.max(y0, 0));
  const g = svgEl("g");
  rows.forEach((r, i) => {
    if (r.value === null || r.value === undefined) return;
    const x = PAD.l + i * step + (step - bw) / 2;
    const yv = plot.y(r.value);
    const top = Math.min(yv, base), height = Math.max(1.5, Math.abs(base - yv));
    const rect = svgEl("rect", {
      x, y: top, width: bw, height, rx: Math.min(4, bw / 2),
      fill: r.color || opts.color || SERIES[0]
    });
    rect.addEventListener("pointerenter", ev => showTip(
      `<strong>${esc(r.tipTitle || r.label)}</strong>` +
      (r.tipRows || [[opts.valueLabel || "Value", (opts.tipFormat || opts.tickFormat || nf)(r.value)]])
        .map(([k, v]) => `<div class="tr"><span>${esc(k)}</span><span>${v}</span></div>`).join(""),
      ev));
    rect.addEventListener("pointerleave", hideTip);
    g.appendChild(rect);
    if (opts.labelEvery && i % opts.labelEvery === 0) {
      const t = svgEl("text", { x: x + bw / 2, y: h - PAD.b + 14, "text-anchor": "middle" });
      t.textContent = r.label;
      g.appendChild(t);
    }
  });
  svg.appendChild(g);
  return svg;
}

/* ---------- line chart with optional reference line ------------------- */
function lineChart(host, rows, opts = {}) {
  const h = opts.height || 190, w = 800;
  const svg = mount(host, w, h);
  const vals = rows.map(r => r.value).filter(v => Number.isFinite(v));
  const extra = Number.isFinite(opts.ref) ? [opts.ref] : [];
  const ticks = niceTicks(
    opts.zeroBase ? 0 : Math.min(...vals, ...extra),
    Math.max(...vals, ...extra), opts.tickCount || 4);
  const y0 = ticks[0], y1 = ticks[ticks.length - 1];
  const plotH = h - PAD.t - PAD.b;
  const plot = { w, h, y: v => PAD.t + plotH - (v - y0) / (y1 - y0) * plotH };
  axes(svg, ticks, plot, opts.tickFormat || (v => nf(v)));

  const inner = w - PAD.l - PAD.r;
  const step = rows.length > 1 ? inner / (rows.length - 1) : 0;
  const X = i => PAD.l + i * step;

  if (Number.isFinite(opts.ref)) {
    svg.appendChild(svgEl("line", {
      class: "ref-line", x1: PAD.l, x2: w - PAD.r, y1: plot.y(opts.ref), y2: plot.y(opts.ref)
    }));
    const rl = svgEl("text", { x: w - PAD.r, y: plot.y(opts.ref) - 6, "text-anchor": "end" });
    rl.textContent = opts.refLabel || "";
    svg.appendChild(rl);
  }

  const pts = rows.map((r, i) => Number.isFinite(r.value) ? [X(i), plot.y(r.value)] : null)
    .filter(Boolean);
  const color = opts.color || SERIES[0];
  if (opts.area) {
    svg.appendChild(svgEl("path", {
      d: `M${pts[0][0]} ${plot.y(Math.max(y0, 0))} ` +
         pts.map(p => `L${p[0]} ${p[1]}`).join(" ") +
         ` L${pts[pts.length - 1][0]} ${plot.y(Math.max(y0, 0))} Z`,
      fill: color, opacity: .12
    }));
  }
  svg.appendChild(svgEl("path", {
    d: "M" + pts.map(p => p.join(" ")).join(" L"),
    fill: "none", stroke: color, "stroke-width": 2,
    "stroke-linejoin": "round", "stroke-linecap": "round"
  }));
  // emphasised endpoint
  const last = pts[pts.length - 1];
  svg.appendChild(svgEl("circle", {
    cx: last[0], cy: last[1], r: 4.5, fill: color,
    stroke: "var(--surface)", "stroke-width": 2
  }));

  // hover targets
  const g = svgEl("g");
  rows.forEach((r, i) => {
    if (!Number.isFinite(r.value)) return;
    const hit = svgEl("rect", {
      x: X(i) - step / 2, y: PAD.t, width: Math.max(step, 8), height: plotH,
      fill: "transparent"
    });
    hit.addEventListener("pointerenter", ev => showTip(
      `<strong>${esc(r.tipTitle || r.label)}</strong>` +
      (r.tipRows || [[opts.valueLabel || "Value", (opts.tipFormat || opts.tickFormat || nf)(r.value)]])
        .map(([k, v]) => `<div class="tr"><span>${esc(k)}</span><span>${v}</span></div>`).join(""),
      ev));
    hit.addEventListener("pointerleave", hideTip);
    g.appendChild(hit);
  });
  svg.appendChild(g);

  rows.forEach((r, i) => {
    if (opts.labelEvery && i % opts.labelEvery === 0) {
      const t = svgEl("text", { x: X(i), y: h - PAD.b + 14, "text-anchor": "middle" });
      t.textContent = r.label;
      svg.appendChild(t);
    }
  });
  return svg;
}

/* ---------- grouped vertical bars ------------------------------------- */
function groupedBars(host, rows, seriesDefs, opts = {}) {
  const h = opts.height || 220, w = 800;
  const svg = mount(host, w, h);
  const all = rows.flatMap(r => r.values).filter(Number.isFinite);
  const ticks = niceTicks(Math.min(0, ...all), Math.max(0, ...all), opts.tickCount || 4);
  const y0 = ticks[0], y1 = ticks[ticks.length - 1];
  const plotH = h - PAD.t - PAD.b;
  const plot = { w, h, y: v => PAD.t + plotH - (v - y0) / (y1 - y0) * plotH };
  axes(svg, ticks, plot, opts.tickFormat || (v => nf(v)), { zeroLine: y0 < 0 });

  const inner = w - PAD.l - PAD.r;
  const step = inner / rows.length;
  const gap = 2;
  const groupW = step * 0.74;
  const bw = Math.max(2, (groupW - gap * (seriesDefs.length - 1)) / seriesDefs.length);
  const base = plot.y(Math.max(y0, 0));
  rows.forEach((r, i) => {
    const gx = PAD.l + i * step + (step - groupW) / 2;
    seriesDefs.forEach((s, j) => {
      const v = r.values[j];
      if (!Number.isFinite(v)) return;
      const x = gx + j * (bw + gap);
      const yv = plot.y(v);
      const rect = svgEl("rect", {
        x, y: Math.min(yv, base), width: bw, height: Math.max(1.5, Math.abs(base - yv)),
        rx: Math.min(4, bw / 2), fill: s.color
      });
      rect.addEventListener("pointerenter", ev => showTip(
        `<strong>${esc(r.label)}</strong>` + seriesDefs.map((ss, k) =>
          `<div class="tr"><span>${esc(ss.label)}</span><span>${(opts.tipFormat || nf)(r.values[k])}</span></div>`).join(""),
        ev));
      rect.addEventListener("pointerleave", hideTip);
      svg.appendChild(rect);
    });
    if (!opts.labelEvery || i % opts.labelEvery === 0) {
      const t = svgEl("text", { x: PAD.l + i * step + step / 2, y: h - PAD.b + 14, "text-anchor": "middle" });
      t.textContent = r.label;
      svg.appendChild(t);
    }
  });
  return svg;
}

/* ---------- horizontal bars, diverging around zero when needed -------- */
function hBars(host, rows, opts = {}) {
  const rowH = opts.rowH || 24;
  const w = 800, labelW = opts.labelW || 250;
  const h = rows.length * rowH + 26;
  const svg = mount(host, w, h);
  const vals = rows.map(r => r.value).filter(Number.isFinite);
  const min = Math.min(0, ...vals), max = Math.max(0, ...vals);
  const x0 = labelW, x1 = w - 68;
  const zero = x0 + (0 - min) / ((max - min) || 1) * (x1 - x0);
  const X = v => x0 + (v - min) / ((max - min) || 1) * (x1 - x0);

  if (min < 0) {
    svg.appendChild(svgEl("line", { class: "axis-line", x1: zero, x2: zero, y1: 2, y2: h - 24 }));
  }
  rows.forEach((r, i) => {
    const y = i * rowH + 3;
    const lab = svgEl("text", { x: labelW - 10, y: y + rowH / 2 - 1, "text-anchor": "end" });
    lab.textContent = r.label.length > 42 ? r.label.slice(0, 41) + "…" : r.label;
    if (r.emphasis) lab.setAttribute("class", "val");
    svg.appendChild(lab);
    if (!Number.isFinite(r.value)) return;
    const bx = r.value >= 0 ? zero : X(r.value);
    const bwid = Math.max(1.5, Math.abs(X(r.value) - zero));
    const bar = svgEl("rect", {
      x: bx, y: y + 3, width: bwid, height: rowH - 10, rx: 4,
      fill: r.color || opts.color || SERIES[0]
    });
    bar.addEventListener("pointerenter", ev => showTip(
      `<strong>${esc(r.label)}</strong>` +
      (r.tipRows || [[opts.valueLabel || "Value", (opts.tipFormat || nf)(r.value)]])
        .map(([k, v]) => `<div class="tr"><span>${esc(k)}</span><span>${v}</span></div>`).join(""),
      ev));
    bar.addEventListener("pointerleave", hideTip);
    svg.appendChild(bar);
    const vt = svgEl("text", {
      x: w - 62, y: y + rowH / 2 - 1, class: "val", "text-anchor": "start"
    });
    vt.textContent = (opts.valueFormat || opts.tipFormat || nf)(r.value);
    svg.appendChild(vt);
  });
  return svg;
}

/* ---------- probability bars, 0-100 ---------------------------------- */
function probBars(host, rows) {
  const rowH = 34, w = 800, labelW = 300;
  const h = rows.length * rowH + 6;
  const svg = mount(host, w, h);
  const x0 = labelW, x1 = w - 70;
  rows.forEach((r, i) => {
    const y = i * rowH + 4;
    const lab = svgEl("text", { x: 0, y: y + 15, "text-anchor": "start" });
    lab.textContent = r.label;
    svg.appendChild(lab);
    svg.appendChild(svgEl("rect", {
      x: x0, y: y + 6, width: x1 - x0, height: 12, rx: 6, fill: "var(--surface-sunk)"
    }));
    const wid = Math.max(2, (r.value / 100) * (x1 - x0));
    const bar = svgEl("rect", { x: x0, y: y + 6, width: wid, height: 12, rx: 6, fill: r.color });
    if (Number.isFinite(r.base)) {
      // the organisation's own historical rate for this outcome: without it a
      // "60%" means nothing
      const bx = x0 + (r.base / 100) * (x1 - x0);
      svg.appendChild(svgEl("line", {
        x1: bx, x2: bx, y1: y + 2, y2: y + 22, class: "ref-line"
      }));
    }
    bar.addEventListener("pointerenter", ev => showTip(
      `<strong>${esc(r.label)}</strong><div class="tr"><span>Probability</span><span>${pc(r.value)}</span></div>` +
      (Number.isFinite(r.base) ? `<div class="tr"><span>Historical rate</span><span>${pc(r.base)}</span></div>` : "") +
      (r.note ? `<div style="margin-top:4px;color:var(--ink-2)">${esc(r.note)}</div>` : ""), ev));
    bar.addEventListener("pointerleave", hideTip);
    svg.appendChild(bar);
    const vt = svgEl("text", { x: x1 + 8, y: y + 16, class: "val" });
    vt.textContent = pc(r.value, 0);
    svg.appendChild(vt);
  });
  return svg;
}

/* ---------- margin waterfall: forecast -> predicted ------------------- */
function waterfall(host, start, steps, endLabel, opts = {}) {
  const rowH = 30, w = 800, labelW = 330;
  const rows = [{ label: opts.startLabel || "Forecast margin", value: start, kind: "total" }]
    .concat(steps.map(s => ({ ...s, kind: "step" })))
    .concat([{ label: endLabel, value: start + steps.reduce((a, s) => a + s.value, 0), kind: "total" }]);
  const h = rows.length * rowH + 8;
  const svg = mount(host, w, h);

  let running = 0;
  const values = [start];
  steps.forEach(s => { running += s.value; values.push(start + running); });
  const allEdges = [0, start, ...values];
  const lo = Math.min(...allEdges), hi = Math.max(...allEdges);
  const x0 = labelW, x1 = w - 80;
  const X = v => x0 + (v - lo) / ((hi - lo) || 1) * (x1 - x0);

  running = start;
  rows.forEach((r, i) => {
    const y = i * rowH + 4;
    const lab = svgEl("text", { x: labelW - 12, y: y + rowH / 2, "text-anchor": "end" });
    lab.textContent = r.label.length > 52 ? r.label.slice(0, 51) + "…" : r.label;
    if (r.kind === "total") lab.setAttribute("class", "val");
    svg.appendChild(lab);

    let from, to, fill;
    if (r.kind === "total") {
      from = Math.min(0, r.value); to = Math.max(0, r.value);
      fill = "var(--ink-3)";
    } else {
      from = Math.min(running, running + r.value);
      to = Math.max(running, running + r.value);
      fill = r.value < 0 ? "var(--bad)" : "var(--good)";
      running += r.value;
    }
    const bar = svgEl("rect", {
      x: X(from), y: y + 6, width: Math.max(1.5, X(to) - X(from)), height: rowH - 14, rx: 4, fill
    });
    bar.addEventListener("pointerenter", ev => showTip(
      `<strong>${esc(r.label)}</strong>` +
      `<div class="tr"><span>${r.kind === "total" ? "Margin" : "Margin impact"}</span>` +
      `<span>${r.kind === "total" ? pc(r.value) : (r.value >= 0 ? "+" : "") + nf(r.value, 2) + " pts"}</span></div>`, ev));
    bar.addEventListener("pointerleave", hideTip);
    svg.appendChild(bar);

    const vt = svgEl("text", { x: w - 74, y: y + rowH / 2, class: "val" });
    vt.textContent = r.kind === "total" ? pc(r.value) :
      (r.value >= 0 ? "+" : "") + nf(r.value, 1);
    svg.appendChild(vt);
  });
  if (lo < 0 && hi > 0) {
    svg.appendChild(svgEl("line", { class: "axis-line", x1: X(0), x2: X(0), y1: 2, y2: h - 6 }));
  }
  return svg;
}

/* ---------- sparkline for table cells --------------------------------- */
function spark(values, w = 78, h = 20, color = "var(--s1)") {
  const v = values.filter(Number.isFinite);
  if (v.length < 2) return "";
  const lo = Math.min(...v), hi = Math.max(...v);
  const pts = v.map((x, i) => [
    (i / (v.length - 1)) * (w - 2) + 1,
    h - 2 - ((x - lo) / ((hi - lo) || 1)) * (h - 4)
  ]);
  return `<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" aria-hidden="true">` +
    `<path d="M${pts.map(p => p.join(" ")).join(" L")}" fill="none" stroke="${color}" stroke-width="1.6" stroke-linejoin="round"/>` +
    `<circle cx="${pts[pts.length - 1][0]}" cy="${pts[pts.length - 1][1]}" r="2.2" fill="${color}"/></svg>`;
}

function legend(items) {
  return `<div class="legend">` + items.map(i =>
    `<span><i style="background:${i.color}"></i>${esc(i.label)}</span>`).join("") + `</div>`;
}

/* ---------- scatter with quadrant dividers ----------------------------
   Written for one job: the cost index against the schedule index, where the
   reading is the quadrant a point sits in and not either coordinate on its
   own. Two reference lines at 1.00 do most of the work; the axes are there to
   say how far outside the point is.

   Radius carries budget, because a 40-hour engagement and a 4,000-hour one
   sitting at the same coordinates are not the same finding, and colour
   carries the quadrant so the four groups stay legible without a legend.
   ---------------------------------------------------------------------- */
function scatter(host, points, opts = {}) {
  const h = opts.height || 300, w = 800;
  const svg = mount(host, w, h);
  // Every other chart here stretches its viewBox to the container, which is
  // fine for bars and wrong for a scatter: a stretched circle reads as an
  // ellipse and the eye starts inferring a direction that is not in the data.
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  svg.style.height = "auto";
  const xs = points.map(p => p.x).filter(Number.isFinite);
  const ys = points.map(p => p.y).filter(Number.isFinite);
  if (!xs.length) { host.innerHTML = ""; return svg; }

  const xRef = Number.isFinite(opts.xRef) ? opts.xRef : 1;
  const yRef = Number.isFinite(opts.yRef) ? opts.yRef : 1;
  const xt = niceTicks(Math.min(...xs, xRef * 0.9), Math.max(...xs, xRef * 1.1), 4);
  const yt = niceTicks(Math.min(...ys, yRef * 0.9), Math.max(...ys, yRef * 1.1), 4);
  const x0 = xt[0], x1 = xt[xt.length - 1];
  const y0 = yt[0], y1 = yt[yt.length - 1];
  const plotH = h - PAD.t - PAD.b, plotW = w - PAD.l - PAD.r;
  const X = v => PAD.l + (v - x0) / (x1 - x0) * plotW;
  const Y = v => PAD.t + plotH - (v - y0) / (y1 - y0) * plotH;

  // Grid and value axis, then the two dividers on top of it, because the
  // dividers are the chart and the grid is scaffolding.
  const g = svgEl("g");
  yt.forEach(t => {
    g.appendChild(svgEl("line", { class: "grid-line", x1: PAD.l,
      x2: w - PAD.r, y1: Y(t), y2: Y(t) }));
    const lab = svgEl("text", { x: PAD.l - 8, y: Y(t) + 3.5, "text-anchor": "end" });
    lab.textContent = (opts.yFormat || nf)(t);
    g.appendChild(lab);
  });
  xt.forEach(t => {
    const lab = svgEl("text", { x: X(t), y: h - PAD.b + 14, "text-anchor": "middle" });
    lab.textContent = (opts.xFormat || nf)(t);
    g.appendChild(lab);
  });
  g.appendChild(svgEl("line", { class: "axis-line", x1: X(xRef), x2: X(xRef),
    y1: PAD.t, y2: PAD.t + plotH }));
  g.appendChild(svgEl("line", { class: "axis-line", x1: PAD.l, x2: w - PAD.r,
    y1: Y(yRef), y2: Y(yRef) }));

  (opts.quadrantLabels || []).forEach(q => {
    const t = svgEl("text", {
      x: q.right ? w - PAD.r - 6 : PAD.l + 6,
      y: q.top ? PAD.t + 13 : PAD.t + plotH - 6,
      "text-anchor": q.right ? "end" : "start", class: "quadlabel"
    });
    t.textContent = q.label;
    g.appendChild(t);
  });

  const sizes = points.map(p => p.size || 1);
  const smax = Math.max(...sizes);
  points.forEach(p => {
    if (!Number.isFinite(p.x) || !Number.isFinite(p.y)) return;
    const r = 3.5 + Math.sqrt((p.size || 1) / smax) * 7.5;
    const c = svgEl("circle", {
      cx: X(Math.max(x0, Math.min(x1, p.x))),
      cy: Y(Math.max(y0, Math.min(y1, p.y))),
      r, fill: p.color || SERIES[0], "fill-opacity": 0.72,
      stroke: p.color || SERIES[0], "stroke-width": 1.2
    });
    c.addEventListener("pointerenter", ev => showTip(
      `<strong>${esc(p.label)}</strong>` +
      (p.tipRows || []).map(([k, v]) =>
        `<div class="tr"><span>${esc(k)}</span><span>${v}</span></div>`).join(""),
      ev));
    c.addEventListener("pointerleave", hideTip);
    g.appendChild(c);
  });
  svg.appendChild(g);
  return svg;
}
