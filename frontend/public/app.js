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

function renderLegend(metric) {
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
  const parties = ["PiS", "KO", "Trzecia Droga", "Lewica", "Konfederacja"];
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

function renderStats(electionInfo, data) {
  const stats = document.getElementById("stats");
  const withResults = data.features.filter((f) => f.properties.winner);
  const avgTurnout =
    withResults.reduce((sum, f) => sum + (f.properties.frekwencja || 0), 0) /
    (withResults.length || 1);

  stats.innerHTML = `
    <p>Pokrycie wynikami: ${electionInfo.matched}/${electionInfo.total}</p>
    <p>Obwody na mapie: ${data.features.length}</p>
    <p>Średnia frekwencja: ${formatPercent(avgTurnout)}</p>
  `;
}

function bindPopup(feature, layer) {
  const props = feature.properties;
  const results = parseResults(props.results);
  const resultLines = topResults(results)
    .map(([name, votes]) => `<li><strong>${name}</strong>: ${votes}</li>`)
    .join("");

  layer.bindPopup(`
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

function drawMap(data, metric) {
  if (layer) {
    map.removeLayer(layer);
  }
  layer = L.geoJSON(data, {
    style: (feature) => styleFeature(feature, metric),
    onEachFeature: bindPopup,
  }).addTo(map);
  map.fitBounds(layer.getBounds(), { padding: [24, 24] });
}

async function loadElection(electionId) {
  const metric = document.getElementById("metric").value;
  const response = await fetch(`data/${electionId}.geojson`);
  if (!response.ok) throw new Error("Brak danych GeoJSON");
  currentData = await response.json();
  const electionInfo = manifest.elections.find((item) => item.id === electionId);
  renderStats(electionInfo, currentData);
  renderLegend(metric);
  drawMap(currentData, metric);
}

async function init() {
  const manifestResponse = await fetch("data/manifest.json");
  manifest = await manifestResponse.json();
  document.getElementById("city-name").textContent = manifest.city;

  map = L.map("map").setView(manifest.center, manifest.zoom);
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
  document.getElementById("metric").addEventListener("change", () => {
    if (currentData) {
      renderLegend(document.getElementById("metric").value);
      drawMap(currentData, document.getElementById("metric").value);
    }
  });

  await loadElection(manifest.elections[0].id);
}

init().catch((error) => {
  document.getElementById("stats").innerHTML = `<p class="error">${error.message}</p>`;
});
