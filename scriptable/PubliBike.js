// Variables used by Scriptable.
// These must be at the very top of the file. Do not edit.
// icon-color: teal; icon-glyph: bicycle;

// PubliBike widget for Scriptable (https://scriptable.app).
// Live e-bike ⚡ and bike 🚲 counts at your favourite PubliBike / Velospot stations,
// nearest first. Works as a home screen widget (small, medium, large) and on the
// lock screen (rectangular, inline, circular).
//
// Favourites: list station IDs below, or set the widget's Parameter to a
// comma-separated list (e.g. "1154,1163") to show different stations per widget.
// Run the script inside Scriptable and pick "Find station ID" to look IDs up.

const FAVORITES = ["1154", "1163", "1107"]; // Stapferstube, ETH Leonhardstrasse, ETH / Naturhistorisches Museum
const USE_LOCATION = true; // sort by distance; false keeps the order above

const LIVE_URL = "https://velospot.info/customer/public/api/pbvsng/stations";
const INFO_URL = "https://api.mobidata-bw.de/sharing/gbfs/v3/velospot_ch/station_information";
const INFO_MAX_AGE = 24 * 3600 * 1000;

const C = {
  bg: Color.dynamic(new Color("#ffffff"), new Color("#1c1c1e")),
  text: Color.dynamic(new Color("#111111"), new Color("#f2f2f7")),
  dim: Color.dynamic(new Color("#8e8e93"), new Color("#8e8e93")),
  ebike: Color.dynamic(new Color("#d97706"), new Color("#fbbf24")),
  bike: Color.dynamic(new Color("#0d9488"), new Color("#2dd4bf")),
  zero: Color.dynamic(new Color("#c7c7cc"), new Color("#48484a")),
};

const fm = FileManager.local();
const cacheDir = fm.joinPath(fm.cacheDirectory(), "publibike");
if (!fm.fileExists(cacheDir)) fm.createDirectory(cacheDir, true);
const cachePath = (name) => fm.joinPath(cacheDir, name);

function readCache(name) {
  const p = cachePath(name);
  if (!fm.fileExists(p)) return null;
  try { return JSON.parse(fm.readString(p)); } catch { return null; }
}
const writeCache = (name, value) => fm.writeString(cachePath(name), JSON.stringify(value));

async function getJSON(url) {
  const req = new Request(url);
  req.timeoutInterval = 15;
  req.headers = { "User-Agent": "PubliBikeWidget/1.0", Accept: "application/json" };
  return req.loadJSON();
}

// { id: [name, lat, lon] }, cached for a day.
async function loadInfo() {
  const cached = readCache("info.json");
  if (cached && Date.now() - cached.at < INFO_MAX_AGE) return cached.stations;
  try {
    const json = await getJSON(INFO_URL);
    const stations = {};
    for (const s of json.data.stations) {
      const id = s.station_id.split(":").pop();
      stations[id] = [s.name?.[0]?.text ?? `Station ${id}`, s.lat, s.lon];
    }
    writeCache("info.json", { at: Date.now(), stations });
    return stations;
  } catch (e) {
    return cached?.stations ?? {};
  }
}

// { id: [eBikes, bikes, outOfService] }; falls back to the last good copy.
async function loadLive() {
  try {
    const list = await getJSON(LIVE_URL);
    const stations = {};
    for (const s of list) {
      let e = 0, b = 0;
      // Vehicle types: 1 bike, 2 e-bike, 3 bike with own battery, 4 cargo, 5 e-scooter.
      for (const v of s.available_vehicles ?? []) {
        if (v.type === 2) e += v.count;
        else if (v.type === 1 || v.type === 3) b += v.count;
      }
      stations[s.id] = [e, b, s.out_of_service === true];
    }
    const result = { at: Date.now(), stations, stale: false };
    writeCache("live.json", result);
    return result;
  } catch (e) {
    const cached = readCache("live.json");
    return cached ? { ...cached, stale: true } : { at: null, stations: {}, stale: true };
  }
}

// Current location, or the last known one if it's slow or unavailable.
async function loadLocation() {
  if (!USE_LOCATION) return null;
  try {
    Location.setAccuracyToHundredMeters();
    const timeout = new Promise((_, reject) => Timer.schedule(6000, false, () => reject("timeout")));
    const loc = await Promise.race([Location.current(), timeout]);
    writeCache("location.json", { latitude: loc.latitude, longitude: loc.longitude });
    return loc;
  } catch (e) {
    return readCache("location.json");
  }
}

