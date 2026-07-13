const PARTY_COLORS = {
  PiS: "#004098",
  KO: "#e30613",
  "Trzecia Droga": "#f7941d",
  Lewica: "#e1001a",
  Konfederacja: "#582c07",
  "Polska jest Jedna": "#00a651",
  Bezpartyjni: "#666666",
};

let map;
let layer;
let manifest;
let currentData = null;
let currentElection = null; // wpis z manifest.elections
let viewMode = "country"; // "country" | "area"
let currentArea = null;

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

function styleFeature(feature, metric) {
  const props = feature.properties;
  const fillColor =
    metric === "frekwencja"
      ? frekwencjaColor(props.frekwencja)
      : winnerColor(props.winner);
  return {
    fillColor,
    fillOpacity: 0.65,
    color: "#333333",
    weight: 1,
  };
}

function renderLegend(metric, data) {
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
  const winners = new Set(
    (data?.features || [])
      .map((f) => f.properties.winner)
      .filter(Boolean),
  );
  const parties = [...winners].sort();
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

function renderStats(data, meta) {
  const stats = document.getElementById("stats");
  const withResults = data.features.filter((f) => f.properties.winner);
  const avgTurnout =
    withResults.reduce((sum, f) => sum + (f.properties.frekwencja || 0), 0) /
    (withResults.length || 1);

  const coverageLabel = meta.unit === "gmina" ? "Gminy z wynikami" : "Pokrycie wynikami";
  const countLabel = meta.unit === "gmina" ? "Gminy na mapie" : "Obwody na mapie";

  stats.innerHTML = `
    <p>${coverageLabel}: ${meta.matched}/${meta.total}</p>
    <p>${countLabel}: ${data.features.length}</p>
    <p>Średnia frekwencja: ${formatPercent(avgTurnout)}</p>
  `;
}

function findAreaForTeryt(teryt) {
  if (!currentElection || !teryt) return null;
  return currentElection.areas.find((area) => area.teryt === teryt) || null;
}

function bindGminaPopup(feature, mapLayer) {
  const props = feature.properties;
  const results = parseResults(props.results);
  const resultLines = topResults(results)
    .map(([name, votes]) => `<li><strong>${name}</strong>: ${votes}</li>`)
    .join("");
  const area = findAreaForTeryt(props.teryt);

  const popupNode = document.createElement("div");
  popupNode.className = "popup";
  popupNode.innerHTML = `
    <h3>${props.gmina_nazwa ?? "Gmina"}</h3>
    <p><strong>Frekwencja:</strong> ${formatPercent(props.frekwencja)}</p>
    <p><strong>Głosy ważne:</strong> ${props.glosy_wazne ?? "—"}</p>
    ${props.winner ? `<p><strong>Zwycięzca:</strong> ${props.winner}</p>` : ""}
    <p><strong>Obwody:</strong> ${props.obwody ?? "—"}</p>
    ${resultLines ? `<ul>${resultLines}</ul>` : ""}
    ${area ? `<button type="button" class="show-area-btn">Pokaż obwody →</button>` : ""}
  `;
  if (area) {
    popupNode.querySelector(".show-area-btn").addEventListener("click", () => {
      loadArea(area);
    });
  }
  mapLayer.bindPopup(popupNode);
}

function bindObwodPopup(feature, mapLayer) {
  const props = feature.properties;
  const results = parseResults(props.results);
  const resultLines = topResults(results)
    .map(([name, votes]) => `<li><strong>${name}</strong>: ${votes}</li>`)
    .join("");

  mapLayer.bindPopup(`
    <div class="popup">
      <h3>Obwód ${props.obwod ?? "?"}</h3>
      ${props.dzielnica ? `<p><em>${props.dzielnica}</em></p>` : ""}
      <p><strong>Frekwencja:</strong> ${formatPercent(props.frekwencja)}</p>
      <p><strong>Głosy ważne:</strong> ${props.glosy_wazne ?? "—"}</p>
      ${props.winner ? `<p><strong>Zwycięzca:</strong> ${props.winner}</p>` : ""}
      ${props.komisja ? `<p><strong>Komisja:</strong> ${props.komisja}</p>` : ""}
      ${resultLines ? `<ul>${resultLines}</ul>` : ""}
    </div>
  `);
}

function drawMap(data, metric, onEachFeature) {
  if (layer) {
    map.removeLayer(layer);
  }
  layer = L.geoJSON(data, {
    style: (feature) => styleFeature(feature, metric),
    onEachFeature,
  }).addTo(map);
  map.fitBounds(layer.getBounds(), { padding: [24, 24] });
}

function currentMetric() {
  return document.getElementById("metric").value;
}

function redraw() {
  if (!currentData) return;
  const metric = currentMetric();
  renderLegend(metric, currentData);
  drawMap(currentData, metric, viewMode === "country" ? bindGminaPopup : bindObwodPopup);
}

async function loadCountry() {
  viewMode = "country";
  currentArea = null;
  document.getElementById("back-to-country").style.display = "none";
  document.getElementById("context-name").textContent = "Polska";

  const response = await fetch(`data/${currentElection.country.file}`);
  if (!response.ok) throw new Error("Brak danych GeoJSON (gminy)");
  currentData = await response.json();
  renderStats(currentData, { ...currentElection.country, unit: "gmina" });
  redraw();
}

async function loadArea(area) {
  viewMode = "area";
  currentArea = area;
  document.getElementById("back-to-country").style.display = currentElection.country ? "block" : "none";
  document.getElementById("context-name").textContent = area.name;

  const response = await fetch(`data/${area.file}`);
  if (!response.ok) throw new Error("Brak danych GeoJSON (obwody)");
  currentData = await response.json();
  renderStats(currentData, area);
  redraw();
  map.setView(area.center, area.zoom);
}

async function loadElection(electionId) {
  currentElection = manifest.elections.find((item) => item.id === electionId);
  if (currentElection.country) {
    await loadCountry();
  } else {
    await loadArea(currentElection.areas[0]);
  }
}

async function init() {
  const manifestResponse = await fetch("data/manifest.json");
  manifest = await manifestResponse.json();

  map = L.map("map").setView([52.0, 19.3], 6);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);

  const select = document.getElementById("election");
  manifest.elections.forEach((election) => {
    const option = document.createElement("option");
    option.value = election.id;
    option.textContent = election.label;
    select.appendChild(option);
  });

  select.addEventListener("change", () => loadElection(select.value));
  document.getElementById("metric").addEventListener("change", redraw);
  document.getElementById("back-to-country").addEventListener("click", () => loadCountry());

  await loadElection(manifest.elections[0].id);
}

init().catch((error) => {
  document.getElementById("stats").innerHTML = `<p class="error">${error.message}</p>`;
});
