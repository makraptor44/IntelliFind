/* IntelliFind procurement simulation — dashboard client.
 * Dependency-free: polls /api/state and renders live tables + custom canvas charts. */
"use strict";

// ----------------------------------------------------------------------------
// palettes
// ----------------------------------------------------------------------------
const PRODUCT_COLORS = ["#4f9dff", "#6ee7c7", "#fbbf24", "#a78bfa", "#f472b6", "#38bdf8"];
const SUPPLIER_COLORS = { supplier_A: "#4f9dff", supplier_B: "#f87171", supplier_C: "#34d399" };
const SHOP_COLORS = ["#4f9dff", "#6ee7c7", "#fbbf24", "#a78bfa", "#f472b6"];
const FALLBACK = ["#4f9dff", "#f87171", "#34d399", "#fbbf24", "#a78bfa", "#6ee7c7", "#f472b6"];

function productColor(i) { return PRODUCT_COLORS[i % PRODUCT_COLORS.length]; }
function supplierColor(id, i) { return SUPPLIER_COLORS[id] || FALLBACK[i % FALLBACK.length]; }

// ----------------------------------------------------------------------------
// formatting
// ----------------------------------------------------------------------------
const fmtInt = (n) => (n == null ? "—" : Math.round(n).toLocaleString());
function money(n) {
  if (n == null) return "—";
  const a = Math.abs(n), s = n < 0 ? "-" : "";
  if (a >= 1e6) return `${s}£${(a / 1e6).toFixed(2)}M`;
  if (a >= 1e3) return `${s}£${(a / 1e3).toFixed(1)}k`;
  return `${s}£${a.toFixed(0)}`;
}
const money2 = (n) => (n == null ? "—" : `£${Number(n).toLocaleString(undefined, { maximumFractionDigits: 2 })}`);
const pct = (n) => (n == null ? "—" : `${(n * 100).toFixed(1)}%`);
const f2 = (n) => (n == null ? "—" : Number(n).toFixed(2));
const f3 = (n) => (n == null ? "—" : Number(n).toFixed(3));

// ----------------------------------------------------------------------------
// canvas chart engine
// ----------------------------------------------------------------------------
function prepCanvas(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || 600;
  const h = canvas.clientHeight || 220;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  return { ctx, w, h };
}

function niceTicks(min, max, count) {
  if (min === max) { min -= 1; max += 1; }
  const span = max - min;
  const step0 = span / count;
  const mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const norm = step0 / mag;
  let step;
  if (norm < 1.5) step = 1; else if (norm < 3) step = 2; else if (norm < 7) step = 5; else step = 10;
  step *= mag;
  const start = Math.ceil(min / step) * step;
  const ticks = [];
  for (let v = start; v <= max + step * 0.5; v += step) ticks.push(v);
  return ticks;
}

