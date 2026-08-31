/* 매출 누수 진단 리포트 — 렌더러
   외부 차트 라이브러리 없이 SVG를 직접 생성한다. */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const NS = "http://www.w3.org/2000/svg";
const cw = (box, min = 560) => Math.max(box.clientWidth || 0, min);

/* ── 포맷 ─────────────────────────────────────────── */
const eok = v => v / 10000;                                   // 만원 → 억
const fEok = (v, d = 1) => eok(v).toLocaleString("ko-KR", { maximumFractionDigits: d, minimumFractionDigits: d }) + "억";
const fNum = v => (v ?? 0).toLocaleString("ko-KR");
const fMan = v => fNum(v) + "만원";
const fPct = (v, d = 1) => v.toFixed(d) + "%";

/* ── SVG 헬퍼 ─────────────────────────────────────── */
function svg(w, h, cls = "") {
  const s = document.createElementNS(NS, "svg");
  s.setAttribute("viewBox", `0 0 ${w} ${h}`);
  s.setAttribute("preserveAspectRatio", "xMidYMid meet");
  if (cls) s.setAttribute("class", cls);
  return s;
}
function el(tag, attrs = {}, parent) {
  const e = document.createElementNS(NS, tag);
  for (const k in attrs) {
    let v = attrs[k];
    if (v === null || v === undefined) continue;
    if ((k === "width" || k === "height" || k === "r") && typeof v === "number") v = Math.max(v, 0);
    e.setAttribute(k, v);
  }
  if (parent) parent.appendChild(e);
  return e;
}
function txt(parent, x, y, s, attrs = {}) {
  const t = el("text", { x, y, ...attrs }, parent);
  t.textContent = s;
  return t;
}
function niceMax(v, steps = 4) {
  if (v <= 0) return steps;
  const raw = v / steps;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const n = [1, 2, 2.5, 5, 10].find(m => m * mag >= raw) || 10;
  return n * mag * steps;
}

/* ── 툴팁 ─────────────────────────────────────────── */
const tip = $("#tip");
function bindTip(node, html) {
  node.addEventListener("pointerenter", e => { tip.innerHTML = html; tip.classList.add("on"); move(e); });
  node.addEventListener("pointermove", move);
  node.addEventListener("pointerleave", () => tip.classList.remove("on"));
  function move(e) {
    const pad = 16, w = tip.offsetWidth, h = tip.offsetHeight;
    let x = e.clientX + pad, y = e.clientY + pad;
    if (x + w > innerWidth - 8) x = e.clientX - w - pad;
    if (y + h > innerHeight - 8) y = e.clientY - h - pad;
    tip.style.left = x + "px"; tip.style.top = y + "px";
  }
}
const trow = (k, v) => `<div class="r"><span>${k}</span><span>${v}</span></div>`;

/* ── 부트 ─────────────────────────────────────────── */
let D;
fetch("data/dataset.json").then(r => r.json()).then(d => { D = d; boot(); })
  .catch(e => { document.body.insertAdjacentHTML("afterbegin",
    `<div style="padding:40px;color:#ff5d5d">데이터 로드 실패: ${e}. 로컬에서 볼 때는 <code>python -m http.server</code> 로 실행하세요.</div>`); });

let LOGRULES = [];
fetch("data/cleaning_log.json").then(r => r.json()).then(j => { LOGRULES = j.rules; renderLog(); }).catch(() => {});

function boot() {
  renderHero();
  renderQuality();
  renderScale();
  renderLeak();
  renderFunnel();
  renderMonthly();
  renderROI();
  renderSim();
  renderStock();
  renderPeople();
  renderInsights();
  renderQueue();
  renderMethod();
  wireNav();
  addEventListener("resize", debounce(() => {
    renderScale(); renderQuality(); renderFunnel(); renderMonthly();
    renderROI(); renderStock(); renderPeople(); renderLeak();
  }, 220));
}
function debounce(f, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => f(...a), ms); }; }

/* ── HERO ─────────────────────────────────────────── */
function renderHero() {
  const k = D.kpi;
  $("#h-rev").textContent = fEok(k.revenue_done);
  $("#h-leak").textContent = fEok(k.leak_total);
  $("#h-rows").textContent = fNum(D.quality.raw_rows);
  $("#h-asof").textContent = D.meta.asof;
  $("#f-asof").textContent = D.meta.asof;
  $("#f-size").textContent = "dataset.json " + Math.round(JSON.stringify(D).length / 1024) + "KB";

  const stats = [
    { v: fEok(k.revenue_done), k: "계약 완료 매출", c: "var(--win)" },
    { v: fEok(k.leak_total), k: "식별된 누수 총액", c: "var(--leak)" },
    { v: fPct(k.leak_ratio), k: "잠재 매출 중 누수 비중", c: "var(--leak)" },
    { v: fNum(k.customers), k: "정리 후 고객 수", s: `원본 ${fNum(k.customers_raw)}행` },
    { v: fPct(k.conv_rate), k: "계약희망 → 계약 전환율" },
    { v: fEok(k.queue_value), k: "액션 큐 TOP 30 기대값", c: "var(--acc)" },
  ];
  $("#hero-stats").innerHTML = stats.map(s =>
    `<div><div class="v" style="color:${s.c || "var(--tx)"}">${s.v}</div><div class="k">${s.k}</div></div>`).join("");
}

