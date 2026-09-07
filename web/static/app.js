/* ボリュームチェック図 Web のフロントエンド。
 *
 * 計算は一切しない。地図で矩形を描き、フォームの値を /api/solve に投げて
 * 返ってきた断面図SVGと面積表を表示するだけ。 */
"use strict";

const $ = (sel) => document.querySelector(sel);
const form = $("#form");

// ---------------------------------------------------------------------------
// 地図
// ---------------------------------------------------------------------------
const map = L.map("map", { preferCanvas: true }).setView([35.6812, 139.7671], 17);

const gsi = L.tileLayer(
  "https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png",
  { maxZoom: 18, attribution: "地理院タイル（淡色地図）" }
).addTo(map);
const gsiPhoto = L.tileLayer(
  "https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg",
  { maxZoom: 18, attribution: "地理院タイル（写真）" }
);
const osm = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors",
});
L.control.layers({ "地理院 淡色": gsi, "地理院 写真": gsiPhoto, OpenStreetMap: osm }).addTo(map);
L.control.scale({ imperial: false }).addTo(map);

// コンテナのサイズが変わるとタイルがずれるので、Leaflet に再計測させる
new ResizeObserver(() => map.invalidateSize()).observe(document.getElementById("map"));

let rectLayer = null;   // 確定した敷地矩形
let drawing = false;    // 描画モード中か
let dragStart = null;   // ドラッグ開始のLatLng
let ghost = null;       // ドラッグ中のプレビュー

function setDrawing(on) {
  drawing = on;
  $("#btn-draw").classList.toggle("active", on);
  $("#btn-draw").textContent = on ? "描画中（ドラッグ／Escで中止）" : "敷地の矩形を描く";
  map.dragging[on ? "disable" : "enable"]();
  map.getContainer().style.cursor = on ? "crosshair" : "";
  $("#map-hint").textContent = on
    ? "地図上でドラッグして敷地の矩形を描いてください。"
    : "矩形の辺は南北・東西を向きます。道路の方位で間口/奥行が決まります。";
}

$("#btn-draw").addEventListener("click", () => setDrawing(!drawing));
$("#btn-clear").addEventListener("click", () => {
  if (rectLayer) { map.removeLayer(rectLayer); rectLayer = null; }
  setDrawing(false);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && drawing) {
    if (ghost) { map.removeLayer(ghost); ghost = null; }
    dragStart = null;
    setDrawing(false);
  }
});

map.on("mousedown", (e) => {
  if (!drawing) return;
  dragStart = e.latlng;
  if (ghost) map.removeLayer(ghost);
  ghost = L.rectangle(L.latLngBounds(dragStart, dragStart), {
    color: "#1d4ed8", weight: 2, dashArray: "5 4", fillOpacity: 0.08,
  }).addTo(map);
});

map.on("mousemove", (e) => {
  if (!drawing || !dragStart) return;
  ghost.setBounds(L.latLngBounds(dragStart, e.latlng));
});

map.on("mouseup", (e) => {
  if (!drawing || !dragStart) return;
  const bounds = L.latLngBounds(dragStart, e.latlng);
  dragStart = null;
  if (ghost) { map.removeLayer(ghost); ghost = null; }
  setDrawing(false);
  // 数ピクセルのクリックは無視する
  const p1 = map.latLngToLayerPoint(bounds.getNorthWest());
  const p2 = map.latLngToLayerPoint(bounds.getSouthEast());
  if (Math.abs(p1.x - p2.x) < 8 || Math.abs(p1.y - p2.y) < 8) return;
  applyRect(bounds);
});

function applyRect(bounds) {
  if (rectLayer) map.removeLayer(rectLayer);
  rectLayer = L.rectangle(bounds, {
    color: "#1d4ed8", weight: 2, fillOpacity: 0.1,
  }).addTo(map);
  updateFromRect(true);
}

/** 矩形から間口・奥行を求めてフォームへ。初回は用途地域も引く。 */
async function updateFromRect(lookupZoning) {
  if (!rectLayer) return;
  const b = rectLayer.getBounds();
  const body = {
    south: b.getSouth(), west: b.getWest(), north: b.getNorth(), east: b.getEast(),
    road_side: form.road_side.value,
  };
  const res = await fetch("/api/rect", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) return;
  const data = await res.json();
  form.frontage.value = data.frontage;
  form.depth.value = data.depth;
  $("#site-area-hint").textContent =
    `地図から取得: 敷地面積 ${data.area_m2.toLocaleString()} m2`;
  if (lookupZoning) await lookupZoningAt(data.center.lat, data.center.lon);
  scheduleSolve();
}

