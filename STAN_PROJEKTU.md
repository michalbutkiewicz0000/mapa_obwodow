# Stan projektu: mapa obwodów wyborczych

Dokument opisuje cel projektu, to co zostało zrealizowane, ograniczenia oraz najbliższe kroki. Stan na podstawie prac w repozytorium `mapa_obwodow`.

---

## Cel projektu

Celem jest **interaktywna strona internetowa z mapą obwodów głosowania w Polsce**, na której:

1. widać **granice obwodów** (poligony na mapie),
2. można **wybrać konkretne wybory** z listy,
3. po wyborze widać **siatkę obwodów z nałożonymi wynikami** (frekwencja, wyniki list/kandydatów, zwycięzca w obwodzie),
4. kliknięcie w obwód pokazuje **szczegóły** (numer, adres komisji, wyniki).

**MVP** zrealizowano dla **Krakowa** (411 obwodów z oficjalnymi poligonami MSIP). Dalszym celem jest **skalowanie na całą Polskę** (~31 500 obwodów), co wymaga innego podejścia do geometrii granic niż proste kopiowanie rozwiązania krakowskiego.

---

## Co zostało zrealizowane

### 1. Analiza danych źródłowych

- Przeanalizowano plik PKW [`obwody_glosowania_utf8.xlsx`](/Users/michalbutkiewicz/Downloads/obwody_glosowania_utf8.xlsx):
  - **31 497 obwodów** w całej Polsce,
  - **454 obwody** dla `m. Kraków`,
  - kolumna `Opis granic` wypełniona dla **100%** rekordów (0 pustych),
  - brak współrzędnych / poligonów — tylko **tekstowy opis granic** (ulice, numery, parzystość, miejscowości).
- Ustalono, że Excel nadaje się jako **metadane i klucz łączenia** (`TERYT gminy` + `Numer`), ale **nie zastępuje shapefile’ów** do mapy poligonowej.

### 2. Pipeline ETL (Kraków, działający)

Pliki: [`scripts/build_dataset.py`](scripts/build_dataset.py), [`scripts/utils.py`](scripts/utils.py), [`config/elections.yaml`](config/elections.yaml)

Pipeline:
1. Pobiera **shapefile obwodów** z MSIP Kraków (ZIP per wybory),
2. Pobiera **wyniki wyborów** z PKW (`wybory.gov.pl/.../data/csv/..._csv.zip`),
3. Łączy dane po kluczu `teryt + nr_obwodu`,
4. Wzbogaca metadanymi z Excela PKW (adres komisji, liczba wyborców, opis granic),
5. Eksportuje **GeoJSON** do [`frontend/public/data/`](frontend/public/data/).

**Działające wybory (Kraków, 411/411 dopasowanych wyników):**

| ID | Opis | Granice | Wyniki |
|----|------|---------|--------|
| `sejm2023` | Wybory parlamentarne 15.10.2023 | MSIP shapefile 2023 | CSV PKW ogólnopolski |
| `samorzad2024` | Wybory samorządowe 7.04.2024 (rady gmin) | MSIP shapefile 2024 | CSV PKW (woj. małopolskie) |

**Nie udało się dodać:** wyborów prezydenckich 2025 — URL CSV na `wybory.gov.pl/prezydent2025/...` zwraca HTML zamiast pliku ZIP.

**Uruchomienie ETL:**
```bash
source .venv/bin/activate
python scripts/build_dataset.py
```

### 3. Aplikacja webowa (mapa, działająca)

Pliki: [`frontend/public/index.html`](frontend/public/index.html), [`frontend/public/app.js`](frontend/public/app.js), [`frontend/public/styles.css`](frontend/public/styles.css)

Funkcje:
- mapa Leaflet (kafelki OpenStreetMap),
- **selektor wyborów** (dropdown),
- **kolorowanie**: frekwencja lub zwycięzca/lista,
- **popup** po kliknięciu w obwód (wyniki, frekwencja, adres komisji),
- legenda i statystyki (pokrycie wynikami, średnia frekwencja).