function distance(a, lat, lon) {
  const r = Math.PI / 180;
  const dLat = (lat - a.latitude) * r, dLon = (lon - a.longitude) * r;
  const h = Math.sin(dLat / 2) ** 2 +
    Math.cos(a.latitude * r) * Math.cos(lat * r) * Math.sin(dLon / 2) ** 2;
  return 2 * 6371000 * Math.asin(Math.sqrt(h));
}

function formatDistance(d) {
  if (d == null) return "";
  if (d < 1000) return `${Math.round(d / 10) * 10} m`;
  return d < 10000 ? `${(d / 1000).toFixed(1)} km` : `${Math.round(d / 1000)} km`;
}

// "Comerge - Zürich" -> ["Comerge", "Zürich"]
function splitName(name) {
  const i = name.lastIndexOf(" - ");
  return i < 0 ? [name, ""] : [name.slice(0, i), name.slice(i + 3)];
}

async function loadStations(ids) {
  const [info, live, here] = await Promise.all([loadInfo(), loadLive(), loadLocation()]);
  const stations = ids.map((id) => {
    const [name, lat, lon] = info[id] ?? [`Station ${id}`, null, null];
    const [title, place] = splitName(name);
    const [eBikes, bikes, outOfService] = live.stations[id] ?? [0, 0, true];
    const dist = here && lat != null ? distance(here, lat, lon) : null;
    return { id, title, place, eBikes, bikes, outOfService, dist };
  });
  if (here) stations.sort((a, b) => (a.dist ?? Infinity) - (b.dist ?? Infinity));
  return { stations, updated: live.at ? new Date(live.at) : null, stale: live.stale };
}

// MARK: Drawing

function symbol(stack, name, size, color) {
  const img = stack.addImage(SFSymbol.named(name).image);
  img.imageSize = new Size(size, size);
  img.tintColor = color;
  return img;
}

function count(stack, sym, n, color, size, width) {
  const s = stack.addStack();
  s.centerAlignContent();
  if (width) s.size = new Size(width, 0);
  symbol(s, sym, size * 0.8, n > 0 ? color : C.zero);
  s.addSpacer(2);
  const t = s.addText(String(n));
  t.font = Font.boldRoundedSystemFont(size);
  t.textColor = n > 0 ? C.text : C.zero;
  t.lineLimit = 1;
  if (width) s.addSpacer();
}

function counts(stack, st, size, width) {
  if (st.outOfService) {
    const t = stack.addText("out of service");
    t.font = Font.systemFont(size * 0.7);
    t.textColor = C.dim;
    return;
  }
  count(stack, "bolt.fill", st.eBikes, C.ebike, size, width);
  stack.addSpacer(width ? 0 : size * 0.5);
  count(stack, "bicycle", st.bikes, C.bike, size, width);
}

function footer(w, data) {
  const t = w.addText(data.updated
    ? `${data.stale ? "⚠︎ " : ""}${data.updated.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`
    : "no data");
  t.font = Font.systemFont(10);
  t.textColor = data.stale ? C.ebike : C.dim;
}

function smallWidget(w, data) {
  const [st, ...rest] = data.stations;
  const title = w.addText(st.title);
  title.font = Font.semiboldSystemFont(14);
  title.textColor = C.text;
  title.lineLimit = 2;
  title.minimumScaleFactor = 0.8;
  const sub = w.addText([formatDistance(st.dist), st.place].filter(Boolean).join(" · "));
  sub.font = Font.systemFont(11);
  sub.textColor = C.dim;
  sub.lineLimit = 1;
  w.addSpacer();
  const row = w.addStack();
  row.centerAlignContent();
  counts(row, st, 26);
  w.addSpacer();
  if (rest[0]) {
    const next = w.addStack();
    next.centerAlignContent();
    const t = next.addText(rest[0].title);
    t.font = Font.systemFont(11);
    t.textColor = C.dim;
    t.lineLimit = 1;
    next.addSpacer(4);
    counts(next, rest[0], 11);
    w.addSpacer(2);
  }
  footer(w, data);
}

function listWidget(w, data, max) {
  const head = w.addStack();
  head.centerAlignContent();
  symbol(head, "bicycle", 14, C.bike);
  head.addSpacer(4);
  const h = head.addText("PubliBike");
  h.font = Font.semiboldSystemFont(13);
  h.textColor = C.text;
  head.addSpacer();
  footer(head, data);
  w.addSpacer(6);

  for (const st of data.stations.slice(0, max)) {
    const row = w.addStack();
    row.centerAlignContent();
    const left = row.addStack();
    left.layoutVertically();
    const t = left.addText(st.title);
    t.font = Font.semiboldSystemFont(14);
    t.textColor = C.text;
    t.lineLimit = 1;
    const sub = left.addText([formatDistance(st.dist), st.place].filter(Boolean).join(" · "));
    sub.font = Font.systemFont(11);
    sub.textColor = C.dim;
    sub.lineLimit = 1;
    row.addSpacer();
    counts(row, st, 18, 44);
    w.addSpacer(6);
  }
  w.addSpacer();
}