// ---------------------------------------------------------------------------
// 住所検索（国土地理院 住所検索API をバックエンド経由で）
// ---------------------------------------------------------------------------
async function search() {
  const q = $("#q").value.trim();
  if (!q) return;
  const res = await fetch(`/api/geocode?q=${encodeURIComponent(q)}`);
  const data = await res.json();
  if (!data.results || !data.results.length) {
    $("#map-hint").textContent = "該当する住所が見つかりませんでした。";
    return;
  }
  const hit = data.results[0];
  map.setView([hit.lat, hit.lon], 18);
  $("#map-hint").textContent = `移動: ${hit.title}`;
}
$("#btn-search").addEventListener("click", search);
$("#q").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); search(); } });

// ---------------------------------------------------------------------------
// 用途地域の自動判定
// ---------------------------------------------------------------------------
async function lookupZoningAt(lat, lon) {
  const badge = $("#zoning-source");
  const hint = $("#zoning-hint");
  badge.textContent = "判定中…";
  try {
    const res = await fetch(`/api/zoning?lat=${lat}&lon=${lon}`);
    const z = await res.json();
    if (z.district) {
      form.use_district.value = z.district;
      if (z.bcr) form.bcr.value = Math.round(z.bcr * 100);
      if (z.far) form.far_designated.value = Math.round(z.far * 100);
      badge.textContent = z.source === "reinfolib" ? "自動判定（不動産情報ライブラリ）" : "自動判定（ローカルデータ）";
      hint.textContent = "自動判定した値です。実際の都市計画情報で必ず確認してください。";
    } else {
      badge.textContent = "手入力";
      hint.textContent = z.detail || "自動判定できませんでした。手入力してください。";
    }
  } catch (e) {
    badge.textContent = "手入力";
    hint.textContent = `自動判定に失敗しました: ${e}`;
  }
}

// ---------------------------------------------------------------------------
// フォーム → API
// ---------------------------------------------------------------------------
function payload() {
  const num = (name) => parseFloat(form[name].value);
  const limit = form.height_limit_absolute.value.trim();
  return {
    site: {
      frontage: num("frontage"), depth: num("depth"),
      road_width: num("road_width"), road_side: form.road_side.value,
    },
    zoning: {
      use_district: form.use_district.value,
      bcr: num("bcr") / 100,
      far_designated: num("far_designated") / 100,
      height_limit_absolute: limit === "" ? null : parseFloat(limit),
    },
    program: {
      floor_height: num("floor_height"), gf_height: num("gf_height"),
      wall_setback: num("wall_setback"), core_ratio: num("core_ratio") / 100,
      max_floors: parseInt(form.max_floors.value, 10),
    },
  };
}

let solveTimer = null;
let lastPayload = null;

function scheduleSolve() {
  clearTimeout(solveTimer);
  solveTimer = setTimeout(solve, 250);
}

async function solve() {
  if (!form.reportValidity()) return;
  const body = payload();
  lastPayload = body;
  $("#status").textContent = "計算中…";
  try {
    const res = await fetch("/api/solve", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail ? JSON.stringify(err.detail) : `HTTP ${res.status}`);
    }
    render(await res.json());
    showError(null);
    $("#btn-dxf").disabled = false;
  } catch (e) {
    showError(String(e.message || e));
    $("#btn-dxf").disabled = true;
  } finally {
    $("#status").textContent = "";
  }
}

function showError(msg) {
  const el = $("#error");
  el.hidden = !msg;
  el.textContent = msg || "";
}

