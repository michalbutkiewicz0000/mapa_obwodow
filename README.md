# Mapa obwodów wyborczych

Interaktywna mapa obwodów głosowania z wynikami wyborów. MVP dla **Krakowa**, docelowo cała Polska (patrz [`STAN_PROJEKTU.md`](STAN_PROJEKTU.md)).

## Wymagania

- Python 3.9+

Frontend jest statyczny (HTML/JS + Leaflet z CDN) — Node.js nie jest wymagany.

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

## Uruchomienie aplikacji

```bash
cd frontend/public
python3 -m http.server 8080
```

Otwórz w przeglądarce: http://localhost:8080

## Konfiguracja

Plik [`config/elections.yaml`](config/elections.yaml) definiuje miasto, źródła granic i wyników dla poszczególnych wyborów.

## Źródła danych

- Granice obwodów: [MSIP Kraków](https://msip.krakow.pl/dataset/2684)
- Wyniki wyborów: [wybory.gov.pl](https://wybory.gov.pl/)
- Metadane obwodów: plik PKW Excel