/* ── 01 데이터 신뢰 ───────────────────────────────── */
function renderQuality() {
  const q = D.quality;
  $("#q-cards").innerHTML = [
    { v: fNum(q.raw_rows), k: "원본 총 행 수", s: `5개 표 · 정리 후 ${fNum(q.clean_rows)}행` },
    { v: fNum(q.issues), k: "표준화·보정한 값", s: `전체 셀의 ${q.issue_rate}%` },
    { v: fNum(D.kpi.customers_raw - D.kpi.customers), k: "제거한 중복 고객", s: "동일 ID · 전화 포맷만 상이" },
    { v: fNum(q.integrity["고아 상담(고객)"] + q.integrity["고아 상담(매물)"]), k: "마스터에 없는 참조", s: "삭제 대신 격리 후 금액 환산" },
  ].map(c => `<div class="q-card"><div class="v">${c.v}</div><div class="k">${c.k}</div><div class="s">${c.s}</div></div>`).join("");

  // before/after 표기 종수
  const box = $("#c-ba"); box.innerHTML = "";
  const keys = Object.keys(q.before);
  const W = cw(box, 460), rowH = 40, H = keys.length * rowH + 34;
  const s = svg(W, H); box.appendChild(s);
  const L = 96, R = W - 54, max = Math.max(...Object.values(q.before));
  keys.forEach((kk, i) => {
    const y = i * rowH + 16;
    txt(s, L - 12, y + 15, kk, { class: "ax", "text-anchor": "end" });
    const wB = (q.before[kk] / max) * (R - L), wA = (q.after[kk] / max) * (R - L);
    el("rect", { x: L, y: y + 3, width: wB, height: 9, rx: 4, fill: "var(--leak)", opacity: .75 }, s);
    el("rect", { x: L, y: y + 16, width: wA, height: 9, rx: 4, fill: "var(--win)" }, s);
    txt(s, L + wB + 7, y + 11, q.before[kk] + "종", { class: "ax", fill: "var(--leak)" });
    txt(s, L + wA + 7, y + 24, q.after[kk] + "종", { class: "ax", fill: "var(--win)" });
  });
  txt(s, L, H - 4, "정리 전", { class: "ax", fill: "var(--leak)" });
  txt(s, L + 52, H - 4, "정리 후", { class: "ax", fill: "var(--win)" });

  // 무결성 이슈
  const box2 = $("#c-integrity"); box2.innerHTML = "";
  const items = Object.entries(q.integrity).sort((a, b) => b[1] - a[1]);
  const W2 = cw(box2, 460), rh = 27, H2 = items.length * rh + 10;
  const s2 = svg(W2, H2); box2.appendChild(s2);
  const L2 = 106, R2 = W2 - 46, mx = Math.max(...items.map(i => i[1]));
  items.forEach(([kk, v], i) => {
    const y = i * rh + 8;
    txt(s2, L2 - 10, y + 12, kk, { class: "ax", "text-anchor": "end" });
    const w = mx ? (v / mx) * (R2 - L2) : 0;
    const r = el("rect", { x: L2, y, width: Math.max(w, 1), height: 15, rx: 4, fill: "var(--warn)", opacity: .8, class: "hit" }, s2);
    bindTip(r, `<b>${kk}</b>${trow("건수", fNum(v))}`);
    txt(s2, L2 + w + 7, y + 12, fNum(v), { class: "ax" });
  });
}

/* ── 가격 스케일 산점도 ───────────────────────────── */
function renderScale() {
  const ps = D.price_scale;
  $("#scale-text").innerHTML =
    `매물 마스터(<code>units.csv</code>)의 분양가와 실제 계약금액(<code>contracts_raw.csv</code>)을
     ${fNum(ps.pairs)}건 조인해 비교했습니다. 두 값의 <b>상관계수는 ${ps.corr}</b> — 통계적으로 아무 관계가 없습니다.
     계약금액은 심지어 <b>전용면적과도 무관</b>(r = ${ps.corr_area})했습니다.
     즉 두 표는 같은 매물의 가격을 서로 다른 체계로 적어두고 있었고,
     이걸 모르고 합산하면 매출이 <b>${ps.ratio}배</b> 부풀려집니다.`;

  $("#scale-facts").innerHTML = [
    ["마스터 표시가 (중앙값)", `평당 ${fNum(ps.listed_pyeong)}만원`],
    ["실제 계약가 (중앙값)", `평당 ${fNum(ps.contract_pyeong)}만원`],
    ["두 값의 상관계수", `r = ${ps.corr}`],
    ["면적 ↔ 계약가 상관계수", `r = ${ps.corr_area}`],
    ["예: 102A형 마스터 / 실계약", `${fNum(ps.listed_med["102A"])} / ${fNum(ps.type_ref["102A"])}만원`],
  ].map(([k, v]) => `<li><span>${k}</span><span>${v}</span></li>`).join("");

  $("#scale-verdict").innerHTML =
    `<b>처리:</b> 매출과 재고 금액은 <b>실계약가만</b> 근거로 삼았습니다.
     마스터 표시가는 <code>price_listed</code>로 분리 보관하고, 재고 평가액은
     타입별 실계약가 중앙값으로 캘리브레이션(계수 ${Math.min(...Object.values(ps.cal)).toFixed(3)}~${Math.max(...Object.values(ps.cal)).toFixed(3)})해 산출했습니다.
     이 보정을 하지 않으면 미계약 재고가 ${fEok(D.leaks.find(l => l.key === "stock").amount / (ps.contract_pyeong / ps.listed_pyeong), 0)} 규모로 잡혀 모든 우선순위가 뒤집힙니다.`;

  // 산점도
  const box = $("#c-scatter"); box.innerHTML = "";
  const W = cw(box, 460), H = 300;
  const s = svg(W, H); box.appendChild(s);
  const P = { t: 14, r: 14, b: 40, l: 54 };
  const xs = Object.keys(ps.listed_med), pts = [];
  // 타입별 산점 대신 대표점 + 이론선 비교
  const maxX = niceMax(Math.max(...Object.values(ps.listed_med)));
  const maxY = niceMax(Math.max(...Object.values(ps.type_ref)));
  const X = v => P.l + (v / maxX) * (W - P.l - P.r);
  const Y = v => H - P.b - (v / maxY) * (H - P.t - P.b);

  for (let i = 0; i <= 4; i++) {
    const y = P.t + ((H - P.t - P.b) / 4) * i;
    el("line", { x1: P.l, y1: y, x2: W - P.r, y2: y, class: "grid" }, s);
    txt(s, P.l - 8, y + 4, fNum(Math.round(maxY - (maxY / 4) * i)), { class: "ax", "text-anchor": "end" });
  }
  el("line", { x1: P.l, y1: H - P.b, x2: W - P.r, y2: H - P.b, class: "axline" }, s);
  for (let i = 0; i <= 4; i++) {
    const x = P.l + ((W - P.l - P.r) / 4) * i;
    txt(s, x, H - P.b + 17, fNum(Math.round((maxX / 4) * i)), { class: "ax", "text-anchor": "middle" });
  }
  txt(s, W - P.r, H - 6, "마스터 표시가 (만원)", { class: "ax", "text-anchor": "end" });
  txt(s, P.l - 44, P.t + 4, "실계약가", { class: "ax" });

  // 상관이 있었다면 그려졌을 선 (참고선)
  el("line", { x1: X(0), y1: Y(0), x2: X(maxX), y2: Y(maxY), stroke: "var(--tx-3)", "stroke-width": 1, "stroke-dasharray": "4 4", opacity: .5 }, s);
  txt(s, X(maxX) - 6, Y(maxY) + 16, "상관이 있었다면", { class: "ax", "text-anchor": "end", opacity: .7 });

  xs.forEach(t => {
    const cx = X(ps.listed_med[t]), cy = Y(ps.type_ref[t]);
    const c = el("circle", { cx, cy, r: 8, fill: "var(--leak)", opacity: .85, class: "hit" }, s);
    bindTip(c, `<b>${t}형</b>${trow("마스터 표시가", fMan(ps.listed_med[t]))}${trow("실계약가 중앙값", fMan(ps.type_ref[t]))}${trow("캘리브레이션 계수", "×" + ps.cal[t])}`);
    txt(s, cx, cy - 14, t, { class: "vlabel", "text-anchor": "middle", "font-size": 10.5 });
  });
}