/* Draw a multi-series line chart with axes, grid, legend and hover crosshair. */
function lineChart(canvas, model) {
  canvas._model = model;
  canvas._draw = function () {
    const { ctx, w, h } = prepCanvas(canvas);
    const m = canvas._model;
    const pad = { l: 52, r: 12, t: 10, b: 26 };
    const x = m.x || [];
    const series = (m.series || []).filter((s) => s.data && s.data.length);
    const plotW = w - pad.l - pad.r, plotH = h - pad.t - pad.b;

    if (!x.length || !series.length) {
      ctx.fillStyle = "#93a1bd"; ctx.font = "12px Inter, sans-serif";
      ctx.fillText("no data yet", pad.l, h / 2); return;
    }

    let yMin = Infinity, yMax = -Infinity;
    series.forEach((s) => s.data.forEach((v) => { if (v == null) return; if (v < yMin) yMin = v; if (v > yMax) yMax = v; }));
    if (m.yZero && yMin > 0) yMin = 0;
    if (m.yMinFixed != null) yMin = m.yMinFixed;
    if (m.yMaxFixed != null) yMax = m.yMaxFixed;
    if (yMin === Infinity) { yMin = 0; yMax = 1; }
    if (yMin === yMax) { yMax = yMin + 1; }
    const yPad = (yMax - yMin) * 0.08; yMin -= yPad; yMax += yPad;

    const xMin = x[0], xMax = x[x.length - 1] || 1;
    const X = (v) => pad.l + ((v - xMin) / (xMax - xMin || 1)) * plotW;
    const Y = (v) => pad.t + (1 - (v - yMin) / (yMax - yMin)) * plotH;

    // grid + y ticks
    ctx.strokeStyle = "#26304a"; ctx.lineWidth = 1; ctx.fillStyle = "#93a1bd"; ctx.font = "10px Inter, sans-serif";
    ctx.textAlign = "right"; ctx.textBaseline = "middle";
    niceTicks(yMin, yMax, 5).forEach((t) => {
      const yy = Y(t); if (yy < pad.t - 1 || yy > h - pad.b + 1) return;
      ctx.beginPath(); ctx.moveTo(pad.l, yy); ctx.lineTo(w - pad.r, yy); ctx.stroke();
      ctx.fillText(m.yFmt ? m.yFmt(t) : t.toFixed(0), pad.l - 6, yy);
    });
    // x ticks
    ctx.textAlign = "center"; ctx.textBaseline = "top";
    niceTicks(xMin, xMax, 6).forEach((t) => {
      const xx = X(t); if (xx < pad.l - 1 || xx > w - pad.r + 1) return;
      ctx.fillText(m.xFmt ? m.xFmt(t) : t.toFixed(0), xx, h - pad.b + 6);
    });

    // series
    series.forEach((s) => {
      ctx.strokeStyle = s.color; ctx.lineWidth = s.width || 1.8; ctx.globalAlpha = s.alpha || 1;
      ctx.beginPath(); let started = false;
      for (let i = 0; i < s.data.length; i++) {
        const v = s.data[i]; if (v == null) { started = false; continue; }
        const px = X(x[i]), py = Y(v);
        if (!started) { ctx.moveTo(px, py); started = true; } else ctx.lineTo(px, py);
      }
      ctx.stroke(); ctx.globalAlpha = 1;
    });

    // hover crosshair
    if (canvas._hoverX != null) {
      const hx = canvas._hoverX;
      let idx = 0, best = Infinity;
      for (let i = 0; i < x.length; i++) { const d = Math.abs(X(x[i]) - hx); if (d < best) { best = d; idx = i; } }
      const px = X(x[idx]);
      ctx.strokeStyle = "rgba(230,236,245,.35)"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(px, pad.t); ctx.lineTo(px, h - pad.b); ctx.stroke();
      // tooltip
      const lines = [`${m.xLabel || "day"} ${(x[idx]).toFixed(1)}`];
      series.forEach((s) => { if (s.data[idx] != null) lines.push(`${s.name}: ${m.tipFmt ? m.tipFmt(s.data[idx]) : s.data[idx].toFixed(2)}`); });
      const bw = 150, bh = 14 * lines.length + 8;
      let bx = px + 10; if (bx + bw > w) bx = px - bw - 10;
      const by = pad.t + 4;
      ctx.fillStyle = "rgba(12,16,22,.92)"; ctx.strokeStyle = "#26304a";
      ctx.fillRect(bx, by, bw, bh); ctx.strokeRect(bx, by, bw, bh);
      ctx.textAlign = "left"; ctx.textBaseline = "top"; ctx.font = "10px Inter, sans-serif";
      lines.forEach((ln, i) => {
        ctx.fillStyle = i === 0 ? "#e6ecf5" : (series[i - 1] ? series[i - 1].color : "#e6ecf5");
        ctx.fillText(ln, bx + 6, by + 5 + i * 14);
      });
      series.forEach((s) => {
        if (s.data[idx] == null) return;
        ctx.fillStyle = s.color; ctx.beginPath(); ctx.arc(px, Y(s.data[idx]), 2.6, 0, 7); ctx.fill();
      });
    }
  };
  if (!canvas._hooked) {
    canvas._hooked = true;
    canvas.addEventListener("mousemove", (e) => {
      const r = canvas.getBoundingClientRect(); canvas._hoverX = e.clientX - r.left; canvas._draw();
    });
    canvas.addEventListener("mouseleave", () => { canvas._hoverX = null; canvas._draw(); });
  }
  canvas._draw();
  drawLegend(canvas, (model.series || []).filter((s) => s.showInLegend !== false));
}

function drawLegend(canvas, series) {
  const id = canvas.id + "-legend";
  let el = document.getElementById(id);
  if (!el) { el = document.createElement("div"); el.id = id; el.className = "legend"; canvas.parentNode.appendChild(el); }
  el.innerHTML = series.map((s) => `<span><i style="background:${s.color}"></i>${s.name}</span>`).join("");
}

function scatterChart(canvas, points, opts) {
  opts = opts || {};
  const { ctx, w, h } = prepCanvas(canvas);
  const pad = { l: 54, r: 14, t: 12, b: 30 };
  const plotW = w - pad.l - pad.r, plotH = h - pad.t - pad.b;
  if (!points.length) { ctx.fillStyle = "#93a1bd"; ctx.font = "12px Inter"; ctx.fillText("no samples yet", pad.l, h / 2); return; }
  let xMin = Math.min(...points.map((p) => p.x)), xMax = Math.max(...points.map((p) => p.x));
  let yMin = Math.min(...points.map((p) => p.y)), yMax = Math.max(...points.map((p) => p.y));
  if (opts.xFrom0) xMin = Math.min(0, xMin);
  const xp = (xMax - xMin) * 0.06 || 0.02, yp = (yMax - yMin) * 0.08 || 1;
  xMin -= xp; xMax += xp; yMin -= yp; yMax += yp;
  const X = (v) => pad.l + ((v - xMin) / (xMax - xMin || 1)) * plotW;
  const Y = (v) => pad.t + (1 - (v - yMin) / (yMax - yMin || 1)) * plotH;

  ctx.strokeStyle = "#26304a"; ctx.fillStyle = "#93a1bd"; ctx.font = "10px Inter"; ctx.lineWidth = 1;
  ctx.textAlign = "right"; ctx.textBaseline = "middle";
  niceTicks(yMin, yMax, 5).forEach((t) => { const yy = Y(t); ctx.beginPath(); ctx.moveTo(pad.l, yy); ctx.lineTo(w - pad.r, yy); ctx.stroke(); ctx.fillText(opts.yFmt ? opts.yFmt(t) : t.toFixed(0), pad.l - 6, yy); });
  ctx.textAlign = "center"; ctx.textBaseline = "top";
  niceTicks(xMin, xMax, 6).forEach((t) => { ctx.fillText(opts.xFmt ? opts.xFmt(t) : t.toFixed(2), X(t), h - pad.b + 6); });

  // zero line for y
  if (yMin < 0 && yMax > 0) { ctx.strokeStyle = "rgba(147,161,189,.5)"; ctx.beginPath(); ctx.moveTo(pad.l, Y(0)); ctx.lineTo(w - pad.r, Y(0)); ctx.stroke(); }

  points.forEach((p) => { ctx.fillStyle = opts.color || "rgba(79,157,255,.55)"; ctx.beginPath(); ctx.arc(X(p.x), Y(p.y), 3, 0, 7); ctx.fill(); });

  // regression line
  if (opts.regression && points.length > 2) {
    const n = points.length, mx = points.reduce((a, p) => a + p.x, 0) / n, my = points.reduce((a, p) => a + p.y, 0) / n;
    let sxx = 0, sxy = 0; points.forEach((p) => { sxx += (p.x - mx) ** 2; sxy += (p.x - mx) * (p.y - my); });
    if (sxx > 1e-9) { const b = sxy / sxx, a = my - b * mx;
      ctx.strokeStyle = "#fbbf24"; ctx.lineWidth = 2; ctx.setLineDash([6, 4]);
      ctx.beginPath(); ctx.moveTo(X(xMin), Y(a + b * xMin)); ctx.lineTo(X(xMax), Y(a + b * xMax)); ctx.stroke(); ctx.setLineDash([]);
    }
  }
  // axis labels
  ctx.fillStyle = "#93a1bd"; ctx.font = "10px Inter"; ctx.textAlign = "center";
  if (opts.xLabel) ctx.fillText(opts.xLabel, pad.l + plotW / 2, h - 11);
  ctx.save(); ctx.translate(12, pad.t + plotH / 2); ctx.rotate(-Math.PI / 2);
  if (opts.yLabel) ctx.fillText(opts.yLabel, 0, 0); ctx.restore();
}

