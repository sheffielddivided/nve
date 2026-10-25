// Felles kode for index.html og data.html: områder, datalasting og
// sammenstilling av magasinfylling og priser per uke.
'use strict';

const LOCAL_DATA   = 'data.json';
const LOCAL_PRICES = 'prices.json';
const API          = 'https://biapi.nve.no/magasinstatistikk/api/Magasinstatistikk/HentOffentligData';
const CURRENT_YEAR = new Date().getFullYear();
const HIST_YEARS   = 20;
const PRICE_AREAS  = ['NO1', 'NO2', 'NO3', 'NO4', 'NO5'];

const REGIONS = [
  { key: 'LAND', label: 'Norge', name: 'Hele Norge',        match: d => d.omrType !== 'EL' },
  { key: 'NO1',  label: 'NO1',   name: 'NO1 – Østlandet',  match: d => d.omrType === 'EL' && d.omrnr === 1 },
  { key: 'NO2',  label: 'NO2',   name: 'NO2 – Sørlandet',  match: d => d.omrType === 'EL' && d.omrnr === 2 },
  { key: 'NO3',  label: 'NO3',   name: 'NO3 – Midt-Norge', match: d => d.omrType === 'EL' && d.omrnr === 3 },
  { key: 'NO4',  label: 'NO4',   name: 'NO4 – Nord-Norge', match: d => d.omrType === 'EL' && d.omrnr === 4 },
  { key: 'NO5',  label: 'NO5',   name: 'NO5 – Vestlandet', match: d => d.omrType === 'EL' && d.omrnr === 5 },
];

// X-axis: week number → month label (show label at first week of each month)
const MONTH_LABEL = {
   1:'Jan',  5:'Feb',  9:'Mar', 14:'Apr', 18:'Mai', 23:'Jun',
  27:'Jul', 31:'Aug', 36:'Sep', 40:'Okt', 45:'Nov', 49:'Des',
};

// ── Normalise fill percentage ─────────────────────────────────
// API may return fyllingsgrad as fraction (0–1) or percent (0–100).
// Detect scale once from first batch of non-zero historical rows.
let pctScale = null;

function detectScale(rows) {
  if (pctScale !== null) return;
  const sample = rows.filter(r => r.fyllingsgrad > 0).slice(0, 200);
  if (!sample.length) return;
  const maxVal = Math.max(...sample.map(r => r.fyllingsgrad));
  pctScale = maxVal <= 1 ? 100 : 1;
}

function calcPct(row) {
  const fg = row.fyllingsgrad;
  if (fg != null && fg !== 0 && pctScale !== null) {
    return fg * pctScale;
  }
  // Fallback: derive from energy values
  if (row.fylling_TWh > 0 && row.kapasitet_TWh > 0) {
    return (row.fylling_TWh / row.kapasitet_TWh) * 100;
  }
  return null;
}