/* ── 처리 로그 테이블 ─────────────────────────────── */
function renderLog() {
  const tbl = $("#log-table"); if (!tbl) return;
  const draw = () => {
    const q = ($("#log-search").value || "").toLowerCase();
    const hi = $("#log-high").checked;
    const rows = LOGRULES.filter(r =>
      (!hi || r.severity === "high") &&
      (!q || [r.table, r.field, r.action, r.note, r.rule].join(" ").toLowerCase().includes(q)));
    $("#log-count").textContent = `${rows.length} / ${LOGRULES.length} 규칙`;
    tbl.innerHTML =
      `<thead><tr><th>테이블</th><th>컬럼</th><th>처리 내용 · 근거</th><th style="text-align:right">건수</th></tr></thead><tbody>` +
      rows.map(r => `<tr class="${r.severity === "high" ? "hi" : ""}">
        <td><span class="tag">${r.table}</span></td>
        <td><code>${r.field}</code></td>
        <td>${r.action}
          ${r.note ? `<div class="note">${r.note}</div>` : ""}
          ${(r.samples || []).length ? `<div class="smp">${r.samples.map(s => `<i>${esc(s.before)}</i> → <b>${esc(s.after)}</b>`).join("<br>")}</div>` : ""}
        </td>
        <td class="n">${fNum(r.count)}</td></tr>`).join("") + "</tbody>";
  };
  $("#log-search").addEventListener("input", draw);
  $("#log-high").addEventListener("change", draw);
  draw();
}
const esc = s => String(s).replace(/[<>&]/g, c => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[c]));

/* ── 02 누수 워터폴 ───────────────────────────────── */
function renderLeak() {
  const box = $("#c-waterfall"); box.innerHTML = "";
  const items = [{ key: "rev", label: "계약 완료 매출", amount: D.kpi.revenue_done, win: true }, ...D.leaks];
  const W = cw(box, 860), H = 330, P = { t: 26, r: 20, b: 74, l: 62 };
  const s = svg(W, H); box.appendChild(s);
  const max = niceMax(Math.max(...items.map(i => i.amount)));
  const iw = (W - P.l - P.r) / items.length, bw = Math.min(iw * .58, 78);
  const Y = v => H - P.b - (v / max) * (H - P.t - P.b);

  for (let i = 0; i <= 4; i++) {
    const y = P.t + ((H - P.t - P.b) / 4) * i;
    el("line", { x1: P.l, y1: y, x2: W - P.r, y2: y, class: "grid" }, s);
    txt(s, P.l - 8, y + 4, fEok(max - (max / 4) * i, 0), { class: "ax", "text-anchor": "end" });
  }
  items.forEach((it, i) => {
    const cx = P.l + iw * i + iw / 2, y = Y(it.amount), h = H - P.b - y;
    const g = el("g", { class: "hit" }, s);
    el("rect", { x: cx - bw / 2, y, width: bw, height: Math.max(h, 2), rx: 5,
      fill: it.win ? "var(--win)" : "var(--leak)", opacity: it.win ? .9 : .82 }, g);
    txt(g, cx, y - 9, fEok(it.amount), { class: "vlabel", "text-anchor": "middle",
      fill: it.win ? "var(--win)" : "var(--leak)" });
    const words = it.label.split(" ");
    words.forEach((w, j) => txt(g, cx, H - P.b + 19 + j * 14, w, { class: "ax", "text-anchor": "middle" }));
    bindTip(g, `<b>${it.label}</b>${trow("금액", fEok(it.amount))}${it.count ? trow("건수", fNum(it.count) + "건") : ""}${it.kind ? trow("성격", it.kind) : ""}`);
    if (!it.win) g.addEventListener("click", () => {
      const card = $(`#leak-${it.key}`);
      card.scrollIntoView({ behavior: "smooth", block: "center" });
      card.classList.add("flash"); setTimeout(() => card.classList.remove("flash"), 1400);
    });
  });
  txt(s, P.l, H - 6, `단위: 억원 · 좌측 초록 막대가 실제로 번 돈, 나머지 빨강이 새는 돈 (합계 ${fEok(D.kpi.leak_total)})`, { class: "ax" });

  $("#leak-cards").innerHTML = D.leaks.map(l => `
    <div class="leak-card" id="leak-${l.key}">
      <div class="kind">${l.kind}</div>
      <div class="amt">${fEok(l.amount)}<small>${l.count ? ` · ${fNum(l.count)}건` : ""}</small></div>
      <div class="lb">${l.label}</div>
      <dl>
        <div><dt>근거 데이터</dt><dd>${l.basis}</dd></div>
        <div><dt>제안 액션</dt><dd class="act">${l.action}</dd></div>
      </dl>
    </div>`).join("");
}

