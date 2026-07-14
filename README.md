# Mapa obwodów wyborczych

Interaktywna mapa obwodów głosowania z wynikami wyborów. MVP dla **Krakowa**, docelowo cała Polska (patrz [`STAN_PROJEKTU.md`](STAN_PROJEKTU.md)).

## Wymagania

- Python 3.9+

Frontend jest statyczny (HTML/JS + MapLibre GL i PMTiles z CDN) — Node.js nie jest wymagany.

Do przebudowy kafelków wektorowych (`scripts/build_tiles.py`) potrzebna jest binarka
`tippecanoe` w PATH — jeśli `brew install tippecanoe` nie jest dostępny, można zbudować
ją ze źródeł (`github.com/felt/tippecanoe`, wymaga tylko Xcode Command Line Tools):

```bash
git clone --depth 1 https://github.com/felt/tippecanoe.git /tmp/tippecanoe
cd /tmp/tippecanoe && make -j4
cp tippecanoe ~/.local/bin/
```

## Przygotowanie danych

```bash
cd /path/to/mapa_obwodow
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/build_dataset.py
```

Skrypt pobiera:
- granice obwodów (shapefile) z MSIP Kraków,
- wyniki wyborów (CSV) z wybory.gov.pl,
- metadane z pliku Excel PKW ([`data/metadata/obwody_glosowania_utf8.xlsx`](data/metadata/obwody_glosowania_utf8.xlsx) — adres komisji, opis granic).

Wynik trafia do `frontend/public/data/`.

Kolejne kroki pipeline'u (opcjonalne, w tej kolejności):
```bash
python scripts/build_gminy.py          # agregacja wyników per gmina (widok krajowy)
python scripts/generate_boundaries.py --teryt <TERYT...>   # granice dla kolejnych miast (PRG)
python scripts/build_tiles.py          # kafelki PMTiles + results_{wybory}.json
```

## Uruchomienie aplikacji

```bash
cd frontend/public
python3 -m http.server 8080
```

Otwórz w przeglądarce: http://localhost:8080

**Uwaga:** `python -m http.server` nie obsługuje HTTP Range requests, których wymaga
biblioteka PMTiles do wczytania warstwy obwodów (`data/obwody.pmtiles`) — bez tego
warstwa obwodów przy przybliżeniu nie wczyta się (widok gmin działa normalnie).
Docelowy hosting (GitHub Pages, Etap 6) obsługuje Range natywnie. Do lokalnych testów
można użyć dowolnego serwera z obsługą Range, np. `npx serve` (wymaga Node) albo
prostego własnego handlera opartego o `http.server.SimpleHTTPRequestHandler`.

## Konfiguracja

Plik [`config/elections.yaml`](config/elections.yaml) definiuje miasto, źródła granic i wyników dla poszczególnych wyborów.

## Źródła danych

- Granice obwodów: [MSIP Kraków](https://msip.krakow.pl/dataset/2684)
- Wyniki wyborów: [wybory.gov.pl](https://wybory.gov.pl/)
- Metadane obwodów: plik PKW Excel
