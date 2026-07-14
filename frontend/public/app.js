const PARTY_COLORS = {
  PiS: "#004098",
  KO: "#e30613",
  "Trzecia Droga": "#f7941d",
  Lewica: "#e1001a",
  Konfederacja: "#582c07",
  "Polska jest Jedna": "#00a651",
  Bezpartyjni: "#666666",
};

const OBWODY_MIN_ZOOM = 9;

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
let countryData = null; // GeoJSON gmin dla bieżących wyborów
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
  map.addLayer({
    id: "obwody-fill",
    type: "fill",
    source: "obwody",
    "source-layer": "obwody",
    minzoom: OBWODY_MIN_ZOOM,
    paint: { "fill-color": "#cccccc", "fill-opacity": 0.7 },
  });
  map.addLayer({
    id: "obwody-line",
    type: "line",
    source: "obwody",
    "source-layer": "obwody",
    minzoom: OBWODY_MIN_ZOOM,
    paint: { "line-color": "#333333", "line-width": 1 },
  });
  currentTilesFile = tilesFile;
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

function frekwencjaColor(value) {
  const clamped = Math.max(0, Math.min(1, value || 0));
  const hue = 210 - clamped * 210;
  return `hsl(${hue}, 70%, 45%)`;
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
  (countryData?.features || []).forEach((f) => {
    if (!f.properties.gmina_nazwa) return;
    const [minX, minY, maxX, maxY] = geometryBounds(f.geometry);
    searchIndex.push({ label: f.properties.gmina_nazwa, center: [(minX + maxX) / 2, (minY + maxY) / 2], zoom: 11 });
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

function updateContext() {
  const backBtn = document.getElementById("back-to-country");
  const contextName = document.getElementById("context-name");
  if (map.getZoom() < OBWODY_MIN_ZOOM) {
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

function renderStats() {
  const stats = document.getElementById("stats");
  const countryInfo = currentElection.country;

  let html = "";
  if (countryInfo) {
    const gminyFeatures = countryData ? countryData.features : [];
    const withResults = gminyFeatures.filter((f) => f.properties.winner);
    const avgTurnout =
      withResults.reduce((sum, f) => sum + (f.properties.frekwencja || 0), 0) / (withResults.length || 1);
    html += `
      <p><strong>Widok krajowy</strong></p>
      <p>Gminy z wynikami: ${countryInfo.matched}/${countryInfo.total}</p>
      <p>Średnia frekwencja: ${formatPercent(avgTurnout)}</p>
    `;
  }

  if (map.getZoom() >= OBWODY_MIN_ZOOM) {
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
    html += `
      <p style="margin-top:0.75rem"><strong>Widok obwodowy (na ekranie)</strong></p>
      <p>Obwody widoczne: ${seen.size}</p>
      <p>Z wynikami: ${withData}</p>
      <p>Średnia frekwencja: ${formatPercent(withData ? sumTurnout / withData : null)}</p>
    `;
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

function showGminaPopup(feature, lngLat) {
  const props = feature.properties;
  const results = parseResults(props.results);
  const resultLines = topResults(results)
    .map(([name, votes]) => `<li><strong>${name}</strong>: ${votes}</li>`)
    .join("");
  const area = findAreaForTeryt(props.teryt);

  const node = document.createElement("div");
  node.className = "popup";
  node.innerHTML = `
    <h3>${props.gmina_nazwa ?? "Gmina"}</h3>
    <p><strong>Frekwencja:</strong> ${formatPercent(props.frekwencja)}</p>
    <p><strong>Głosy ważne:</strong> ${props.glosy_wazne ?? "—"}</p>
    ${props.winner ? `<p><strong>Zwycięzca:</strong> ${props.winner}</p>` : ""}
    <p><strong>Obwody:</strong> ${props.obwody ?? "—"}</p>
    ${resultLines ? `<ul>${resultLines}</ul>` : ""}
    ${area ? `<button type="button" class="show-area-btn">Pokaż obwody →</button>` : ""}
    ${area ? qualityBadge(area.quality) : ""}
  `;
  if (area) {
    node.querySelector(".show-area-btn").addEventListener("click", () => {
      closePopup();
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
  const resultLines = topResults(results)
    .map(([name, votes]) => `<li><strong>${name}</strong>: ${votes}</li>`)
    .join("");

  const html = `
    <div class="popup">
      <h3>Obwód ${props.obwod ?? "?"}</h3>
      ${result.dzielnica ? `<p><em>${result.dzielnica}</em></p>` : ""}
      <p><strong>Frekwencja:</strong> ${formatPercent(result.frekwencja)}</p>
      <p><strong>Głosy ważne:</strong> ${result.glosy_wazne ?? "—"}</p>
      ${result.winner ? `<p><strong>Zwycięzca:</strong> ${result.winner}</p>` : ""}
      ${result.komisja ? `<p><strong>Komisja:</strong> ${result.komisja}</p>` : ""}
      ${resultLines ? `<ul>${resultLines}</ul>` : ""}
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

function applyGminaStyle() {
  const fillColor =
    currentMetric === "frekwencja"
      ? ["interpolate", ["linear"], ["coalesce", ["get", "frekwencja"], 0], ...frekwencjaColorStops()]
      : winnerFillExpression(countryData ? countryData.features.map((f) => f.properties.winner) : []);
  map.setPaintProperty("gminy-fill", "fill-color", fillColor);
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
  applyGminaStyle();
  applyObwodyStyle();
  const winners =
    map.getZoom() >= OBWODY_MIN_ZOOM
      ? Object.values(resultsIndex).map((r) => r.winner)
      : countryData
        ? countryData.features.map((f) => f.properties.winner)
        : [];
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

let loadToken = 0;

async function loadElection(electionId, options = {}) {
  const token = ++loadToken;
  const election = manifest.elections.find((item) => item.id === electionId);

  const [countryGeojson, results] = await Promise.all([
    election.country
      ? fetch(`data/${election.country.file}`).then((r) => r.json())
      : Promise.resolve({ type: "FeatureCollection", features: [] }),
    fetch(`data/results_${electionId}.json`).then((r) => (r.ok ? r.json() : {})),
  ]);

  // Jeśli w międzyczasie użytkownik przełączył na inne wybory, ta (wolniejsza)
  // odpowiedź jest nieaktualna — porzucamy ją, żeby nie nadpisać nowszego stanu.
  if (token !== loadToken) return;

  currentElection = election;
  countryData = countryGeojson;
  resultsIndex = results;

  map.getSource("gminy").setData(countryData);
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

  map = new maplibregl.Map({
    container: "map",
    style: {
      version: 8,
      sources: {
        osm: {
          type: "raster",
          tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
          tileSize: 256,
          attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
        },
      },
      layers: [{ id: "osm", type: "raster", source: "osm" }],
    },
    center: hash.center || [19.3, 52.0],
    zoom: hash.zoom ?? 6,
  });

  map.addControl(new maplibregl.NavigationControl(), "top-left");

  await new Promise((resolve) => map.on("load", resolve));

  map.addSource("gminy", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  map.addLayer({
    id: "gminy-fill",
    type: "fill",
    source: "gminy",
    maxzoom: OBWODY_MIN_ZOOM,
    paint: { "fill-color": "#cccccc", "fill-opacity": 0.65 },
  });
  map.addLayer({
    id: "gminy-line",
    type: "line",
    source: "gminy",
    maxzoom: OBWODY_MIN_ZOOM,
    paint: { "line-color": "#333333", "line-width": 1 },
  });

  // Warstwa "obwody" jest tworzona dynamicznie w ensureObwodyLayer() —
  // geometria obwodów jest per wybory (zmienia się w czasie), więc źródło
  // kafelków trzeba podmieniać przy każdej zmianie wyborów (patrz loadElection).

  map.on("click", "gminy-fill", (e) => {
    if (e.features.length) showGminaPopup(e.features[0], e.lngLat);
  });
  map.on("click", "obwody-fill", (e) => {
    if (e.features.length) showObwodPopup(e.features[0], e.lngLat);
  });
  map.on("mouseenter", "gminy-fill", () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", "gminy-fill", () => (map.getCanvas().style.cursor = ""));
  map.on("mouseenter", "obwody-fill", () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", "obwody-fill", () => (map.getCanvas().style.cursor = ""));
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
  select.value = initialElectionId;
  await loadElection(initialElectionId, { skipAutoJump: Boolean(hash.center) });
}

init().catch((error) => {
  console.error(error);
  document.getElementById("stats").innerHTML = `<p class="error">${error.message}</p>`;
});