/* ── 03 퍼널 ──────────────────────────────────────── */
function renderFunnel() {
  const box = $("#c-funnel"); box.innerHTML = "";
  const f = D.funnel.filter(x => x.stage !== "광고 리드");
  const W = cw(box, 520), rh = 62, H = f.length * rh + 20;
  const s = svg(W, H); box.appendChild(s);
  const L = 92, R = W - 20, max = f[0].value;
  f.forEach((st, i) => {
    const y = i * rh + 10, w = (st.value / max) * (R - L);
    txt(s, L - 12, y + 24, st.stage, { class: "ax", "text-anchor": "end", "font-size": 12.5, fill: "var(--tx-2)" });
    const g = el("g", { class: "hit" }, s);
    el("rect", { x: L, y, width: Math.max(w, 3), height: 34, rx: 6,
      fill: i === f.length - 1 ? "var(--win)" : "var(--acc)", opacity: 1 - i * .12 }, g);
    txt(g, L + 12, y + 22, fNum(st.value) + "명", { class: "vlabel", fill: "#fff" });
    bindTip(g, `<b>${st.stage}</b>${trow("인원", fNum(st.value) + "명")}${trow("첫 단계 대비", fPct(st.value / max * 100))}<div style="margin-top:6px;color:var(--tx-3)">${st.note}</div>`);
    if (i < f.length - 1) {
      const drop = st.value - f[i + 1].value;
      const dp = drop / st.value * 100;
      txt(s, L + 8, y + 50, `▼ ${fNum(drop)}명 이탈 (${fPct(dp)})`, { class: "ax", fill: "var(--leak)", "font-size": 11.5 });
    }
  });

  const worst = (() => {
    let best = null;
    for (let i = 0; i < f.length - 1; i++) {
      const d = f[i].value - f[i + 1].value;
      if (!best || d > best.d) best = { d, from: f[i], to: f[i + 1] };
    }
    return best;
  })();
  $("#funnel-notes").innerHTML = `
    <div class="snote">
      <div class="t">가장 큰 이탈 구간</div>
      <div class="big">${worst.from.stage} → ${worst.to.stage}</div>
      <div class="d">${fNum(worst.d)}명이 여기서 사라집니다 (${fPct(worst.d / worst.from.value * 100)}).
        상담까지 온 사람을 계약희망으로 못 만드는 것보다, <b>계약희망까지 만든 사람을 계약으로 못 만드는 것</b>이 훨씬 비쌉니다.</div>
    </div>
    <div class="snote good">
      <div class="t">계약희망 → 계약 전환율</div>
      <div class="big">${fPct(D.kpi.conv_rate)}</div>
      <div class="d">이 리포트의 모든 기대매출 추정에 쓰인 기준값입니다. 임의 가정이 아니라 <b>실측치</b>입니다.</div>
    </div>
    <div class="snote">
      <div class="t">광고 리드는 왜 뺐나</div>
      <div class="d">광고비 표의 리드 ${fNum(D.ad_leads)}건은 고객 마스터 ${fNum(D.kpi.customers)}명과 1:1로 대응하지 않습니다
        (클릭·문의 등 원시 반응으로 추정). 대응 관계가 불명확한 수치를 퍼널 첫 단계에 넣으면
        모든 전환율이 0.4%대로 왜곡되므로 <b>참고값으로 분리</b>했습니다.</div>
    </div>
    <div class="snote">
      <div class="t">상담 결과 분포</div>
      <div class="d">${D.outcomes.map(o => `${o.outcome} ${fNum(o.count)}`).join(" · ")}</div>
    </div>`;
}

/* ── 월별 콤보 ────────────────────────────────────── */
function renderMonthly() {
  const box = $("#c-monthly"); box.innerHTML = "";
  const m = D.monthly;
  const W = cw(box, 860), H = 300, P = { t: 18, r: 56, b: 46, l: 44 };
  const s = svg(W, H); box.appendChild(s);
  const maxC = niceMax(Math.max(...m.map(x => x.consults)));
  const maxR = niceMax(Math.max(...m.map(x => x.revenue)));
  const iw = (W - P.l - P.r) / m.length, bw = Math.min(iw * .62, 22);
  const Y = v => H - P.b - (v / maxC) * (H - P.t - P.b);
  const Y2 = v => H - P.b - (v / maxR) * (H - P.t - P.b);

  for (let i = 0; i <= 4; i++) {
    const y = P.t + ((H - P.t - P.b) / 4) * i;
    el("line", { x1: P.l, y1: y, x2: W - P.r, y2: y, class: "grid" }, s);
    txt(s, P.l - 8, y + 4, fNum(Math.round(maxC - (maxC / 4) * i)), { class: "ax", "text-anchor": "end" });
    txt(s, W - P.r + 8, y + 4, fEok(maxR - (maxR / 4) * i, 0), { class: "ax", fill: "var(--win)" });
  }
  m.forEach((x, i) => {
    const cx = P.l + iw * i + iw / 2;
    const g = el("g", { class: "hit" }, s);
    el("rect", { x: cx - bw / 2, y: Y(x.consults), width: bw, height: H - P.b - Y(x.consults), rx: 3, fill: "var(--acc)", opacity: .32 }, g);
    el("rect", { x: cx - bw / 2, y: Y(x.hopes), width: bw, height: H - P.b - Y(x.hopes), rx: 3, fill: "var(--acc)", opacity: .95 }, g);
    el("rect", { x: cx - iw / 2, y: P.t, width: iw, height: H - P.t - P.b, fill: "transparent" }, g);
    bindTip(g, `<b>${x.month}</b>${trow("상담", fNum(x.consults) + "건")}${trow("계약희망", fNum(x.hopes) + "건")}${trow("신규 계약", fNum(x.contracts) + "건")}${trow("해지", fNum(x.cancels) + "건")}${trow("완료 매출", fEok(x.revenue))}`);
    if (i % 3 === 0) txt(s, cx, H - P.b + 17, x.month.slice(2), { class: "ax", "text-anchor": "middle", "font-size": 10 });
  });
  const pts = m.map((x, i) => `${P.l + iw * i + iw / 2},${Y2(x.revenue)}`).join(" ");
  el("polyline", { points: pts, fill: "none", stroke: "var(--win)", "stroke-width": 2, "stroke-linejoin": "round" }, s);
  m.forEach((x, i) => el("circle", { cx: P.l + iw * i + iw / 2, cy: Y2(x.revenue), r: 2.4, fill: "var(--win)" }, s));

  $("#mon-legend").innerHTML =
    `<span><i style="background:var(--acc);opacity:.32"></i>월별 상담</span>
     <span><i style="background:var(--acc)"></i>그중 계약희망</span>
     <span><i style="background:var(--win)"></i>계약 완료 매출 (우축, 억원)</span>`;
}