// ---------------------------------------------------------------------------
// 表示
// ---------------------------------------------------------------------------
const pct = (v) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(0)}%`);
const n2 = (v) => v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function render(data) {
  $("#svg").innerHTML = data.svg;

  const s = data.summary;
  const cards = [
    ["階数", `${s.floor_count} 階`, ""],
    ["最高高さ", `${n2(s.max_height_m)}`, "m"],
    ["延床面積", `${n2(s.total_gross_area_m2)}`, "m2"],
    ["容積対象床面積", `${n2(s.total_far_area_m2)}`, `m2 / 上限 ${n2(s.max_far_area_m2)}`],
    ["建築面積", `${n2(s.building_area_m2)}`, `m2 / 上限 ${n2(s.max_building_area_m2)}`],
    ["実効容積率", pct(s.far_effective),
      s.far_by_road === null ? "道路幅員の低減なし" : `道路幅員による ${pct(s.far_by_road)}`],
    ["達成容積率", pct(s.far_achieved), `建蔽率 ${pct(s.bcr_achieved)}`],
    ["貸室面積", `${n2(s.total_rentable_area_m2)}`, "m2"],
  ];
  $("#summary").innerHTML = cards
    .map(([k, v, sub]) => `<div><dt>${k}</dt><dd>${v}${sub ? ` <small>${sub}</small>` : ""}</dd></div>`)
    .join("");

  $("#stop-reason").textContent = s.stop_reason
    ? `打ち切り理由: ${s.stop_reason} — ${s.stop_detail}` : "";

  const head = `<thead><tr><th>階</th><th>階高(m)</th><th>間口(m)</th><th>奥行(m)</th>
    <th>床面積(m2)</th><th>容積対象(m2)</th><th>累計(m2)</th><th class="l">支配規定</th></tr></thead>`;
  const rows = data.floors.map((f) => `<tr>
      <td>${f.floor}F</td><td>${f.story_height_m.toFixed(2)}</td>
      <td>${f.width_m.toFixed(2)}</td><td>${f.depth_m.toFixed(2)}</td>
      <td>${n2(f.area_m2)}</td><td>${n2(f.far_area_m2)}</td><td>${n2(f.cumulative_far_area_m2)}</td>
      <td class="l">${f.governing}</td></tr>`).join("");
  const foot = data.floors.length
    ? `<tfoot><tr><td>合計</td><td>—</td><td>—</td><td>—</td>
        <td>${n2(s.total_gross_area_m2)}</td><td>${n2(s.total_far_area_m2)}</td>
        <td>${n2(s.total_far_area_m2)}</td><td class="l"></td></tr></tfoot>`
    : "";
  $("#areas").innerHTML = data.floors.length
    ? head + `<tbody>${rows}</tbody>` + foot
    : `<tbody><tr><td class="l">建築可能な階が成立しません。</td></tr></tbody>`;

  $("#rules").innerHTML =
    `<h3>適用した規定と根拠値</h3><ul>${data.applied_rules
      .map((r) => `<li>${r.label}：${r.value} <span class="basis">［${r.basis}］</span></li>`)
      .join("")}</ul>` +
    `<h3>未考慮事項（いずれも安全側）</h3><ul>${data.notes
      .map((n) => `<li>${n}</li>`).join("")}</ul>`;
}

// ---------------------------------------------------------------------------
// DXF ダウンロード
// ---------------------------------------------------------------------------
$("#btn-dxf").addEventListener("click", async () => {
  if (!lastPayload) return;
  $("#status").textContent = "DXF を生成中…";
  try {
    const res = await fetch("/api/dxf", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(lastPayload),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "volume_check.dxf";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    $("#status").textContent = "DXF をダウンロードしました。";
  } catch (e) {
    showError(`DXFの生成に失敗しました: ${e.message || e}`);
    $("#status").textContent = "";
  }
});

// ---------------------------------------------------------------------------
// 初期化
// ---------------------------------------------------------------------------
form.addEventListener("input", scheduleSolve);
form.road_side.addEventListener("change", () => updateFromRect(false));

(async function init() {
  const meta = await (await fetch("/api/use-districts")).json();
  form.use_district.innerHTML = meta.districts
    .map((d) => `<option${d === "商業地域" ? " selected" : ""}>${d}</option>`).join("");

  const lookup = meta.zoning_lookup;
  $("#zoning-source").textContent = lookup.reinfolib_enabled
    ? "自動判定：利用可"
    : (lookup.local_datasets.length ? "自動判定：ローカルデータ" : "自動判定：未設定");

  // 低層住専系で絶対高さ制限が空欄なら既定値が使われることを伝える
  form.use_district.addEventListener("change", () => {
    const required = meta.absolute_height_limit_required.includes(form.use_district.value);
    $("#zoning-hint").textContent = required && !form.height_limit_absolute.value
      ? `${form.use_district.value} は法55条の絶対高さ制限がかかります。`
        + `空欄のままだと既定の ${meta.default_absolute_height_limit_m}m で試算します。`
      : "";
  });

  setDrawing(false);
  solve();
})();