function rectangularWidget(w, data) {
  const st = data.stations[0];
  const t = w.addText(st.title);
  t.font = Font.semiboldSystemFont(13);
  t.lineLimit = 1;
  const row = w.addStack();
  row.centerAlignContent();
  if (st.outOfService) {
    row.addText("out of service").font = Font.systemFont(14);
  } else {
    for (const [sym, n] of [["bolt.fill", st.eBikes], ["bicycle", st.bikes]]) {
      symbol(row, sym, 14, Color.white());
      row.addSpacer(3);
      row.addText(String(n)).font = Font.boldRoundedSystemFont(20);
      row.addSpacer(10);
    }
  }
  const d = w.addText(formatDistance(st.dist) || st.place);
  d.font = Font.systemFont(11);
}

function inlineWidget(w, data) {
  const st = data.stations[0];
  w.addText(st.outOfService
    ? `${st.title}: out of service`
    : `⚡${st.eBikes} 🚲${st.bikes} ${st.title}`);
}

function circularWidget(w, data) {
  const st = data.stations[0];
  w.addAccessoryWidgetBackground = true;
  for (const [sym, n] of [["bolt.fill", st.eBikes], ["bicycle", st.bikes]]) {
    const row = w.addStack();
    row.addSpacer();
    row.centerAlignContent();
    symbol(row, sym, 11, Color.white());
    row.addSpacer(2);
    row.addText(String(n)).font = Font.boldRoundedSystemFont(15);
    row.addSpacer();
  }
}

async function buildWidget(family) {
  const ids = (args.widgetParameter ?? "").split(",").map((s) => s.trim()).filter(Boolean);
  const data = await loadStations(ids.length ? ids : FAVORITES);
  const w = new ListWidget();
  w.refreshAfterDate = new Date(Date.now() + 5 * 60 * 1000);

  if (!data.stations.length) {
    w.addText("Add station IDs to FAVORITES or the widget parameter.").font = Font.systemFont(12);
    return w;
  }

  if (family.startsWith("accessory")) {
    if (family === "accessoryInline") inlineWidget(w, data);
    else if (family === "accessoryCircular") circularWidget(w, data);
    else rectangularWidget(w, data);
    return w;
  }

  w.backgroundColor = C.bg;
  w.setPadding(14, 14, 12, 14);
  if (family === "small") smallWidget(w, data);
  else listWidget(w, data, family === "medium" ? 3 : 7);
  return w;
}

// MARK: In-app: preview and station search

async function findStation() {
  const ask = new Alert();
  ask.title = "Find station";
  ask.addTextField("Name or address, e.g. Stapferstube");
  ask.addAction("Search");
  ask.addCancelAction("Cancel");
  if ((await ask.present()) === -1) return;

  const fold = (s) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  const q = fold(ask.textFieldValue(0)).split(/\s+/).filter(Boolean);
  const info = await loadInfo();
  const matches = Object.entries(info)
    .filter(([, [name]]) => q.every((w) => fold(name).includes(w)))
    .sort((a, b) => a[1][0].localeCompare(b[1][0]))
    .slice(0, 50);

  const table = new UITable();
  table.showSeparators = true;
  if (!matches.length) {
    const row = new UITableRow();
    row.addText("No stations found");
    table.addRow(row);
  }
  for (const [id, [name]] of matches) {
    const row = new UITableRow();
    row.addText(name, `ID ${id} · tap to copy`);
    row.dismissOnSelect = true;
    row.onSelect = () => Pasteboard.copy(id);
    table.addRow(row);
  }
  await table.present();
}

if (config.runningInWidget) {
  Script.setWidget(await buildWidget(config.widgetFamily ?? "small"));
} else {
  const menu = new Alert();
  menu.title = "PubliBike";
  for (const s of ["Small", "Medium", "Large"]) menu.addAction(`Preview ${s.toLowerCase()} widget`);
  menu.addAction("Find station ID");
  menu.addCancelAction("Close");
  const choice = await menu.present();
  if (choice === 3) await findStation();
  else if (choice >= 0) {
    const family = ["small", "medium", "large"][choice];
    const w = await buildWidget(family);
    await w[`present${family[0].toUpperCase()}${family.slice(1)}`]();
  }
}
Script.complete();
