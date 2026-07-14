const PARTY_COLORS = {
  PiS: "#004098",
  KO: "#e30613",
  "Trzecia Droga": "#f7941d",
  Lewica: "#e1001a",
  Konfederacja: "#582c07",
  "Polska jest Jedna": "#00a651",
  Bezpartyjni: "#666666",
};

const OBWODY_MIN_ZOOM = 9; // tylko próg do jakiego "Pokaż obwody"/wyszukiwarka podskakują, nie do widoczności warstw

const COUNTRY_LEVELS = ["wojewodztwa", "powiaty", "gminy"];
const ALL_LEVELS = [...COUNTRY_LEVELS, "obwody"];

const QUALITY_LABELS = {
  official: "granice oficjalne (MSIP)",
  generated: "granice wygenerowane — dobra jakość",
  approximate: "granice wygenerowane — przybliżone",
};

const QUALITY_CLASSES = {
  official: "quality-official",
  generated: "quality-generated",
  approximate: "quality-approximate",
};

let map;
let manifest;
let currentElection = null; // wpis z manifest.elections
let currentMetric = "frekwencja";
let currentLevel = "gminy"; // wybrany poziom szczegółowości — niezależny od zoomu
let levelData = { wojewodztwa: null, powiaty: null, gminy: null }; // GeoJSON per poziom dla bieżących wyborów
let resultsIndex = {}; // "{teryt}_{obwod}" -> {frekwencja, winner, results, komisja, dzielnica}
let currentPopup = null;
let currentTilesFile = null; // plik .pmtiles aktualnie załadowany jako źródło "obwody"

function ensureObwodyLayer(tilesFile) {
  if (tilesFile === currentTilesFile) return;

  if (map.getLayer("obwody-fill")) map.removeLayer("obwody-fill");
  if (map.getLayer("obwody-line")) map.removeLayer("obwody-line");
  if (map.getSource("obwody")) map.removeSource("obwody");
  currentTilesFile = null;

  if (!tilesFile) return; // te wybory nie mają jeszcze żadnych poligonów obwodów

  map.addSource("obwody", {
    type: "vector",
    url: `pmtiles://data/${tilesFile}`,
    promoteId: "key",
  });
  map.addLayer(
    {
      id: "obwody-fill",
      type: "fill",
      source: "obwody",
      "source-layer": "obwody",
      layout: { visibility: currentLevel === "obwody" ? "visible" : "none" },
      paint: { "fill-color": "#cccccc", "fill-opacity": 0.8 },
    },
    "carto-labels",
  );
  map.addLayer(
    {
      id: "obwody-line",
      type: "line",
      source: "obwody",
      "source-layer": "obwody",
      layout: { visibility: currentLevel === "obwody" ? "visible" : "none" },
      paint: {
        "line-color": ["case", ["boolean", ["feature-state", "hover"], false], "#0f172a", "#94a3b8"],
        "line-width": ["case", ["boolean", ["feature-state", "hover"], false], 2.5, 0.7],
      },
    },
    "carto-labels",
  );
  currentTilesFile = tilesFile;
}

function setLevel(level) {
  currentLevel = level;
  ALL_LEVELS.forEach((lvl) => {
    if (!map.getLayer(`${lvl}-fill`)) return;
    const visibility = lvl === level ? "visible" : "none";
    map.setLayoutProperty(`${lvl}-fill`, "visibility", visibility);
    map.setLayoutProperty(`${lvl}-line`, "visibility", visibility);
  });
  applyStyles();
  renderStats();
  updateContext();
  updateHash();
}

function qualityBadge(quality) {
  if (!quality) return "";
  const label = QUALITY_LABELS[quality] || quality;
  const cls = QUALITY_CLASSES[quality] || "";
  return `<span class="quality-badge ${cls}">${label}</span>`;
}

function winnerColor(winner) {
  if (!winner) return "#cccccc";
  return PARTY_COLORS[winner] || colorFromString(winner);
}

// Sekwencyjna rampa frekwencji (ColorBrewer YlGnBu) — bezpieczna dla daltonistów,
// jasny (niska) → ciemny (wysoka). Interpolujemy w RGB między 6 przystankami; ta
// sama funkcja zasila frekwencjaColorStops() (warstwy mapy) i legendę CSS.
const TURNOUT_RAMP = [
  [255, 255, 204],
  [199, 233, 180],
  [127, 205, 187],
  [65, 182, 196],
  [44, 127, 184],
  [37, 52, 148],
];