/* ── 04 채널 ROI ──────────────────────────────────── */
function renderROI() {
  const box = $("#c-channel"); box.innerHTML = "";
  const ch = D.channels.filter(c => c.roas !== null);
  const W = cw(box, 520), H = 340, P = { t: 22, r: 24, b: 48, l: 48 };
  const s = svg(W, H); box.appendChild(s);
  const maxX = niceMax(Math.max(...ch.map(c => c.spend)));
  const maxY = niceMax(Math.max(...ch.map(c => c.roas)));
  const X = v => P.l + (v / maxX) * (W - P.l - P.r);
  const Y = v => H - P.b - (v / maxY) * (H - P.t - P.b);
  const maxN = Math.max(...ch.map(c => c.contracts));

  for (let i = 0; i <= 4; i++) {
    const y = P.t + ((H - P.t - P.b) / 4) * i;
    el("line", { x1: P.l, y1: y, x2: W - P.r, y2: y, class: "grid" }, s);
    txt(s, P.l - 8, y + 4, (maxY - (maxY / 4) * i).toFixed(0), { class: "ax", "text-anchor": "end" });
  }
  for (let i = 0; i <= 4; i++) {
    const x = P.l + ((W - P.l - P.r) / 4) * i;
    txt(s, x, H - P.b + 17, fEok((maxX / 4) * i, 0), { class: "ax", "text-anchor": "middle" });
  }
  el("line", { x1: P.l, y1: Y(D.median_roas), x2: W - P.r, y2: Y(D.median_roas), stroke: "var(--warn)", "stroke-dasharray": "5 4", "stroke-width": 1 }, s);
  txt(s, W - P.r, Y(D.median_roas) - 7, `ROAS 중앙값 ${D.median_roas}`, { class: "ax", "text-anchor": "end", fill: "var(--warn)" });
  txt(s, W - P.r, H - 6, "누적 광고 지출 (억원)", { class: "ax", "text-anchor": "end" });
  txt(s, P.l - 38, P.t - 6, "ROAS", { class: "ax" });

  ch.forEach(c => {
    const cx = X(c.spend), cy = Y(c.roas), r = 9 + (c.contracts / maxN) * 16;
    const good = c.roas >= D.median_roas;
    const g = el("g", { class: "hit" }, s);
    el("circle", { cx, cy, r, fill: good ? "var(--win)" : "var(--leak)", opacity: .3 }, g);
    el("circle", { cx, cy, r, fill: "none", stroke: good ? "var(--win)" : "var(--leak)", "stroke-width": 1.8 }, g);
    txt(g, cx, cy - r - 8, c.channel, { class: "vlabel", "text-anchor": "middle", "font-size": 11 });
    bindTip(g, `<b>${c.channel}</b>${trow("지출", fEok(c.spend))}${trow("계약 매출", fEok(c.revenue))}${trow("ROAS", c.roas)}${trow("계약", fNum(c.contracts) + "건")}${trow("계약당 획득비용", fMan(c.cac))}${trow("리드당 비용", c.cpl + "만원")}`);
  });

  const best = ch.reduce((a, b) => a.roas > b.roas ? a : b);
  const worst = ch.reduce((a, b) => a.roas < b.roas ? a : b);
  const spread = Math.max(...ch.map(c => c.spend)) / Math.min(...ch.map(c => c.spend));
  $("#roi-notes").innerHTML = `
    <div class="snote good">
      <div class="t">최고 효율</div>
      <div class="big" style="color:var(--win)">${best.channel}</div>
      <div class="d">ROAS <b>${best.roas}</b> · 계약 ${best.contracts}건 · 계약당 획득비용 <b>${fMan(best.cac)}</b>.
        지출은 6개 채널 중 ${D.channels.filter(c => c.spend > best.spend).length + 1}위인데 계약은 압도적 1위입니다.</div>
    </div>
    <div class="snote">
      <div class="t">최저 효율</div>
      <div class="big">${worst.channel}</div>
      <div class="d">ROAS <b>${worst.roas}</b> · 계약 ${worst.contracts}건 · 계약당 <b>${fMan(worst.cac)}</b>.
        ${best.channel} 대비 계약 한 건을 따는 데 <b>${(worst.cac / best.cac).toFixed(1)}배</b>의 돈이 듭니다.</div>
    </div>
    <div class="snote">
      <div class="t">예산은 거의 균등하게 뿌려져 있음</div>
      <div class="d">최대·최소 지출 채널의 격차가 <b>${spread.toFixed(2)}배</b>에 불과합니다.
        성과 격차가 ${(best.roas / worst.roas).toFixed(1)}배인데 예산은 사실상 n분의 1로 나눈 상태 —
        이것이 <b>${fEok(D.leaks.find(l => l.key === "adwaste").amount)}</b>의 저효율 지출로 이어집니다.</div>
    </div>`;
}