**Uruchomienie:**
```bash
cd frontend/public
python3 -m http.server 8080
# → http://localhost:8080
```

Przygotowano też szkielet wersji React (`frontend/src/`), ale **nie jest wymagany do działania** — brak npm w środowisku, dlatego produkcyjnie używana jest wersja statyczna HTML/JS.

### 4. Pilot generatora granic z „Opis granic” (rozpoczęty, niedokończony)

Pliki: [`scripts/parse_opis_granic.py`](scripts/parse_opis_granic.py), [`scripts/fetch_addresses_osm.py`](scripts/fetch_addresses_osm.py), [`scripts/run_pilot.py`](scripts/run_pilot.py)

**Idea pilota:** odtworzyć poligony obwodów z wiążącego opisu tekstowego PKW + przypisania adresów (zamiast czekać na shapefile z każdej gminy).

Co zostało zrobione:
- **Parser `Opis granic`** — rozpoznaje m.in.:
  - listy ulic,
  - parzyste / nieparzyste numery,
  - zakresy numerów (`1–13`, `od 2 do 20`, `do końca`),
  - miejscowości wiejskie (pojedyncze nazwy wsi).
- **Pobieranie adresów z OpenStreetMap** (Overpass API) w zadanym bbox.
- **Przypisywanie adresów do obwodów** na podstawie reguł z parsera.
- **Budowa wstępnych poligonów** (bufor + dissolve punktów przypisanych do obwodu).

**Wynik pilota dla Bolesławca** (`m. Bolesławiec`, 24 obwody, TERYT 20101):

| Metryka | Wartość |
|---------|---------|
| Adresy OSM w bbox | 6 405 |
| Adresy przypisane do obwodu | 5 472 (**85,4%**) |
| Obwody z co najmniej 1 punktem | **20 / 24** |
| Pliki wyjściowe | `data/pilot/boleslawiec_assigned.parquet`, `data/pilot/boleslawiec_polygons.geojson` |

**Nie dokończono:**
- walidacji **Krakowa vs shapefile MSIP** (IoU poligonów, trafność punktów) — skrypt się zatrzymał / został przerwany przed tym etapem,
- pliku `data/pilot/pilot_report.json` (raport końcowy pilota),
- integracji wygenerowanych poligonów z frontendem.

---

## Kluczowe ustalenia techniczne

1. **Wyniki wyborów ogólnopolskie są dostępne** — CSV PKW ma ~31 498 wierszy (Sejm 2023); parser w [`build_dataset.py`](scripts/build_dataset.py) filtruje dziś tylko jedną gminę, ale dane źródłowe obejmują całą Polskę.

2. **Poligony obwodów nie są ogólnopolskie** — MSIP Kraków publikuje shapefile per wybory; nie ma jednego centralnego repozytorium GeoJSON dla ~31k obwodów.

3. **Excel vs MSIP Kraków** — Excel ma 454 obwody dla Krakowa, shapefile MSIP ma 411 (dla wyborów 2023/2024). Numery 1–411 się pokrywają; 43 nowsze obwody (412–454) nie mają jeszcze poligonów w MSIP dla starszych wyborów. Granice i numeracja **muszą być parowane z konkretnymi wyborami**.

4. **Skala frontendu** — 411 obwodów ≈ 1,4 MB GeoJSON; ~31k poligonów wymagałoby kafelkowania wektorowego (PMTiles), nie jednego pliku w przeglądarce.

5. **Pilot używa OSM, nie PRG** — docelowo rzetelniejsze są oficjalne **punkty adresowe PRG** (GUGiK); OSM posłużył jako szybki substytut w pilocie.

---

## Struktura repozytorium (stan obecny)