function barChart(canvas, bars, opts) {
  opts = opts || {};
  const { ctx, w, h } = prepCanvas(canvas);
  const pad = { l: 60, r: 12, t: 10, b: 64 };
  const plotW = w - pad.l - pad.r, plotH = h - pad.t - pad.b;
  if (!bars.length) { ctx.fillStyle = "#93a1bd"; ctx.font = "12px Inter"; ctx.fillText("run a sweep", pad.l, h / 2); return; }
  let yMin = Math.min(0, ...bars.map((b) => b.value)), yMax = Math.max(...bars.map((b) => b.value));
  yMax += (yMax - yMin) * 0.1;
  const Y = (v) => pad.t + (1 - (v - yMin) / (yMax - yMin || 1)) * plotH;
  ctx.strokeStyle = "#26304a"; ctx.fillStyle = "#93a1bd"; ctx.font = "10px Inter"; ctx.textAlign = "right"; ctx.textBaseline = "middle";
  niceTicks(yMin, yMax, 5).forEach((t) => { const yy = Y(t); ctx.beginPath(); ctx.moveTo(pad.l, yy); ctx.lineTo(w - pad.r, yy); ctx.stroke(); ctx.fillText(opts.yFmt ? opts.yFmt(t) : t.toFixed(0), pad.l - 6, yy); });
  const bw = plotW / bars.length * 0.7, gap = plotW / bars.length;
  bars.forEach((b, i) => {
    const x = pad.l + i * gap + (gap - bw) / 2;
    const y0 = Y(0), y1 = Y(b.value);
    ctx.fillStyle = b.color || "#4f9dff"; ctx.fillRect(x, Math.min(y0, y1), bw, Math.abs(y1 - y0));
    ctx.save(); ctx.translate(x + bw / 2, h - pad.b + 6); ctx.rotate(-Math.PI / 4);
    ctx.fillStyle = "#93a1bd"; ctx.font = "9px Inter"; ctx.textAlign = "right"; ctx.textBaseline = "middle";
    ctx.fillText(b.label, 0, 0); ctx.restore();
  });
}

// ----------------------------------------------------------------------------
// state + polling
// ----------------------------------------------------------------------------
let STATE = null;
let currentTab = "overview";
let lastTick = -1;
let controlsBuilt = false;

async function poll() {
  try {
    const r = await fetch("/api/state");
    STATE = await r.json();
    render();
  } catch (e) { /* server not ready */ }
  setTimeout(poll, 700);
}

function post(url, body) {
  return fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
}

// ----------------------------------------------------------------------------
// render dispatch
// ----------------------------------------------------------------------------
function render() {
  if (!STATE) return;
  const reset = STATE.overview.tick < lastTick;
  lastTick = STATE.overview.tick;
  if (reset) { controlsBuilt = false; }

  renderClock();
  renderKPIs();
  if (!controlsBuilt) { buildStaticControls(); controlsBuilt = true; }

  switch (currentTab) {
    case "overview": renderOverview(); break;
    case "shops": renderShops(); break;
    case "suppliers": renderSuppliers(); break;
    case "procurement": renderProcurement(); break;
    case "orderbook": renderOrderBook(); break;
    case "analytics": renderAnalytics(); break;
    case "strategy": /* on demand */ break;
    case "controls": break;
  }
}

function renderClock() {
  const o = STATE.overview, rt = STATE.runtime || {};
  document.getElementById("clock-day").textContent = o.day.toFixed(1);
  document.getElementById("clock-tick").textContent = `tick ${o.tick} · ${o.num_days ? "of " + o.num_days + "d" : "open"}`;
  const st = document.getElementById("run-status");
  if (rt.finished) { st.textContent = "finished"; st.className = "badge finished"; }
  else if (rt.running) { st.textContent = "running"; st.className = "badge running"; }
  else { st.textContent = "paused"; st.className = "badge paused"; }
}