/* ── 예산 재배분 시뮬레이터 ───────────────────────── */
function renderSim() {
  const ch = D.channels.filter(c => c.roas !== null);
  const best = ch.reduce((a, b) => a.roas > b.roas ? a : b);
  const donors = ch.filter(c => c.roas < D.median_roas);
  const baseRev = ch.reduce((s, c) => s + c.revenue, 0);
  const baseSpend = ch.reduce((s, c) => s + c.spend, 0);

  const draw = () => {
    const p = +$("#sim-range").value / 100;
    $("#sim-out").textContent = Math.round(p * 100) + "%";
    let moved = 0, rev = 0;
    const rows = ch.map(c => {
      let spend = c.spend;
      if (donors.includes(c)) { const m = Math.round(c.spend * p); spend -= m; moved += m; }
      return { c, spend };
    });
    rows.forEach(r => { if (r.c === best) r.spend += moved; });
    rows.forEach(r => { r.rev = Math.round(r.spend * r.c.roas); rev += r.rev; });
    const gain = rev - baseRev;

    $("#sim-result").innerHTML = `
      <div><div class="v">${fEok(baseRev)}</div><div class="k">현재 계약 매출</div></div>
      <div><div class="v up">${fEok(rev)}</div><div class="k">재배분 후 예상 매출</div></div>
      <div><div class="v ${gain >= 0 ? "up" : ""}">${gain >= 0 ? "+" : ""}${fEok(gain)}</div><div class="k">증분 (동일 예산 ${fEok(baseSpend)})</div></div>
      <div><div class="v">${fEok(moved)}</div><div class="k">${best.channel}(으)로 이전되는 예산</div></div>`;

    $("#sim-table").innerHTML =
      `<thead><tr><th>채널</th><th>ROAS</th><th>현재 지출</th><th>조정 후</th><th>증감</th><th>예상 매출</th></tr></thead><tbody>` +
      rows.sort((a, b) => b.c.roas - a.c.roas).map(r => {
        const d = r.spend - r.c.spend;
        return `<tr>
          <td>${r.c.channel}${r.c === best ? ' <span class="pill act">수혜</span>' : donors.includes(r.c) ? ' <span class="pill out">감액</span>' : ""}</td>
          <td class="d">${r.c.roas}</td>
          <td>${fEok(r.c.spend)}</td>
          <td>${fEok(r.spend)}</td>
          <td class="d ${d > 0 ? "plus" : d < 0 ? "minus" : ""}">${d === 0 ? "—" : (d > 0 ? "+" : "") + fEok(d)}</td>
          <td>${fEok(r.rev)}</td></tr>`;
      }).join("") + "</tbody>";
  };
  $("#sim-range").addEventListener("input", draw);
  draw();
}

/* ── 05 재고 vs 수요 ──────────────────────────────── */
function renderStock() {
  const box = $("#c-types"); box.innerHTML = "";
  const t = [...D.types].sort((a, b) => (b.demand_per_stock || 0) - (a.demand_per_stock || 0));
  const W = cw(box, 860), rh = 46, H = t.length * rh + 26;
  const s = svg(W, H); box.appendChild(s);
  const mid = W / 2, gap = 52, maxD = Math.max(...t.map(x => x.demand)), maxS = Math.max(...t.map(x => x.unsold + x.hold));
  t.forEach((x, i) => {
    const y = i * rh + 12;
    const wd = (x.demand / maxD) * Math.max(mid - gap - 20, 40);
    const stock = x.unsold + x.hold;
    const ws = (stock / maxS) * Math.max(mid - gap - 130, 40);
    const g1 = el("g", { class: "hit" }, s);
    el("rect", { x: mid - gap / 2 - wd, y, width: wd, height: 22, rx: 4, fill: "var(--acc)", opacity: .85 }, g1);
    txt(g1, mid - gap / 2 - wd - 8, y + 16, fNum(x.demand), { class: "ax", "text-anchor": "end", fill: "var(--acc)" });
    bindTip(g1, `<b>${x.type}형 대기 수요</b>${trow("관심 등록 고객", fNum(x.demand) + "명")}`);

    txt(s, mid, y + 16, x.type, { class: "vlabel", "text-anchor": "middle" });

    const g2 = el("g", { class: "hit" }, s);
    el("rect", { x: mid + gap / 2, y, width: Math.max(ws, 2), height: 22, rx: 4, fill: "var(--leak)", opacity: .8 }, g2);
    txt(g2, mid + gap / 2 + ws + 8, y + 16, `${stock}세대 · ${fEok(x.stock_value)}`, { class: "ax", fill: "var(--leak)" });
    bindTip(g2, `<b>${x.type}형 재고</b>${trow("미계약", x.unsold + "세대")}${trow("보류", x.hold + "세대")}${trow("평가액", fEok(x.stock_value))}${trow("소진율", fPct(x.sold_rate))}${trow("1세대당 대기 수요", (x.demand_per_stock ?? "—") + "명")}`);
  });
  txt(s, mid - gap / 2, H - 4, "◀ 대기 수요 (명)", { class: "ax", "text-anchor": "end" });
  txt(s, mid + gap / 2, H - 4, "남은 재고 ▶", { class: "ax" });
  $("#type-legend").innerHTML =
    `<span><i style="background:var(--acc)"></i>해당 타입 관심 고객</span><span><i style="background:var(--leak)"></i>미계약+보류 세대 및 평가액</span>`;

  // 소진율
  const b2 = $("#c-sold"); b2.innerHTML = "";
  const ts = [...D.types].sort((a, b) => b.sold_rate - a.sold_rate);
  const W2 = cw(b2, 440), rh2 = 34, H2 = ts.length * rh2 + 8;
  const s2 = svg(W2, H2); b2.appendChild(s2);
  const L2 = 52, R2 = W2 - 56;
  ts.forEach((x, i) => {
    const y = i * rh2 + 6;
    txt(s2, L2 - 10, y + 15, x.type, { class: "ax", "text-anchor": "end" });
    el("rect", { x: L2, y: y + 3, width: R2 - L2, height: 16, rx: 4, fill: "#ffffff0d" }, s2);
    const w = (x.sold_rate / 100) * (R2 - L2);
    const g = el("g", { class: "hit" }, s2);
    el("rect", { x: L2, y: y + 3, width: w, height: 16, rx: 4, fill: x.sold_rate >= 50 ? "var(--win)" : "var(--warn)" }, g);
    txt(g, R2 + 8, y + 15, fPct(x.sold_rate, 0), { class: "ax" });
    bindTip(g, `<b>${x.type}형</b>${trow("전체", x.total + "세대")}${trow("계약완료", x.sold + "세대")}${trow("소진율", fPct(x.sold_rate))}${trow("평균 실계약가", fMan(x.avg_price))}`);
  });

  // 지역
  const b3 = $("#c-region"); b3.innerHTML = "";
  const rg = D.regions.slice(0, 12);
  const W3 = cw(b3, 440), rh3 = 26, H3 = rg.length * rh3 + 10;
  const s3 = svg(W3, H3); b3.appendChild(s3);
  const L3 = 46, R3 = W3 - 92, mx = Math.max(...rg.map(r => r.customers));
  rg.forEach((r, i) => {
    const y = i * rh3 + 6;
    txt(s3, L3 - 10, y + 12, r.region, { class: "ax", "text-anchor": "end" });
    const w = (r.customers / mx) * (R3 - L3);
    const g = el("g", { class: "hit" }, s3);
    el("rect", { x: L3, y, width: w, height: 14, rx: 3, fill: "var(--acc)", opacity: .5 }, g);
    el("rect", { x: L3, y, width: w * (r.rate / 100) * 2.2, height: 14, rx: 3, fill: "var(--win)", opacity: .9 }, g);
    txt(g, R3 + 8, y + 12, `${r.customers}명 · ${fPct(r.rate, 0)}`, { class: "ax" });
    bindTip(g, `<b>${r.region}</b>${trow("고객 수", fNum(r.customers) + "명")}${trow("계약 도달", fNum(r.won) + "명")}${trow("전환율", fPct(r.rate))}`);
  });
}