```
mapa_obwodow/
├── config/elections.yaml          # konfiguracja Krakowa i wyborów
├── scripts/
│   ├── build_dataset.py           # ETL: MSIP + PKW → GeoJSON (Kraków)
│   ├── utils.py                   # pobieranie, normalizacja TERYT, CSV
│   ├── parse_opis_granic.py       # parser Opis granic (pilot)
│   ├── fetch_addresses_osm.py     # adresy z OSM (pilot)
│   └── run_pilot.py               # uruchomienie pilota Bolesławiec + Kraków
├── data/
│   ├── raw/                       # pobrane ZIP/shapefile (gitignore częściowo)
│   └── pilot/                     # wyniki pilota (Bolesławiec)
├── frontend/
│   ├── public/                    # działająca aplikacja + dane
│   │   ├── index.html, app.js, styles.css
│   │   └── data/                  # manifest.json, sejm2023.geojson, samorzad2024.geojson
│   └── src/                       # szkielet React (opcjonalny)
├── requirements.txt
├── README.md
└── STAN_PROJEKTU.md               # ten plik
```

---

## Najbliższe kroki (rekomendowana kolejność)

### Krótkoterminowo (dokończenie tego, co już jest)

1. **Dokończyć pilot** — uruchomić [`scripts/run_pilot.py`](scripts/run_pilot.py) do końca:
   - walidacja Krakowa względem MSIP (IoU poligonów, % poprawnie przypisanych adresów),
   - zapis `data/pilot/pilot_report.json`,
   - analiza 4 brakujących obwodów w Bolesławcu i ~15% nieprzypisanych adresów (błędy parsera vs luki w OSM).

2. **Poprawić parser** na podstawie walidacji — szczególnie złożone opisy krakowskie (`Kraków-Śródzieście: ...`, zakresy typu `Dajwór parzyste od 2 do 20`).

### Średnioterminowo (skalowanie)

3. **Wyniki ogólnopolskie bez filtra gminy** — refaktor `build_dataset.py`: wyniki per `(teryt, obwod)` dla całej Polski; mapa gmin (agregacja + PRG GeoJSON) jako widok kraju.

4. **Zastąpić OSM punktami PRG** w generatorze granic — oficjalne punkty adresowe z Geoportalu; cache per gmina.

5. **Model hybrydowy na mapie:**
   - zoom out → gminy z wynikami zagregowanymi,
   - zoom in / wybór miasta → poligony z MSIP (Kraków) lub wygenerowane (generator),
   - oznaczenie jakości granic (`official` / `generated` / `approximate`).

### Długoterminowo

6. **Kafelki wektorowe (PMTiles)** — gdy generator obejmie więcej gmin; 31k poligonów nie da się serwować jako jeden GeoJSON.

7. **Rejestr miast ze shapefile** — stopniowe dodawanie oficjalnych źródeł (Wrocław, Warszawa, …) nadpisujących generator.

---

## Czego świadomie nie zrobiono (jeszcze)

- mapy obwodów dla całej Polski z poligonami,
- integracji pilota z frontendem,
- geokodowania / punktów PRG ogólnopolskich,
- wyborów prezydenckich 2025,
- wersji React uruchamianej przez npm,
- commitów git (repozytorium nie było inicjalizowane w trakcie prac).

---

## Źródła danych

| Cel | Źródło | Status |
|-----|--------|--------|
| Granice obwodów (Kraków) | [MSIP Kraków](https://msip.krakow.pl/dataset/2684) | używane |
| Wyniki wyborów | [wybory.gov.pl](https://wybory.gov.pl/) → Dane w arkuszach (CSV ZIP) | używane |
| Metadane obwodów | Excel PKW (`obwody_glosowania_utf8.xlsx`) | używane |
| Adresy w pilocie | OpenStreetMap (Overpass) | tymczasowo |
| Adresy docelowo | PRG / Geoportal GUGiK | planowane |
| Granice gmin (docelowo) | PRG / gotowe GeoJSON | planowane |

---

## Podsumowanie jednym zdaniem

**Działa mapa Krakowa z wynikami dwóch wyborów i oficjalnymi granicami MSIP; rozpoczęto pilot automatycznego odtwarzania granic z opisu PKW (Bolesławiec ~85% przypisań), ale pełne skalowanie na Polskę wymaga dokończenia pilota, PRG zamiast OSM oraz warstw hybrydowych (gminy + generator + MSIP tam, gdzie jest).**