function kpi(label, value, sub, cls) {
  return `<div class="kpi ${cls || ""}"><div class="k-label">${label}</div><div class="k-value">${value}</div>${sub ? `<div class="k-sub">${sub}</div>` : ""}</div>`;
}
function renderKPIs() {
  const o = STATE.overview;
  const corr = o.score_pnl_correlation;
  document.getElementById("kpis").innerHTML = [
    kpi("Total P&L", money(o.total_pnl), `${o.day.toFixed(0)} days`, o.total_pnl >= 0 ? "good" : "bad"),
    kpi("Revenue", money(o.total_revenue), `${fmtInt(o.total_sales_units)} units sold`),
    kpi("Procurement spend", money(o.total_procurement)),
    kpi("Avg satisfaction", o.avg_satisfaction.toFixed(1), "of 100", o.avg_satisfaction >= 85 ? "good" : "bad"),
    kpi("Stockouts", fmtInt(o.num_stockouts), null, o.num_stockouts > 0 ? "bad" : ""),
    kpi("Supplier defaults", fmtInt(o.num_defaults), null, o.num_defaults > 0 ? "bad" : ""),
    kpi("Market orders", fmtInt(o.num_market_orders), `${o.num_limit_orders_executed}/${o.num_limit_orders_created} limit exec`, "accent"),
    kpi("Intra-shop trades", fmtInt(o.num_intra_trades), null, "accent"),
    kpi("Score↔P&L corr", corr == null ? "—" : corr.toFixed(2), `${o.outcome_samples} samples`, corr != null && corr < 0 ? "good" : ""),
  ].join("");
}

// ---- overview ----
function renderOverview() {
  const h = STATE.history;
  lineChart(document.getElementById("chart-pnl-overview"), {
    x: h.t, yFmt: money, tipFmt: money2, xLabel: "day",
    series: [{ name: "Total P&L", color: "#34d399", width: 2.4, data: h.pnl_total }]
      .concat(Object.keys(h.pnl_by_shop).map((sid, i) => ({ name: sid.replace("shop_", "Shop "), color: SHOP_COLORS[i % SHOP_COLORS.length], width: 1, alpha: .5, data: h.pnl_by_shop[sid] }))),
  });
  lineChart(document.getElementById("chart-sat-overview"), {
    x: h.t, yMinFixed: 0, yMaxFixed: 100, xLabel: "day",
    series: [{ name: "Avg satisfaction", color: "#fbbf24", width: 2.2, data: h.satisfaction }],
  });
  lineChart(document.getElementById("chart-inv-overview"), {
    x: h.t, yZero: true, xLabel: "day",
    series: STATE.products.map((p, i) => ({ name: p.name, color: productColor(i), data: h.inv_by_product[p.product_id] })),
  });
  renderEvents();
}

function renderEvents() {
  const feed = document.getElementById("event-feed");
  const evs = (STATE.events || []).slice().reverse();
  feed.innerHTML = evs.map((e) => {
    const d = (e.timestamp / STATE.overview.ticks_per_day).toFixed(1);
    return `<div class="event-row"><span class="ts">d${d}</span><span class="et et-${e.event_type}">${e.event_type}</span><span>${eventDetail(e)}</span></div>`;
  }).join("") || `<div class="empty">no events yet</div>`;
}
function eventDetail(e) {
  switch (e.event_type) {
    case "market_order": return `${e.shop_id} bought ${fmtInt(e.quantity)} ${e.product_id} from ${e.supplier_id} @£${f2(e.price)} (score ${f3(e.procurement_score)})`;
    case "supplier_default": return `${e.supplier_id} defaulted on ${e.shop_id}'s ${fmtInt(e.quantity)} ${e.product_id}`;
    case "supplier_delivery": return `${fmtInt(e.quantity)} ${e.product_id} delivered to ${e.shop_id}`;
    case "stockout": return `${e.shop_id} short ${f2(e.unmet_units)} ${e.product_id} (cost ${money2(e.stockout_cost)})`;
    case "limit_order_executed": return `${e.shop_id} limit ${e.product_id} hit @score ${f3(e.procurement_score)} ≤ ${f3(e.max_score)}`;
    case "limit_order_created": return `${e.shop_id} wants ${fmtInt(e.quantity)} ${e.product_id} if score ≤ ${f3(e.max_score)}`;
    case "intra_retail_trade": return `${e.shop_a} ↔ ${e.shop_b}: ${fmtInt(e.qty_p)} ${e.product_given} for ${fmtInt(e.qty_q)} ${e.product_received}`;
    default: return "";
  }
}

// ---- shops ----
function renderShops() {
  const el = document.getElementById("shops-container");
  el.innerHTML = STATE.shops.map((s) => {
    const rows = s.products.map((p) => `<tr>
      <td>${p.product_name}</td>
      <td class="num">${fmtInt(p.inventory)}${p.incoming > 0 ? ` <span style="color:var(--muted)">(+${fmtInt(p.incoming)})</span>` : ""}</td>
      <td class="num">${f2(p.sales_rate)}</td>
      <td class="num">${fmtInt(p.threshold)}</td>
      <td class="num">${p.days_of_inventory == null ? "∞" : f2(p.days_of_inventory)}</td>
      <td><span class="pill s-${p.status.replace(/ /g, ".")}">${p.status}</span></td></tr>`).join("");
    return `<div class="card">
      <div class="entity-head">
        <div><div class="entity-title">${s.name}</div>
          <div class="chips" style="margin-top:.4rem">
            <div class="chip"><span>P&L</span> <b style="color:${s.pnl >= 0 ? "var(--good)" : "var(--bad)"}">${money(s.pnl)}</b></div>
            <div class="chip"><span>Cash</span> <b>${money(s.cash)}</b></div>
            <div class="chip"><span>Revenue</span> <b>${money(s.revenue)}</b></div>
            <div class="chip"><span>Procure</span> <b>${money(s.procurement_cost)}</b></div>
            <div class="chip"><span>Stockout cost</span> <b>${money(s.stockout_cost)}</b></div>
            <div class="chip"><span>Open orders</span> <b>${s.open_market_orders}M / ${s.open_limit_orders}L</b></div>
          </div>
        </div>
        <div style="text-align:right">
          <div style="font-size:.7rem;color:var(--muted)">satisfaction</div>
          <div style="font-size:1.3rem;font-weight:700;color:${s.satisfaction >= 85 ? "var(--good)" : "var(--bad)"}">${s.satisfaction.toFixed(1)}</div>
          <div class="satbar"><i style="width:${s.satisfaction}%"></i></div>
        </div>
      </div>
      <div class="table-wrap"><table>
        <thead><tr><th>Product</th><th>Inventory</th><th>Sales/day</th><th>Threshold</th><th>Days inv</th><th>Status</th></tr></thead>
        <tbody>${rows}</tbody></table></div></div>`;
  }).join("");
}