/* ── 06 사람 ──────────────────────────────────────── */
function renderPeople() {
  const b = $("#c-reps"); b.innerHTML = "";
  const r = D.reps;
  const W = cw(b, 440), rh = 30, H = r.length * rh + 10;
  const s = svg(W, H); b.appendChild(s);
  const L = 62, R = W - 74, mx = Math.max(...r.map(x => x.win_rate));
  const avg = r.reduce((a, x) => a + x.win_rate, 0) / r.length;
  el("line", { x1: L + (avg / mx) * (R - L), y1: 2, x2: L + (avg / mx) * (R - L), y2: H - 8, stroke: "var(--warn)", "stroke-dasharray": "4 3" }, s);
  r.forEach((x, i) => {
    const y = i * rh + 6;
    txt(s, L - 10, y + 13, x.consultant, { class: "ax", "text-anchor": "end" });
    const w = (x.win_rate / mx) * (R - L);
    const g = el("g", { class: "hit" }, s);
    el("rect", { x: L, y, width: Math.max(w, 2), height: 16, rx: 4, fill: x.win_rate >= avg ? "var(--win)" : "var(--leak)", opacity: .82 }, g);
    txt(g, L + w + 8, y + 13, fPct(x.win_rate), { class: "ax" });
    bindTip(g, `<b>${x.consultant}</b>${trow("담당 고객", fNum(x.customers) + "명")}${trow("계약 도달", fNum(x.won) + "명")}${trow("계약 전환율", fPct(x.win_rate))}${trow("상담 건수", fNum(x.consults) + "건")}${trow("계약희망 비율", fPct(x.hope_rate))}${trow("평균 상담시간", x.avg_min + "분")}`);
  });
  txt(s, L + (avg / mx) * (R - L) + 5, H - 1, `평균 ${fPct(avg)}`, { class: "ax", fill: "var(--warn)" });

  const b2 = $("#c-sreps"); b2.innerHTML = "";
  const sr = D.sales_reps;
  const W2 = cw(b2, 440), rh2 = 30, H2 = sr.length * rh2 + 10;
  const s2 = svg(W2, H2); b2.appendChild(s2);
  const L2 = 62, R2 = W2 - 92, mxr = Math.max(...sr.map(x => x.revenue)), mxc = Math.max(...sr.map(x => x.cancel_rate));
  sr.forEach((x, i) => {
    const y = i * rh2 + 6;
    txt(s2, L2 - 10, y + 13, x.rep, { class: "ax", "text-anchor": "end" });
    const w = (x.revenue / mxr) * (R2 - L2);
    const g = el("g", { class: "hit" }, s2);
    el("rect", { x: L2, y, width: Math.max(w, 2), height: 16, rx: 4, fill: "var(--acc)", opacity: .6 }, g);
    const cx = L2 + (x.cancel_rate / mxc) * (R2 - L2);
    el("circle", { cx, cy: y + 8, r: 4.5, fill: "var(--leak)" }, g);
    txt(g, R2 + 8, y + 13, `${fEok(x.revenue)} · 해지 ${fPct(x.cancel_rate, 0)}`, { class: "ax" });
    bindTip(g, `<b>${x.rep}</b>${trow("완료 매출", fEok(x.revenue))}${trow("총 계약", fNum(x.total) + "건")}${trow("완료", fNum(x.done) + "건")}${trow("해지", fNum(x.cancel) + "건")}${trow("해지율", fPct(x.cancel_rate))}`);
  });
}