// ── Median helper ─────────────────────────────────────────────
function median(arr) {
  if (!arr.length) return null;
  const s = [...arr].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

// ── Filter & sort rows for selected region ────────────────────
function getRows(allData, key) {
  const cfg = REGIONS.find(r => r.key === key);
  return allData
    .filter(cfg.match)
    .sort((a, b) => (a.iso_aar - b.iso_aar) || (a.iso_uke - b.iso_uke));
}

// ── Auto-detect total-Norway aggregation ──────────────────────
// After data is loaded, find the (omrType, omrnr) pair outside the
// five EL price areas that has the most rows. That pair is the true
// whole-country total, and we update the LAND match function to it.
function detectLandFilter(allData) {
  const elNums = new Set([1, 2, 3, 4, 5]);
  const counts = {};
  for (const d of allData) {
    if (d.omrType === 'EL' && elNums.has(d.omrnr)) continue;
    const k = `${d.omrType}|${d.omrnr}`;
    counts[k] = (counts[k] || 0) + 1;
  }
  const entries = Object.entries(counts);
  if (!entries.length) return;
  const [best] = entries.sort((a, b) => b[1] - a[1]);
  const [bestType, bestNrStr] = best[0].split('|');
  const bestNr = Number(bestNrStr);
  const landRegion = REGIONS.find(r => r.key === 'LAND');
  if (landRegion) landRegion.match = d => d.omrType === bestType && d.omrnr === bestNr;
}

// ── Normalår: median per uke over de siste HIST_YEARS årene ───
function normalByWeek(rows) {
  const histFrom = CURRENT_YEAR - HIST_YEARS;
  const hist = {};
  for (const r of rows) {
    if (r.iso_aar < histFrom || r.iso_aar >= CURRENT_YEAR) continue;
    const pct = calcPct(r);
    if (pct == null || pct < 0) continue;
    (hist[r.iso_uke] ??= []).push(pct);
  }
  const out = {};
  for (const w in hist) out[w] = median(hist[w]);
  return out;
}

// ── Prices (ENTSO-E day-ahead, ukesnitt) ──────────────────────
// For «Norge» brukes et enkelt snitt av NO1–NO5 for uker der alle områdene har pris.
function getPriceRows(priceData, key) {
  const areas = priceData?.omrader;
  if (!areas) return [];
  if (areas[key]) return areas[key];
  const byWeek = {};
  for (const n of PRICE_AREAS) {
    for (const r of areas[n] ?? []) {
      const k = `${r.iso_aar}-${r.iso_uke}`;
      (byWeek[k] ??= { iso_aar: r.iso_aar, iso_uke: r.iso_uke, sum: 0, n: 0 });
      byWeek[k].sum += r.pris;
      byWeek[k].n++;
    }
  }
  return Object.values(byWeek)
    .filter(w => w.n === PRICE_AREAS.length)
    .map(w => ({ iso_aar: w.iso_aar, iso_uke: w.iso_uke, pris: w.sum / w.n }))
    .sort((a, b) => (a.iso_aar - b.iso_aar) || (a.iso_uke - b.iso_uke));
}

function priceMap(rows) {
  const m = new Map();
  for (const r of rows) m.set(`${r.iso_aar}-${r.iso_uke}`, r.pris);
  return m;
}

// ── Én rad per uke med fylling, avvik fra normalår og priser ──
function buildWeekTable(allData, priceData, key) {
  const rows   = getRows(allData, key);
  detectScale(rows);
  const normal = normalByWeek(rows);
  const pris   = priceMap(getPriceRows(priceData, key));
  const prisDE  = priceMap(priceData?.referanse?.['DE-LU'] ?? []);
  const prisSE2 = priceMap(priceData?.referanse?.SE2 ?? []);
  return rows.map(r => {
    const k   = `${r.iso_aar}-${r.iso_uke}`;
    const pct = calcPct(r);
    const nrm = normal[r.iso_uke];
    const p   = pris.get(k) ?? null;
    const de  = prisDE.get(k) ?? null;
    return {
      iso_aar: r.iso_aar,
      iso_uke: r.iso_uke,
      dato:    r.dato_Id,
      pct,
      twh:     r.fylling_TWh ?? null,
      cap:     r.kapasitet_TWh ?? null,
      endring: r.endring_fyllingsgrad != null && pctScale ? r.endring_fyllingsgrad * pctScale : null,
      avvik:   pct != null && nrm != null ? pct - nrm : null,
      pris:    p,
      prisDE:  de,
      prisSE2: prisSE2.get(k) ?? null,
    };
  });
}

// ── Bootstrap ────────────────────────────────────────────────
async function fetchData(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status} – ${res.statusText}`);
  const data = await res.json();
  if (!Array.isArray(data) || !data.length) throw new Error('Tom respons');
  return data;
}

async function loadReservoirData() {
  let data;
  try {
    data = await fetchData(LOCAL_DATA);
  } catch (localErr) {
    // Lokal fil mangler eller er ugyldig – fall tilbake til NVE sitt API direkte.
    data = await fetchData(API);
  }
  detectLandFilter(data);
  return data;
}

// Prisene er valgfrie: mangler fila, vises sidene uten priser.
async function fetchPrices() {
  try {
    const res = await fetch(LOCAL_PRICES);
    if (!res.ok) return null;
    const data = await res.json();
    return data && data.omrader ? data : null;
  } catch {
    return null;
  }
}

function weekLabel(r) {
  return `Uke ${r.iso_uke}, ${r.iso_aar}`;
}