// ---- suppliers ----
function renderSuppliers() {
  const el = document.getElementById("suppliers-container");
  el.innerHTML = STATE.suppliers.map((s, i) => {
    const rows = s.products.map((p) => `<tr><td>${p.product_name}</td><td class="num">${fmtInt(p.inventory)}</td><td class="num">£${f2(p.price)}</td></tr>`).join("");
    const total = s.successful_orders + s.defaults;
    return `<div class="card">
      <div class="entity-head">
        <div class="entity-title" style="color:${supplierColor(s.supplier_id, i)}">${s.name}</div>
        <div class="chips">
          <div class="chip"><span>Lead time</span> <b>${s.delivery_days}d</b></div>
          <div class="chip"><span>Pack size</span> <b>${s.pack_size}</b></div>
          <div class="chip"><span>Default prob</span> <b>${pct(s.default_probability)}</b></div>
          <div class="chip"><span>Hist. default</span> <b style="color:${s.historical_default_rate > .15 ? "var(--bad)" : "var(--text)"}">${pct(s.historical_default_rate)}</b></div>
          <div class="chip"><span>Risk score</span> <b style="color:${s.risk_score > .15 ? "var(--bad)" : "var(--good)"}">${f3(s.risk_score)}</b></div>
          <div class="chip"><span>Orders</span> <b>${s.successful_orders} ok / ${s.defaults} def</b></div>
          <div class="chip"><span>Outstanding</span> <b>${s.outstanding_orders}</b></div>
        </div>
      </div>
      <div class="table-wrap"><table><thead><tr><th>Product</th><th>Inventory</th><th>Price</th></tr></thead><tbody>${rows}</tbody></table></div>
    </div>`;
  }).join("");
}

// ---- procurement (explainability) ----
function renderProcurement() {
  const el = document.getElementById("decisions-container");
  const decisions = STATE.decisions || [];
  if (!decisions.length) { el.innerHTML = `<div class="card"><div class="empty">No procurement decisions yet — start the simulation and wait for a shop to hit its threshold.</div></div>`; return; }
  el.innerHTML = decisions.map((d) => {
    const pname = (STATE.products.find((p) => p.product_id === d.product_id) || {}).name || d.product_id;
    const rows = d.offers.map((o) => {
      const sel = o.supplier_id === d.selected_supplier;
      return `<tr class="${sel ? "selected" : ""}">
        <td>${o.supplier_name}${sel ? ' <span class="selected-tag">← SELECTED</span>' : ""}</td>
        <td class="num">£${f2(o.price)}</td>
        <td class="num">${pct(o.risk)}</td>
        <td class="num">${o.delivery_days}d</td>
        <td class="num">${o.order_quantity} <span style="color:var(--muted)">(${pct(o.quantity_mismatch)})</span></td>
        <td class="num">${f3(o.price_score)}</td>
        <td class="num">${f3(o.risk_score)}</td>
        <td class="num">${f3(o.delivery_score)}</td>
        <td class="num">${f3(o.quantity_score)}</td>
        <td class="num"><b>${f3(o.score)}</b></td></tr>`;
    }).join("");
    const w = d.offers[0] ? d.offers[0].weights : {};
    return `<div class="decision">
      <div class="d-head">
        <div>Shop <b>${d.shop_id}</b> needs <b>${fmtInt(d.required)}</b> units of <b>${pname}</b> <span style="color:var(--muted)">· ${d.reason} · day ${(d.timestamp / STATE.overview.ticks_per_day).toFixed(1)}</span></div>
        <div style="color:var(--muted)">weights: price ${pct(w.price)} · risk ${pct(w.risk)} · delivery ${pct(w.delivery)} · qty ${pct(w.quantity)}</div>
      </div>
      <div class="table-wrap"><table>
        <thead><tr><th>Supplier</th><th>Price</th><th>Risk</th><th>Delivery</th><th>Qty (mismatch)</th><th>Price·s</th><th>Risk·s</th><th>Deliv·s</th><th>Qty·s</th><th>Score ↓</th></tr></thead>
        <tbody>${rows}</tbody></table></div>
      <p class="hint tiny">Component scores are normalised to [0,1] (lower = better) and combined with the weights above. The lowest total score wins — which is why the cheapest nominal price is not always selected.</p>
    </div>`;
  }).join("");
}