/* ── 07 인사이트 ──────────────────────────────────── */
function renderInsights() {
  $("#insight-list").innerHTML = D.insights.map(i => `
    <article class="ins">
      <div class="ins-side">
        <div class="id">${i.id}</div>
        <div class="tag">${i.tag}</div>
        <div class="metric">${i.metric}</div>
      </div>
      <div class="ins-body">
        <div class="ins-row f"><div class="lbl">발견</div><div class="val">${i.finding}</div></div>
        <div class="ins-row e"><div class="lbl">근거 데이터</div><div class="val">${i.evidence}</div></div>
        <div class="ins-row a"><div class="lbl">제안 액션</div><div class="val">${i.action}</div></div>
      </div>
    </article>`).join("");
}

/* ── 08 액션 큐 ───────────────────────────────────── */
function renderQueue() {
  $("#q-all").textContent = fNum(D.queue_all);
  $("#q-val").textContent = fEok(D.kpi.queue_value);

  const parts = D.queue[0].parts;
  const maxes = { "계약희망 이력": 40, "최근 접촉": 20, "고객 등급": 15, "예산 여력": 15, "상담 몰입도": 10, "고객 상태": 10, "거절 이력": "−8/회" };
  $("#score-chips").innerHTML = Object.entries(maxes).map(([k, v]) =>
    `<div class="chip ${k === "거절 이력" ? "neg" : ""}">${k}<b>${typeof v === "number" ? "최대 " + v : v}</b></div>`).join("");

  let filter = "all", q = "";
  const tbl = $("#q-table");
  const draw = () => {
    const rows = D.queue.filter(r => {
      if (filter === "VIP" && !["VIP", "VVIP"].includes(r.grade)) return false;
      if (filter === "활성" && r.status !== "활성") return false;
      if (filter === "휴면" && r.status !== "휴면") return false;
      if (filter === "hope2" && r.hope < 2) return false;
      if (q && ![r.name, r.region, r.interest_type, r.grade, r.lead_source].join(" ").toLowerCase().includes(q)) return false;
      return true;
    });
    tbl.innerHTML =
      `<thead><tr><th>#</th><th>고객</th><th>등급</th><th>상태</th><th>관심</th><th>희망</th><th>최근접촉</th><th>점수</th><th>추천 액션</th></tr></thead><tbody>` +
      (rows.length ? rows.map(r => {
        const i = D.queue.indexOf(r) + 1;
        return `<tr data-id="${r.customer_id}">
          <td class="rank">${String(i).padStart(2, "0")}</td>
          <td class="nm">${r.name} <span class="rank">${r.customer_id}</span></td>
          <td><span class="pill ${["VIP", "VVIP"].includes(r.grade) ? "vip" : ""}">${r.grade}</span></td>
          <td><span class="pill ${r.status === "활성" ? "act" : r.status === "휴면" ? "dor" : "out"}">${r.status}</span></td>
          <td>${r.interest_type}</td>
          <td>${r.hope}회</td>
          <td>${r.days_since}일 전</td>
          <td><span class="sc">${r.score}</span><span class="bar"><i style="width:${r.score}%"></i></span></td>
          <td class="act">${r.action}</td></tr>`;
      }).join("") : `<tr><td colspan="9" style="padding:26px;text-align:center;color:var(--tx-3)">조건에 맞는 고객이 없습니다.</td></tr>`) + "</tbody>";

    $$("#q-table tbody tr[data-id]").forEach(tr => tr.addEventListener("click", () => {
      const next = tr.nextElementSibling;
      if (next && next.classList.contains("q-detail")) { next.remove(); return; }
      $$(".q-detail").forEach(d => d.remove());
      const r = D.queue.find(x => x.customer_id === tr.dataset.id);
      const d = document.createElement("tr");
      d.className = "q-detail";
      d.innerHTML = `<td colspan="9">
        <div class="dgrid">${Object.entries(r.parts).map(([k, v]) =>
          `<div class="dg ${v < 0 ? "neg" : ""}">${k}<b>${v > 0 ? "+" : ""}${v}</b></div>`).join("")}
          <div class="dg" style="border-color:var(--acc)">합계<b>${r.score}</b></div></div>
        <div class="dmeta">
          연락처 ${r.phone || "미등록"} · ${r.region} · 유입 ${r.lead_source} ·
          총 상담 ${r.consults}회 ${r.total_min}분 · 예산 ${r.budget ? fMan(r.budget) + ` (${r.fit})` : "미기재"} ·
          기대 계약금액 <b>${fMan(r.exp_value)}</b><br>
          ${r.match_unit
            ? `추천 매물 <b>${r.match_unit}</b> (${r.interest_type}형 · ${fMan(r.match_price)}) — 관심타입 중 예산 내 최상위 미계약 세대`
            : `예산 내 <b>${r.interest_type}형 미계약 세대 없음</b> — 인접 타입 대안 제시 필요`}
        </div></td>`;
      tr.after(d);
    }));
  };
  $("#q-search").addEventListener("input", e => { q = e.target.value.toLowerCase(); draw(); });
  $$("#q-filter button").forEach(b => b.addEventListener("click", () => {
    $$("#q-filter button").forEach(x => x.classList.remove("on"));
    b.classList.add("on"); filter = b.dataset.f; draw();
  }));
  draw();
}

function renderMethod() { $("#m-conv").textContent = fPct(D.kpi.conv_rate); }

/* ── 네비 ─────────────────────────────────────────── */
function wireNav() {
  const secs = $$("section[id], header[id]");
  const links = $$("#nav a[href^='#']");
  const onScroll = () => {
    const h = document.documentElement;
    $("#progress").style.width = (h.scrollTop / (h.scrollHeight - h.clientHeight) * 100) + "%";
    let cur = "";
    secs.forEach(s => { if (s.getBoundingClientRect().top <= 120) cur = s.id; });
    links.forEach(a => a.classList.toggle("active", a.getAttribute("href") === "#" + cur));
  };
  addEventListener("scroll", onScroll, { passive: true });
  onScroll();
}