function frekwencjaColor(value) {
  const clamped = Math.max(0, Math.min(1, value || 0));
  const scaled = clamped * (TURNOUT_RAMP.length - 1);
  const i = Math.floor(scaled);
  const t = scaled - i;
  const a = TURNOUT_RAMP[i];
  const b = TURNOUT_RAMP[Math.min(i + 1, TURNOUT_RAMP.length - 1)];
  const mix = (x, y) => Math.round(x + (y - x) * t);
  return `rgb(${mix(a[0], b[0])}, ${mix(a[1], b[1])}, ${mix(a[2], b[2])})`;
}

function colorFromString(input) {
  let hash = 0;
  for (let i = 0; i < input.length; i += 1) {
    hash = input.charCodeAt(i) + ((hash << 5) - hash);
  }
  return `hsl(${Math.abs(hash) % 360}, 55%, 45%)`;
}

function formatPercent(value) {
  if (value == null || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

function parseResults(raw) {
  if (!raw) return {};
  if (typeof raw === "object") return raw;
  try {
    return JSON.parse(raw);
  } catch {
    return {};
  }
}

function topResults(results, limit = 5) {
  return Object.entries(results)
    .sort((a, b) => b[1] - a[1])
    .slice(0, limit);
}

function winnerChip(winner) {
  if (!winner) return "";
  return `<span class="winner-chip"><span class="dot" style="background:${winnerColor(winner)}"></span>${winner}</span>`;
}

// Wyniki jako poziome słupki — szerokość ∝ udział w najlepszym wyniku, kolor
// z tej samej palety co warstwa "zwycięzca". Reużywa topResults()/winnerColor().
function resultBars(results, limit = 5) {
  const entries = topResults(results, limit);
  if (!entries.length) return "";
  const max = Math.max(...entries.map(([, votes]) => votes)) || 1;
  const rows = entries
    .map(([name, votes]) => {
      const pct = Math.round((votes / max) * 100);
      return `<div class="result-bar">
          <span class="result-bar-label">${name}</span>
          <span class="result-bar-value">${votes}</span>
          <span class="result-bar-track"><span class="result-bar-fill" style="width:${pct}%; background:${winnerColor(name)}"></span></span>
        </div>`;
    })
    .join("");
  return `<div class="result-bars">${rows}</div>`;
}

// ---------- wyszukiwarka gmin/miast ----------

let searchIndex = [];

function geometryBounds(geometry) {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  (function walk(coords) {
    if (typeof coords[0] === "number") {
      const [x, y] = coords;
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
    } else {
      coords.forEach(walk);
    }
  })(geometry.coordinates);
  return [minX, minY, maxX, maxY];
}

function buildSearchIndex() {
  searchIndex = [];
  // Wyszukiwarka zawsze indeksuje gminy, niezależnie od aktualnie wybranego
  // poziomu szczegółowości — to najdrobniejszy poziom krajowy i najbardziej
  // użyteczny do wyszukiwania konkretnego miejsca.
  (levelData.gminy?.features || []).forEach((f) => {
    if (!f.properties.nazwa) return;
    const [minX, minY, maxX, maxY] = geometryBounds(f.geometry);
    searchIndex.push({ label: f.properties.nazwa, center: [(minX + maxX) / 2, (minY + maxY) / 2], zoom: 11 });
  });
  (currentElection?.areas || []).forEach((area) => {
    searchIndex.push({ label: area.name, center: [area.center[1], area.center[0]], zoom: area.zoom });
  });

  const datalist = document.getElementById("search-list");
  const seen = new Set();
  datalist.innerHTML = searchIndex
    .filter((item) => (seen.has(item.label) ? false : seen.add(item.label)))
    .map((item) => `<option value="${item.label}"></option>`)
    .join("");
}

function handleSearch(query) {
  const match = searchIndex.find((item) => item.label.toLowerCase() === query.trim().toLowerCase());
  if (!match) return;
  closePopup();
  map.flyTo({ center: match.center, zoom: match.zoom });
}

function findAreaForTeryt(teryt) {
  if (!currentElection || !teryt) return null;
  return currentElection.areas.find((area) => area.teryt === teryt) || null;
}

// ---------- legenda / statystyki ----------

function renderLegend(metric, winners) {
  const legend = document.getElementById("legend");
  if (metric === "frekwencja") {
    legend.innerHTML = `
      <h2>Legenda</h2>
      <div class="gradient-legend">
        <span>Niska</span>
        <div class="gradient-bar"></div>
        <span>Wysoka</span>
      </div>
    `;
    return;
  }
  const parties = [...new Set(winners.filter(Boolean))].sort();
  legend.innerHTML = `
    <h2>Legenda</h2>
    <ul>
      ${parties
        .map(
          (party) => `
        <li>
          <span class="swatch" style="background:${winnerColor(party)}"></span>
          ${party}
        </li>`,
        )
        .join("")}
      <li><span class="swatch" style="background:#cccccc"></span> Brak danych</li>
    </ul>
  `;
}

const LEVEL_LABELS = {
  wojewodztwa: "województw",
  powiaty: "powiatów",
  gminy: "gmin",
  obwody: "obwodów",
};

function updateContext() {
  const backBtn = document.getElementById("back-to-country");
  const contextName = document.getElementById("context-name");
  if (currentLevel !== "obwody") {
    contextName.textContent = "Polska";
    backBtn.style.display = "none";
    return;
  }
  const visible = map.queryRenderedFeatures({ layers: ["obwody-fill"] });
  const teryty = new Set(visible.map((f) => f.properties.teryt));
  const area = teryty.size === 1 ? findAreaForTeryt([...teryty][0]) : null;
  contextName.innerHTML = area ? `${area.name} ${qualityBadge(area.quality)}` : "Polska";
  backBtn.style.display = "block";
}

function statRow(label, value, big = false) {
  return `<div class="stat-row"><span class="stat-label">${label}</span><span class="stat-value${
    big ? " big" : ""
  }">${value}</span></div>`;
}

function renderStats() {
  const stats = document.getElementById("stats");
  stats.classList.remove("is-loading");
  let html = "";

  if (currentLevel === "obwody") {
    const visible = map.queryRenderedFeatures({ layers: ["obwody-fill"] });
    const seen = new Set();
    let sumTurnout = 0;
    let withData = 0;
    visible.forEach((f) => {
      const key = f.properties.key;
      if (seen.has(key)) return;
      seen.add(key);
      const result = resultsIndex[key];
      if (result) {
        sumTurnout += result.frekwencja || 0;
        withData += 1;
      }
    });
    html =
      `<div class="card-title">Obwody na ekranie</div>` +
      statRow("Widoczne", seen.size) +
      statRow("Z wynikami", withData) +
      statRow("Śr. frekwencja", formatPercent(withData ? sumTurnout / withData : null), true);
  } else {
    const countryInfo = currentElection.country ? currentElection.country[currentLevel] : null;
    const features = levelData[currentLevel]?.features || [];
    const withResults = features.filter((f) => f.properties.winner);
    const avgTurnout =
      withResults.reduce((sum, f) => sum + (f.properties.frekwencja || 0), 0) / (withResults.length || 1);
    html = countryInfo
      ? `<div class="card-title">Widok: ${LEVEL_LABELS[currentLevel]}</div>` +
        statRow("Z wynikami", `${countryInfo.matched}/${countryInfo.total}`) +
        statRow("Śr. frekwencja", formatPercent(avgTurnout), true)
      : `<p>Brak danych krajowych na poziomie „${LEVEL_LABELS[currentLevel]}" dla tych wyborów.</p>`;
  }

  stats.innerHTML = html;
}

// ---------- popupy ----------

function closePopup() {
  if (currentPopup) {
    currentPopup.remove();
    currentPopup = null;
  }
}

const LEVEL_UNIT_LABELS = { wojewodztwa: "Województwo", powiaty: "Powiat", gminy: "Gmina" };

function showLevelPopup(feature, lngLat, level) {
  const props = feature.properties;
  const results = parseResults(props.results);
  // Poligon obszaru z obwodami (jeśli jest) ma sens jako drill-down tylko z
  // poziomu gmin — teryt powiatu/województwa nie odpowiada żadnemu areas[].teryt.
  const area = level === "gminy" ? findAreaForTeryt(props.teryt) : null;

  const node = document.createElement("div");
  node.className = "popup";
  node.innerHTML = `
    <h3>${props.nazwa ?? LEVEL_UNIT_LABELS[level]}</h3>
    ${props.winner ? winnerChip(props.winner) : ""}
    <p><strong>Frekwencja:</strong> ${formatPercent(props.frekwencja)}</p>
    <p><strong>Głosy ważne:</strong> ${props.glosy_wazne ?? "—"}</p>
    <p><strong>Obwody:</strong> ${props.obwody ?? "—"}</p>
    ${resultBars(results)}
    ${area ? `<button type="button" class="show-area-btn">Pokaż obwody →</button>` : ""}
    ${area ? qualityBadge(area.quality) : ""}
  `;
  if (area) {
    node.querySelector(".show-area-btn").addEventListener("click", () => {
      closePopup();
      const levelSelect = document.getElementById("level");
      levelSelect.value = "obwody";
      setLevel("obwody");
      map.flyTo({ center: [area.center[1], area.center[0]], zoom: Math.max(area.zoom, OBWODY_MIN_ZOOM + 1) });
    });
  }

  closePopup();
  currentPopup = new maplibregl.Popup().setLngLat(lngLat).setDOMContent(node).addTo(map);
}

function showObwodPopup(feature, lngLat) {
  const props = feature.properties;
  const result = resultsIndex[props.key] || {};
  const results = parseResults(result.results);

  const html = `
    <div class="popup">
      <h3>Obwód ${props.obwod ?? "?"}</h3>
      ${result.dzielnica ? `<p><em>${result.dzielnica}</em></p>` : ""}
      ${result.winner ? winnerChip(result.winner) : ""}
      <p><strong>Frekwencja:</strong> ${formatPercent(result.frekwencja)}</p>
      <p><strong>Głosy ważne:</strong> ${result.glosy_wazne ?? "—"}</p>
      ${result.komisja ? `<p><strong>Komisja:</strong> ${result.komisja}</p>` : ""}
      ${resultBars(results)}
      ${
        result.wyborcy || result.opis_granic
          ? `<details class="popup-details">
              <summary>Więcej szczegółów</summary>
              ${result.wyborcy ? `<p><strong>Liczba wyborców:</strong> ${result.wyborcy}</p>` : ""}
              ${result.opis_granic ? `<p><strong>Opis granic (PKW):</strong> ${result.opis_granic}</p>` : ""}
            </details>`
          : ""
      }
      ${
        props.quality !== "official"
          ? `<p class="quality-note">${qualityBadge(props.quality)}<br>Kształt tego obwodu jest przybliżony (wygenerowany z opisu granic PKW i punktów adresowych, nie z oficjalnego geoportalu).</p>`
          : ""
      }
    </div>
  `;
  closePopup();
  currentPopup = new maplibregl.Popup().setLngLat(lngLat).setHTML(html).addTo(map);
}

// ---------- stylowanie warstw ----------

// MapLibre interpoluje kolory liniowo w RGB między przystankami, a nie przez
// obrót odcieniem HSL jak dawny wzór frekwencjaColor — dlatego generujemy wiele
// pośrednich przystanków obliczonych tym samym wzorem, żeby RGB-interpolacja
// między sąsiednimi (bliskimi) przystankami dobrze przybliżała łuk HSL.
function frekwencjaColorStops() {
  const stops = [];
  for (let i = 0; i <= 10; i += 1) {
    const v = i / 10;
    stops.push(v, frekwencjaColor(v));
  }
  return stops;
}

function winnerFillExpression(winners) {
  const unique = [...new Set(winners.filter(Boolean))];
  if (unique.length === 0) return "#cccccc";
  const stops = [];
  unique.forEach((name) => {
    stops.push(name, winnerColor(name));
  });
  return ["match", ["get", "winner"], ...stops, "#cccccc"];
}

function winnerFeatureStateExpression(winners) {
  const unique = [...new Set(winners.filter(Boolean))];
  if (unique.length === 0) return "#cccccc";
  const stops = [];
  unique.forEach((name) => {
    stops.push(name, winnerColor(name));
  });
  return ["match", ["coalesce", ["feature-state", "winner"], ""], ...stops, "#cccccc"];
}

function applyLevelStyle(level) {
  if (!map.getLayer(`${level}-fill`)) return;
  const features = levelData[level]?.features || [];
  const fillColor =
    currentMetric === "frekwencja"
      ? ["interpolate", ["linear"], ["coalesce", ["get", "frekwencja"], 0], ...frekwencjaColorStops()]
      : winnerFillExpression(features.map((f) => f.properties.winner));
  map.setPaintProperty(`${level}-fill`, "fill-color", fillColor);
}

function applyObwodyStyle() {
  if (!map.getLayer("obwody-fill")) return; // te wybory nie mają jeszcze poligonów obwodów
  if (currentMetric === "frekwencja") {
    map.setPaintProperty("obwody-fill", "fill-color", [
      "case",
      ["==", ["coalesce", ["feature-state", "frekwencja"], -1], -1],
      "#cccccc",
      ["interpolate", ["linear"], ["feature-state", "frekwencja"], ...frekwencjaColorStops()],
    ]);
  } else {
    const winners = Object.values(resultsIndex)
      .map((r) => r.winner)
      .filter(Boolean);
    map.setPaintProperty("obwody-fill", "fill-color", winnerFeatureStateExpression(winners));
  }
}

function applyStyles() {
  COUNTRY_LEVELS.forEach(applyLevelStyle);
  applyObwodyStyle();
  const winners =
    currentLevel === "obwody"
      ? Object.values(resultsIndex).map((r) => r.winner)
      : (levelData[currentLevel]?.features || []).map((f) => f.properties.winner);
  renderLegend(currentMetric, winners);
}

// ---------- ładowanie danych ----------

function clearObwodyFeatureState() {
  try {
    map.removeFeatureState({ source: "obwody", sourceLayer: "obwody" });
  } catch {
    // źródło jeszcze niezaładowane
  }
}

function applyResultsFeatureState() {
  if (!map.getSource("obwody")) return;
  clearObwodyFeatureState();
  Object.entries(resultsIndex).forEach(([key, result]) => {
    map.setFeatureState(
      { source: "obwody", sourceLayer: "obwody", id: key },
      { frekwencja: result.frekwencja, winner: result.winner },
    );
  });
}

// Podświetlenie obrysu pod kursorem przez feature-state "hover". Działa zarówno
// dla źródeł GeoJSON (poziomy krajowe, generateId) jak i wektorowych (obwody,
// promoteId "key") — dla obwodów podajemy sourceLayer.
function wireHover(fillLayerId, source, sourceLayer) {
  let hoveredId = null;
  const clear = () => {
    if (hoveredId === null) return;
    map.setFeatureState({ source, sourceLayer, id: hoveredId }, { hover: false });
    hoveredId = null;
  };
  map.on("mousemove", fillLayerId, (e) => {
    if (!e.features.length) return;
    const id = e.features[0].id;
    if (id === hoveredId) return;
    clear();
    hoveredId = id;
    map.setFeatureState({ source, sourceLayer, id }, { hover: true });
  });
  map.on("mouseleave", fillLayerId, clear);
}

let loadToken = 0;

function emptyFeatureCollection() {
  return { type: "FeatureCollection", features: [] };
}

async function loadElection(electionId, options = {}) {
  const token = ++loadToken;
  const election = manifest.elections.find((item) => item.id === electionId);

  const statsEl = document.getElementById("stats");
  statsEl.classList.add("is-loading");
  statsEl.innerHTML = "<p>Ładowanie danych…</p>";

  const [levelGeojsons, results] = await Promise.all([
    Promise.all(
      COUNTRY_LEVELS.map((level) => {
        const info = election.country ? election.country[level] : null;
        return info ? fetch(`data/${info.file}`).then((r) => r.json()) : Promise.resolve(emptyFeatureCollection());
      }),
    ),
    fetch(`data/results_${electionId}.json`).then((r) => (r.ok ? r.json() : {})),
  ]);

  // Jeśli w międzyczasie użytkownik przełączył na inne wybory, ta (wolniejsza)
  // odpowiedź jest nieaktualna — porzucamy ją, żeby nie nadpisać nowszego stanu.
  if (token !== loadToken) return;

  currentElection = election;
  COUNTRY_LEVELS.forEach((level, i) => {
    levelData[level] = levelGeojsons[i];
    map.getSource(level).setData(levelGeojsons[i]);
  });
  resultsIndex = results;

  ensureObwodyLayer(currentElection.tiles);
  applyResultsFeatureState();
  applyStyles();
  buildSearchIndex();

  if (!options.skipAutoJump && !currentElection.country && currentElection.areas.length) {
    const area = currentElection.areas[0];
    map.jumpTo({ center: [area.center[1], area.center[0]], zoom: Math.max(area.zoom, OBWODY_MIN_ZOOM + 1) });
  }

  renderStats();
  updateContext();
  updateHash();
}

// ---------- permalink (stan w hashu URL) ----------

function parseHash() {
  const params = new URLSearchParams(location.hash.replace(/^#/, ""));
  const lng = parseFloat(params.get("lng"));
  const lat = parseFloat(params.get("lat"));
  const zoom = parseFloat(params.get("zoom"));
  return {
    election: params.get("election"),
    metric: params.get("metric"),
    level: params.get("level"),
    center: Number.isFinite(lng) && Number.isFinite(lat) ? [lng, lat] : null,
    zoom: Number.isFinite(zoom) ? zoom : null,
  };
}

function updateHash() {
  if (!currentElection || !map) return;
  const center = map.getCenter();
  const params = new URLSearchParams({
    election: currentElection.id,
    metric: currentMetric,
    level: currentLevel,
    lng: center.lng.toFixed(4),
    lat: center.lat.toFixed(4),
    zoom: map.getZoom().toFixed(2),
  });
  history.replaceState(null, "", "#" + params.toString());
}

async function init() {
  const manifestResponse = await fetch("data/manifest.json");
  manifest = await manifestResponse.json();
  const hash = parseHash();

  const protocol = new pmtiles.Protocol();
  maplibregl.addProtocol("pmtiles", protocol.tile);

  // Stonowany, jasny podkład CARTO Positron. Etykiety miejscowości są osobną
  // warstwą "carto-labels" NAD kartogramem — wszystkie warstwy danych wstawiamy
  // pod nią (beforeId: "carto-labels"), żeby nazwy miast pozostały czytelne mimo
  // wypełnienia obwodów/gmin.
  const cartoAttribution =
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> ' +
    '&copy; <a href="https://carto.com/attributions">CARTO</a>';
  const cartoTiles = (style) =>
    ["a", "b", "c", "d"].map((s) => `https://${s}.basemaps.cartocdn.com/rastertiles/${style}/{z}/{x}/{y}.png`);

  map = new maplibregl.Map({
    container: "map",
    style: {
      version: 8,
      sources: {
        "carto-base": {
          type: "raster",
          tiles: cartoTiles("light_nolabels"),
          tileSize: 256,
          attribution: cartoAttribution,
        },
        "carto-labels": {
          type: "raster",
          tiles: cartoTiles("light_only_labels"),
          tileSize: 256,
        },
      },
      layers: [
        { id: "carto-base", type: "raster", source: "carto-base" },
        { id: "carto-labels", type: "raster", source: "carto-labels" },
      ],
    },
    center: hash.center || [19.3, 52.0],
    zoom: hash.zoom ?? 6,
  });

  map.addControl(new maplibregl.NavigationControl(), "top-left");

  await new Promise((resolve) => map.on("load", resolve));

  // Trzy poziomy krajowe (województwa/powiaty/gminy) to osobne źródła/warstwy —
  // widoczna jest tylko jedna naraz, sterowana przez setLevel() (poziom
  // szczegółowości jest wyborem użytkownika, nie funkcją zoomu). Warstwa
  // "obwody" jest tworzona dynamicznie w ensureObwodyLayer(), bo jej geometria
  // jest per wybory (patrz loadElection).
  COUNTRY_LEVELS.forEach((level) => {
    // generateId: true nadaje featom stabilne id, bez których feature-state
    // (podświetlenie na hover) nie działa dla źródeł GeoJSON.
    map.addSource(level, { type: "geojson", data: emptyFeatureCollection(), generateId: true });
    map.addLayer(
      {
        id: `${level}-fill`,
        type: "fill",
        source: level,
        layout: { visibility: level === currentLevel ? "visible" : "none" },
        paint: { "fill-color": "#cccccc", "fill-opacity": 0.8 },
      },
      "carto-labels",
    );
    map.addLayer(
      {
        id: `${level}-line`,
        type: "line",
        source: level,
        layout: { visibility: level === currentLevel ? "visible" : "none" },
        paint: {
          "line-color": ["case", ["boolean", ["feature-state", "hover"], false], "#0f172a", "#94a3b8"],
          "line-width": ["case", ["boolean", ["feature-state", "hover"], false], 2.5, 0.6],
        },
      },
      "carto-labels",
    );
    map.on("click", `${level}-fill`, (e) => {
      if (e.features.length) showLevelPopup(e.features[0], e.lngLat, level);
    });
    map.on("mouseenter", `${level}-fill`, () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", `${level}-fill`, () => (map.getCanvas().style.cursor = ""));
    wireHover(`${level}-fill`, level);
  });

  map.on("click", "obwody-fill", (e) => {
    if (e.features.length) showObwodPopup(e.features[0], e.lngLat);
  });
  map.on("mouseenter", "obwody-fill", () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", "obwody-fill", () => (map.getCanvas().style.cursor = ""));
  wireHover("obwody-fill", "obwody", "obwody");
  // "idle" (nie tylko zoomend/moveend) — kafelki wektorowe ładują się async,
  // więc zaraz po zoomend mogą jeszcze nie być wyrenderowane.
  map.on("idle", () => {
    renderStats();
    updateContext();
  });

  document.getElementById("back-to-country").addEventListener("click", () => {
    closePopup();
    map.flyTo({ center: [19.3, 52.0], zoom: 6 });
  });

  const filtersToggle = document.getElementById("filters-toggle");
  filtersToggle.addEventListener("click", () => {
    const collapsed = document.getElementById("app").classList.toggle("filters-collapsed");
    filtersToggle.setAttribute("aria-expanded", String(!collapsed));
  });

  const select = document.getElementById("election");
  manifest.elections.forEach((election) => {
    const option = document.createElement("option");
    option.value = election.id;
    option.textContent = election.label;
    select.appendChild(option);
  });

  select.addEventListener("change", () => {
    loadElection(select.value).catch((error) => {
      console.error(error);
      document.getElementById("stats").innerHTML = `<p class="error">${error.message}</p>`;
    });
  });
  document.getElementById("metric").addEventListener("change", (e) => {
    currentMetric = e.target.value;
    applyStyles();
    updateHash();
  });
  document.getElementById("level").addEventListener("change", (e) => {
    closePopup();
    setLevel(e.target.value);
  });
  map.on("moveend", updateHash);

  const searchInput = document.getElementById("search");
  searchInput.addEventListener("change", (e) => handleSearch(e.target.value));
  searchInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") handleSearch(searchInput.value);
  });

  const initialElectionId =
    hash.election && manifest.elections.some((e) => e.id === hash.election)
      ? hash.election
      : manifest.elections[0].id;
  if (hash.metric === "frekwencja" || hash.metric === "winner") {
    currentMetric = hash.metric;
    document.getElementById("metric").value = hash.metric;
  }
  if (ALL_LEVELS.includes(hash.level)) {
    currentLevel = hash.level;
    document.getElementById("level").value = hash.level;
    ALL_LEVELS.forEach((lvl) => {
      if (!map.getLayer(`${lvl}-fill`)) return;
      const visibility = lvl === currentLevel ? "visible" : "none";
      map.setLayoutProperty(`${lvl}-fill`, "visibility", visibility);
      map.setLayoutProperty(`${lvl}-line`, "visibility", visibility);
    });
  }
  select.value = initialElectionId;
  await loadElection(initialElectionId, { skipAutoJump: Boolean(hash.center) });
}

init().catch((error) => {
  console.error(error);
  document.getElementById("stats").innerHTML = `<p class="error">${error.message}</p>`;
});