// ---- order book ----
function table(el, headers, rows) {
  el.innerHTML = `<thead><tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.length ? rows.join("") : `<tr><td colspan="${headers.length}" class="empty">none yet</td></tr>`}</tbody>`;
}
function renderOrderBook() {
  const ob = STATE.order_book, tpd = STATE.overview.ticks_per_day;
  const day = (t) => (t / tpd).toFixed(1);
  table(document.getElementById("tbl-market"),
    ["Shop", "Product", "Qty", "Supplier", "Score", "Unit £", "Ordered", "ETA", "Status"],
    ob.market_orders.map((o) => `<tr>
      <td>${o.shop_id}</td><td>${o.product_id}</td><td class="num">${fmtInt(o.quantity)}</td><td>${o.supplier_id}</td>
      <td class="num">${f3(o.score)}</td><td class="num">£${f2(o.unit_price)}</td><td class="num">d${day(o.order_time)}</td>
      <td class="num">d${day(o.expected_delivery)}</td>
      <td><span class="pill ${o.status === "defaulted" ? "s-Stockout" : o.status === "delivered" ? "s-Healthy" : "s-Procurement"}">${o.status}</span></td></tr>`));
  table(document.getElementById("tbl-limit"),
    ["Shop", "Product", "Qty", "Max score", "Best score now", "Submitted", "Status"],
    ob.limit_orders.map((o) => `<tr>
      <td>${o.shop_id}</td><td>${o.product_id}</td><td class="num">${fmtInt(o.quantity)}</td><td class="num">${f3(o.max_score)}</td>
      <td class="num">${o.current_best_score == null ? "—" : f3(o.current_best_score)}</td><td class="num">d${day(o.created_time)}</td>
      <td><span class="pill ${o.status === "executed" ? "s-Healthy" : "s-Approaching"}">${o.status}</span></td></tr>`));
  table(document.getElementById("tbl-intra"),
    ["Seller", "Buyer", "Gives", "Receives", "Ratio", "Qty given", "Qty recv", "Day"],
    ob.intra_trades.map((t) => `<tr>
      <td>${t.seller_id}</td><td>${t.buyer_id}</td><td>${t.product_given}</td><td>${t.product_received}</td>
      <td class="num">${f2(t.exchange_ratio)}</td><td class="num">${fmtInt(t.qty_given)}</td><td class="num">${fmtInt(t.qty_received)}</td><td class="num">d${day(t.time)}</td></tr>`));
}

// ---- analytics ----
function selectedVal(id, fallback) { const el = document.getElementById(id); return el && el.value ? el.value : fallback; }
function renderAnalytics() {
  const h = STATE.history, o = STATE.overview;
  lineChart(document.getElementById("chart-pnl"), {
    x: h.t, yFmt: money, tipFmt: money2, xLabel: "day",
    series: [{ name: "Total P&L", color: "#34d399", width: 2.4, data: h.pnl_total }]
      .concat(Object.keys(h.pnl_by_shop).map((sid, i) => ({ name: sid.replace("shop_", "Shop "), color: SHOP_COLORS[i % SHOP_COLORS.length], width: 1, alpha: .55, data: h.pnl_by_shop[sid] }))),
  });

  // scatter score vs pnl
  const corr = o.score_pnl_correlation;
  const lbl = document.getElementById("corr-label");
  lbl.textContent = corr == null ? "correlation —" : `correlation r = ${corr.toFixed(3)}`;
  lbl.style.color = corr != null && corr < 0 ? "var(--good)" : (corr != null ? "var(--warn)" : "var(--muted)");
  scatterChart(document.getElementById("chart-scatter"), (STATE.scatter_samples || []).map((s) => ({ x: s.score, y: s.pnl })),
    { regression: true, xLabel: "procurement score (lower = better)", yLabel: "subsequent P&L", yFmt: money, xFmt: (v) => v.toFixed(2) });

  // prices
  const pp = selectedVal("price-product", STATE.products[0].product_id);
  lineChart(document.getElementById("chart-prices"), {
    x: h.t, yFmt: (v) => "£" + v.toFixed(0), tipFmt: (v) => "£" + v.toFixed(2), xLabel: "day",
    series: STATE.suppliers.map((s, i) => ({ name: s.name, color: supplierColor(s.supplier_id, i), data: h.price[s.supplier_id][pp] })),
  });

  // demand actual vs baseline
  const dp = selectedVal("demand-product", STATE.products[0].product_id);
  lineChart(document.getElementById("chart-demand"), {
    x: h.t, yZero: true, xLabel: "day", tipFmt: (v) => v.toFixed(2),
    series: [
      { name: "Actual demand", color: "#4f9dff", width: 1.4, data: h.demand_actual[dp] },
      { name: "Baseline demand", color: "#fbbf24", width: 2, data: h.demand_baseline[dp] },
    ],
  });

  lineChart(document.getElementById("chart-score"), {
    x: h.t, yFmt: (v) => v.toFixed(2), tipFmt: (v) => v.toFixed(3), xLabel: "day",
    series: STATE.suppliers.map((s, i) => ({ name: s.name, color: supplierColor(s.supplier_id, i), data: h.score[s.supplier_id] })),
  });
  lineChart(document.getElementById("chart-risk"), {
    x: h.t, yZero: true, yFmt: pct, tipFmt: pct, xLabel: "day",
    series: STATE.suppliers.map((s, i) => ({ name: s.name, color: supplierColor(s.supplier_id, i), data: h.risk[s.supplier_id] })),
  });

  const ip = selectedVal("inv-product", "all");
  const invSeries = ip === "all"
    ? STATE.products.map((p, i) => ({ name: p.name, color: productColor(i), data: h.inv_by_product[p.product_id] }))
    : [{ name: (STATE.products.find((p) => p.product_id === ip) || {}).name, color: productColor(STATE.products.findIndex((p) => p.product_id === ip)), data: h.inv_by_product[ip], width: 2.2 }];
  lineChart(document.getElementById("chart-inv"), { x: h.t, yZero: true, xLabel: "day", series: invSeries });

  lineChart(document.getElementById("chart-sat"), {
    x: h.t, yMinFixed: 0, yMaxFixed: 100, xLabel: "day",
    series: [{ name: "Avg satisfaction", color: "#fbbf24", width: 2.2, data: h.satisfaction }],
  });
}

// ----------------------------------------------------------------------------
// controls (built once per reset)
// ----------------------------------------------------------------------------
function opt(v, t, sel) { return `<option value="${v}" ${sel ? "selected" : ""}>${t}</option>`; }
function buildStaticControls() {
  const c = STATE.config;
  // weight sliders
  const ws = document.getElementById("weight-sliders");
  const W = [["w_price", "Price", "price"], ["w_risk", "Risk", "risk"], ["w_delivery", "Delivery", "delivery"], ["w_quantity", "Quantity mismatch", "quantity"]];
  ws.innerHTML = W.map(([k, label]) => `<div class="wslider">
    <label>${label} <output id="out-${k}">${(c[k] * 100).toFixed(0)}%</output></label>
    <input type="range" id="wt-${k}" min="0" max="100" value="${Math.round(c[k] * 100)}" />
  </div>`).join("");
  W.forEach(([k]) => {
    const inp = document.getElementById("wt-" + k);
    inp.addEventListener("input", () => { document.getElementById("out-" + k).textContent = inp.value + "%"; });
    inp.addEventListener("change", pushWeights);
  });

  // limit order selects
  const shopSel = document.getElementById("lo-shop"), prodSel = document.getElementById("lo-product");
  shopSel.innerHTML = STATE.shops.map((s) => opt(s.shop_id, s.name)).join("");
  prodSel.innerHTML = STATE.products.map((p) => opt(p.product_id, p.name)).join("");

  // analytics selects
  ["price-product", "demand-product"].forEach((id) => {
    document.getElementById(id).innerHTML = STATE.products.map((p) => opt(p.product_id, p.name)).join("");
  });
  document.getElementById("inv-product").innerHTML = opt("all", "All products", true) + STATE.products.map((p) => opt(p.product_id, p.name)).join("");

  // scenario controls
  buildScenarioControls(c);
}

function pushWeights() {
  post("/api/weights", {
    price: +document.getElementById("wt-w_price").value / 100,
    risk: +document.getElementById("wt-w_risk").value / 100,
    delivery: +document.getElementById("wt-w_delivery").value / 100,
    quantity: +document.getElementById("wt-w_quantity").value / 100,
  });
}

function ctrlField(key, label, val, step) {
  return `<label>${label}<input data-key="${key}" type="number" step="${step || "any"}" value="${val}" /></label>`;
}
function buildScenarioControls(c) {
  const grid = document.getElementById("ctrl-scenario");
  grid.innerHTML = [
    ctrlField("seed", "Random seed", c.seed, 1),
    ctrlField("num_days", "Simulation days", c.num_days, 1),
    ctrlField("num_shops", "Repair shops", c.num_shops, 1),
    ctrlField("coverage_days", "Coverage days", c.coverage_days, 0.5),
    ctrlField("initial_inventory_days", "Initial inventory (days)", c.initial_inventory_days, 0.5),
    ctrlField("demand_noise", "Demand volatility (noise σ)", c.demand_noise, 0.01),
    ctrlField("spike_frequency", "Demand spike frequency", c.spike_frequency, 0.001),
    ctrlField("spike_magnitude", "Spike magnitude (days)", c.spike_magnitude, 0.5),
    ctrlField("default_prob_drift", "Supplier default drift", c.default_prob_drift, 0.0005),
    ctrlField("stockout_penalty_per_unit", "Stockout penalty (×retail)", c.stockout_penalty_per_unit, 0.05),
    ctrlField("holding_cost_per_unit_day", "Holding cost (×cost/day)", c.holding_cost_per_unit_day, 0.005),
    ctrlField("churn_sensitivity", "Churn sensitivity", c.churn_sensitivity, 0.001),
  ].join("");

  const sup = document.getElementById("ctrl-suppliers");
  sup.innerHTML = c.suppliers.map((s) => `
    ${ctrlField("supplier_overrides." + s.supplier_id + ".delivery_days", s.name + " delivery (d)", s.delivery_days, 0.5)}
    ${ctrlField("supplier_overrides." + s.supplier_id + ".base_default_probability", s.name + " default prob", s.base_default_probability, 0.01)}
  `).join("");

  const prod = document.getElementById("ctrl-products");
  prod.innerHTML = c.products.map((p) => `
    ${ctrlField("product_overrides." + p.product_id + ".retail_price", p.name + " retail £", p.retail_price, 1)}
    ${ctrlField("product_overrides." + p.product_id + ".baseline_demand", p.name + " demand/day", p.baseline_demand, 0.5)}
  `).join("");
}

function collectOverrides() {
  const ov = {};
  document.querySelectorAll("[data-key]").forEach((inp) => {
    const key = inp.getAttribute("data-key");
    const val = parseFloat(inp.value);
    if (isNaN(val)) return;
    if (key.includes(".")) {
      const [grp, id, field] = key.split(".");
      ov[grp] = ov[grp] || {};
      ov[grp][id] = ov[grp][id] || {};
      ov[grp][id][field] = val;
    } else ov[key] = val;
  });
  // include current live weights
  ov.w_price = +document.getElementById("wt-w_price").value / 100;
  ov.w_risk = +document.getElementById("wt-w_risk").value / 100;
  ov.w_delivery = +document.getElementById("wt-w_delivery").value / 100;
  ov.w_quantity = +document.getElementById("wt-w_quantity").value / 100;
  return ov;
}

// ----------------------------------------------------------------------------
// strategy sweep
// ----------------------------------------------------------------------------
async function runSweep() {
  const btn = document.getElementById("sweep-run"), status = document.getElementById("sweep-status");
  btn.disabled = true; status.textContent = "running headless simulations…";
  try {
    const r = await post("/api/sweep", {
      days: +document.getElementById("sweep-days").value,
      random_configs: +document.getElementById("sweep-random").value,
    });
    const out = (await r.json()).sweep;
    renderSweep(out);
    status.textContent = `done · best P&L ${money(out.best.total_pnl)} with risk ${pct(out.best.weights.risk)} / delivery ${pct(out.best.weights.delivery)}`;
  } catch (e) { status.textContent = "sweep failed: " + e; }
  btn.disabled = false;
}
function stratLabel(w) { return `P${Math.round(w.price * 100)} R${Math.round(w.risk * 100)} D${Math.round(w.delivery * 100)} Q${Math.round(w.quantity * 100)}`; }
function renderSweep(out) {
  const res = out.results;
  barChart(document.getElementById("chart-sweep"),
    res.map((r, i) => ({ label: stratLabel(r.weights), value: r.total_pnl, color: i === 0 ? "#34d399" : "#4f9dff" })),
    { yFmt: money });
  scatterChart(document.getElementById("chart-sweep-scatter"),
    res.map((r) => ({ x: r.num_stockouts, y: r.total_pnl })),
    { color: "rgba(248,113,113,.6)", xLabel: "stockouts", yLabel: "long-term P&L", yFmt: money, xFmt: (v) => v.toFixed(0), xFrom0: true });
  table(document.getElementById("tbl-sweep"),
    ["Rank", "Price", "Risk", "Delivery", "Quantity", "Long-term P&L", "Stockouts", "Defaults", "Intra trades"],
    res.map((r, i) => `<tr class="${i === 0 ? "selected" : ""}">
      <td>${i + 1}${i === 0 ? ' <span class="selected-tag">BEST</span>' : ""}</td>
      <td class="num">${pct(r.weights.price)}</td><td class="num">${pct(r.weights.risk)}</td>
      <td class="num">${pct(r.weights.delivery)}</td><td class="num">${pct(r.weights.quantity)}</td>
      <td class="num"><b>${money2(r.total_pnl)}</b></td><td class="num">${fmtInt(r.num_stockouts)}</td>
      <td class="num">${fmtInt(r.num_defaults)}</td><td class="num">${fmtInt(r.num_intra_trades)}</td></tr>`));
}

// ----------------------------------------------------------------------------
// wiring
// ----------------------------------------------------------------------------
function activateTab(name) {
  const tab = document.querySelector(`.tab[data-tab="${name}"]`);
  const page = document.querySelector(`.tabpage[data-page="${name}"]`);
  if (!tab || !page) return;
  document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
  document.querySelectorAll(".tabpage").forEach((x) => x.classList.remove("active"));
  tab.classList.add("active");
  page.classList.add("active");
  currentTab = name;
  if (location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
  render();
}
function setupTabs() {
  document.querySelectorAll(".tab").forEach((t) => {
    t.addEventListener("click", () => activateTab(t.getAttribute("data-tab")));
  });
  const initial = location.hash.replace("#", "");
  if (initial) activateTab(initial);
  window.addEventListener("hashchange", () => activateTab(location.hash.replace("#", "")));
}

function setup() {
  setupTabs();
  document.getElementById("btn-start").addEventListener("click", () => post("/api/control", { action: "start" }));
  document.getElementById("btn-pause").addEventListener("click", () => post("/api/control", { action: "pause" }));
  document.getElementById("btn-step").addEventListener("click", () => post("/api/control", { action: "step", n: STATE ? STATE.overview.ticks_per_day : 24 }));
  document.getElementById("btn-reset").addEventListener("click", () => post("/api/control", { action: "reset", overrides: STATE ? collectOverrides() : {} }));

  const speed = document.getElementById("speed");
  speed.addEventListener("input", () => { document.getElementById("speed-val").textContent = speed.value; });
  speed.addEventListener("change", () => post("/api/speed", { speed: +speed.value }));

  document.getElementById("lo-submit").addEventListener("click", async () => {
    const r = await post("/api/limit_order", {
      shop_id: document.getElementById("lo-shop").value,
      product_id: document.getElementById("lo-product").value,
      quantity: +document.getElementById("lo-qty").value,
      max_score: +document.getElementById("lo-score").value,
    });
    const res = await r.json();
    document.getElementById("lo-result").textContent = res.ok ? `Limit order ${res.order_id} placed — it will execute when the score drops to your threshold.` : "Failed: " + (res.error || "");
  });

  document.getElementById("apply-reset").addEventListener("click", () => post("/api/control", { action: "reset", overrides: collectOverrides() }));
  document.getElementById("sweep-run").addEventListener("click", runSweep);

  // redraw charts on resize
  window.addEventListener("resize", () => { if (STATE) render(); });
  poll();
}

document.addEventListener("DOMContentLoaded", setup);
