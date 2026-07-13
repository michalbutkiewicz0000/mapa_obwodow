# Stan projektu: mapa obwodów wyborczych

Dokument opisuje cel projektu, to co zostało zrealizowane, ograniczenia oraz najbliższe kroki. Stan na podstawie prac w repozytorium `mapa_obwodow`.

---

## Cel projektu

Celem jest **interaktywna strona internetowa z mapą obwodów głosowania w Polsce**, na której:

1. widać **granice obwodów** (poligony na mapie),
2. można **wybrać konkretne wybory** z listy,
3. po wyborze widać **siatkę obwodów z nałożonymi wynikami** (frekwencja, wyniki list/kandydatów, zwycięzca w obwodzie),
4. kliknięcie w obwód pokazuje **szczegóły** (numer, adres komisji, wyniki).

**MVP** zrealizowano dla **Krakowa** (411 obwodów z oficjalnymi poligonami MSIP). **Etap 3** dodał widok całej Polski (kartogram gmin) dla wyborów z pełnym pokryciem krajowym. Dalszym celem jest **skalowanie na poziom obwodów w całej Polsce** (~31 500 obwodów), co wymaga generatora granic (Etap 4) i kafelków wektorowych (Etap 5).

Pełny wieloetapowy plan wdrożenia: `~/.claude/plans/przeanalizuj-dokladnie-caly-projekt-woolly-sparkle.md`.

---

## Co zostało zrealizowane

### 1. Analiza danych źródłowych

- Przeanalizowano plik PKW [`obwody_glosowania_utf8.xlsx`](data/metadata/obwody_glosowania_utf8.xlsx) (teraz w repo, `data/metadata/`):
  - **31 497 obwodów** w całej Polsce,
  - **454 obwody** dla `m. Kraków`,
  - kolumna `Opis granic` wypełniona dla **100%** rekordów (0 pustych),
  - brak współrzędnych / poligonów — tylko **tekstowy opis granic** (ulice, numery, parzystość, miejscowości).
- Ustalono, że Excel nadaje się jako **metadane i klucz łączenia** (`TERYT gminy` + `Numer`), ale **nie zastępuje shapefile’ów** do mapy poligonowej.

### 2. Pipeline ETL (Kraków, działający)

Pliki: [`scripts/build_dataset.py`](scripts/build_dataset.py), [`scripts/utils.py`](scripts/utils.py), [`config/elections.yaml`](config/elections.yaml)

Pipeline:
1. Pobiera **shapefile obwodów** z MSIP Kraków (ZIP per wybory) albo z lokalnego pliku (`boundaries.local_path`),
2. Pobiera **wyniki wyborów** z PKW (`wybory.gov.pl/.../data/csv/..._csv.zip`) albo z lokalnego CSV (`results.local_path`),
3. Łączy dane po kluczu `teryt + nr_obwodu`,
4. Wzbogaca metadanymi z Excela PKW (adres komisji, liczba wyborców, opis granic),
5. Eksportuje **GeoJSON** do [`frontend/public/data/`](frontend/public/data/).

**Działające wybory (Kraków):**

| ID | Opis | Granice | Wyniki |
|----|------|---------|--------|
| `sejm2023` | Wybory parlamentarne 15.10.2023 | MSIP shapefile 2023 | CSV PKW ogólnopolski (411/411) |
| `samorzad2024` | Wybory samorządowe 7.04.2024 (rady gmin) | MSIP shapefile 2024 | CSV PKW (woj. małopolskie) (411/411) |
| `prez2025_t1` | Wybory prezydenckie 18.05.2025 (I tura) | MSIP shapefile 2025 | CSV PKW, plik lokalny (412/412) |
| `prez2025_t2` | Wybory prezydenckie 01.06.2025 (II tura) | MSIP shapefile 2025 | CSV PKW, plik lokalny (412/412) |

**Uwaga dot. prez2025:** portal `prezydent2025.pkw.gov.pl` został przebudowany na SPA-archiwum — automatyczne pobranie CSV nie działa (każdy URL `/data/csv/*.zip` zwraca HTML). Pliki `protokoly_po_obwodach_utf8.csv` i `protokoly_po_obwodach_w_drugiej_turze_utf8.csv` zostały pobrane ręcznie z `https://prezydent2025.pkw.gov.pl/prezydent2025/pl/dane_w_arkuszach` i leżą w `data/raw/prez2025/`. ETL wspiera teraz `results.local_path` i `boundaries.local_path` w `config/elections.yaml` jako alternatywę dla pobierania przez URL.

**Uruchomienie ETL:**
```bash
source .venv/bin/activate
python scripts/build_dataset.py
```

### 3. Aplikacja webowa (mapa, działająca)

Pliki: [`frontend/public/index.html`](frontend/public/index.html), [`frontend/public/app.js`](frontend/public/app.js), [`frontend/public/styles.css`](frontend/public/styles.css)

Funkcje:
- mapa Leaflet (kafelki OpenStreetMap), wyśrodkowana wg `manifest.center`/`manifest.zoom`,
- **selektor wyborów** (dropdown),
- **kolorowanie**: frekwencja lub zwycięzca/lista/kandydat,
- **popup** po kliknięciu w obwód (wyniki, frekwencja, adres komisji),
- **legenda budowana dynamicznie** z unikalnych zwycięzców w danych (działa też dla kandydatów prezydenckich, nie tylko partii),
- legenda i statystyki (pokrycie wynikami, średnia frekwencja).

**Uruchomienie:**
```bash
cd frontend/public
python3 -m http.server 8080
# → http://localhost:8080
```

Szkielet wersji React (`frontend/src/`) został usunięty — nigdy nie był budowany (brak npm w środowisku), dublował `app.js`. Produkcyjnie używana jest statyczna wersja HTML/JS bez zależności od Node.

### 4. Pilot generatora granic z „Opis granic” (ukończony pierwszy przebieg, wyniki bazowe)

Pliki: [`scripts/parse_opis_granic.py`](scripts/parse_opis_granic.py), [`scripts/fetch_addresses_osm.py`](scripts/fetch_addresses_osm.py), [`scripts/run_pilot.py`](scripts/run_pilot.py)

**Idea pilota:** odtworzyć poligony obwodów z wiążącego opisu tekstowego PKW + przypisania adresów (zamiast czekać na shapefile z każdej gminy).

Co zostało zrobione:
- **Parser `Opis granic`** — rozpoznaje m.in.:
  - listy ulic,
  - parzyste / nieparzyste numery,
  - zakresy numerów (`1–13`, `od 2 do 20`, `do końca`),
  - miejscowości wiejskie (pojedyncze nazwy wsi),
  - dopasowanie nazw ulic po całych słowach (nie po dowolnym podciągu znaków) — poprzedni luźny substring dawał masę fałszywych trafień w gęstej sieci ulic Krakowa,
  - rozstrzyganie konfliktów wg specyficzności reguły (zakres numerów > parzystość > cała ulica/miejscowość) zamiast odrzucania każdego adresu z >1 dopasowaniem.
- **Pobieranie adresów z OpenStreetMap** (Overpass API) w zadanym bbox, z cache.
- **Przypisywanie adresów do obwodów** równolegle (`ProcessPoolExecutor`, do 10 procesów) na podstawie reguł z parsera.
- **Budowa poligonów przez prawdziwy diagram Voronoi** (`shapely.voronoi_polygons`) przycięty do granicy gminy (dla Krakowa: dissolve shapefile MSIP), dissolve po obwodzie — zamiast wcześniejszego bufora punktowego.
- **Skrypt odporny na przerwania**: raport per gmina zapisywany od razu po jej przetworzeniu, walidacja MSIP w osobnym `try/except` (jej porażka nie kasuje reszty raportu).

**Wynik pilota — pełny raport w `data/pilot/pilot_report.json`, z porównaniem przed/po poprawkach parsera:**

| Metryka | Bolesławiec przed | Bolesławiec po | Kraków przed | Kraków po |
|---|---|---|---|---|
| Adresy przypisane | 85,4% (5472/6405) | **90,0% (5762/6405)** | 53,9% (51187/95057) | **74,5% (70847/95057)** |
| Konflikty (>1 dopasowanie) | 389 | **99** | 33 479 | **10 820** |
| Obwody brakujące | 4/24 | **3/24** | 140/454 | **114/454** |
| Średnie IoU vs MSIP | — | — | 0,172 | **0,569** |
| Trafność punktów vs MSIP | — | — | 97,1% | 96,6% |

Pliki wyjściowe: `data/pilot/{boleslawiec,krakow}_{assigned.parquet,polygons.geojson,report.json}`, `data/pilot/pilot_report.json`.

**Wciąż otwarte (Etap 4 planu — PRG zamiast OSM):**
- Kraków ma nadal 114/454 obwodów bez punktu i sporo konfliktów (gęsta, powtarzalna nazwa ulic w różnych dzielnicach) — adresy OSM i luźniejsza semantyka `Opis granic` dla dużego miasta to główne źródło błędu, nie sam mechanizm dopasowania,
- integracja wygenerowanych poligonów z frontendem (poza zakresem pilota).

### 5. Widok całej Polski — kartogram gmin (Etap 3, działający)

Pliki: [`scripts/build_gminy.py`](scripts/build_gminy.py) (nowy), zmiany w [`scripts/build_dataset.py`](scripts/build_dataset.py), [`frontend/public/app.js`](frontend/public/app.js)

Co zostało zrobione:
- **Parsery wyników przyjmują `teryt: str | None`** — bez filtra zwracają wszystkie obwody w kraju wraz z kolumną `teryt`, `eligible`, `voted` (potrzebne do agregacji ważonej frekwencji).
- **`build_gminy.py`** — dla wyborów z pełnym pokryciem krajowym (`sejm2023`, `prez2025_t1`, `prez2025_t2`) agreguje wyniki per gmina (suma głosów, frekwencja ważona liczbą uprawnionych/głosujących, zwycięzca), łączy z granicami gmin i eksportuje `frontend/public/data/gminy_{id}.geojson`.
  - **Wybory samorzad2024 pominięte w agregacji krajowej** — PKW publikuje dla nich tylko rady gmin >20 tys. mieszkańców per województwo, to nie jest komplet Polski; miejski widok Krakowa działa bez zmian.
  - **Granice gmin**: `waszkiewiczja/GeoJSON-Polska-Wojewodztwa-Powiaty-Gminy` (domena publiczna, 2479 gmin, kod `JPT_KOD_JE` — pierwsze 6 znaków to TERYT gminy używany przez PKW), uproszczone (`simplify` tolerancja 0.003) do ~3-4 MB per plik wyborów.
  - Zweryfikowano zgodność: suma głosów Krakowa z agregatu gminy (498 428) vs suma z poligonów obwodowych (494 619) — różnica to dokładnie 43 obwody istniejące w Excelu/CSV, których nie ma w shapefile MSIP (znany fakt, patrz pkt 3 niżej); zwycięzca (KO) zgodny w obu widokach.
- **Manifest v2**: `{"elections": [{"id", "label", "country": {"file","unit","matched","total"} | null, "areas": [{"name","teryt","file","center","zoom","quality","matched","total"}]}]}`. `country: null` dla wyborów bez pełnego pokrycia (samorzad2024).
- **Frontend — widok hybrydowy**: start pokazuje mapę Polski (gminy), popup gminy ma przycisk „Pokaż obwody →” (widoczny tylko dla gmin z dopasowanym `area.teryt`, dziś tylko Kraków), po kliknięciu ładuje się widok obwodowy z przyciskiem powrotu. Legenda trybu „zwycięzca” buduje się dynamicznie z danych — działa identycznie dla partii, kandydatów i lokalnych komitetów.
- Zweryfikowano end-to-end w headless Chrome (CDP): render mapy Polski, klik w gminę → popup → „Pokaż obwody” → widok Krakowa → powrót, przełączanie wyborów (w tym samorzad2024 bez widoku krajowego), oba tryby kolorowania — bez błędów w konsoli.

---

## Kluczowe ustalenia techniczne

1. **Wyniki wyborów ogólnopolskie są dostępne** — CSV PKW ma ~31 498 wierszy (Sejm 2023); parser w [`build_dataset.py`](scripts/build_dataset.py) filtruje dziś tylko jedną gminę, ale dane źródłowe obejmują całą Polskę.

2. **Poligony obwodów nie są ogólnopolskie** — MSIP Kraków publikuje shapefile per wybory; nie ma jednego centralnego repozytorium GeoJSON dla ~31k obwodów.

3. **Excel vs MSIP Kraków** — Excel ma 454 obwody dla Krakowa, shapefile MSIP ma 411 (dla wyborów 2023/2024) i 412 (dla 2025). Numery się w większości pokrywają; nowsze obwody nie mają jeszcze poligonów w MSIP dla starszych wyborów. Granice i numeracja **muszą być parowane z konkretnymi wyborami**.

4. **Skala frontendu** — 411 obwodów ≈ 1,4 MB GeoJSON; ~31k poligonów wymagałoby kafelkowania wektorowego (PMTiles), nie jednego pliku w przeglądarce.

5. **Pilot używa OSM, nie PRG** — docelowo rzetelniejsze są oficjalne **punkty adresowe PRG** (GUGiK); OSM posłużył jako szybki substytut w pilocie i pozostaje głównym ograniczeniem jakości dla dużych miast.

6. **Maszyna deweloperska ma rdzenie heterogeniczne** (Apple M4: 4 wydajne + 6 efektywnych) — zrównoleglanie CPU-bound pracy skaluje się słabiej niż liniowo; przy pilotowaniu większych obliczeń warto to uwzględnić.

---

## Struktura repozytorium (stan obecny)

```
mapa_obwodow/
├── config/elections.yaml          # konfiguracja Krakowa i wyborów
├── data/
│   ├── metadata/                  # Excel PKW (w repo)
│   ├── raw/                       # pobrane ZIP/shapefile/CSV (gitignore)
│   └── pilot/                     # wyniki pilota (Bolesławiec + Kraków)
├── scripts/
│   ├── build_dataset.py           # ETL: MSIP + PKW → GeoJSON per miasto (Kraków)
│   ├── build_gminy.py             # agregacja krajowa wyników per gmina
│   ├── utils.py                   # pobieranie, normalizacja TERYT, CSV
│   ├── parse_opis_granic.py       # parser Opis granic (pilot)
│   ├── fetch_addresses_osm.py     # adresy z OSM (pilot)
│   └── run_pilot.py               # uruchomienie pilota Bolesławiec + Kraków
├── frontend/
│   └── public/                    # jedyna, statyczna wersja aplikacji
│       ├── index.html, app.js, styles.css
│       └── data/                  # manifest.json (v2) + gminy_*.geojson + *.geojson (miasta)
├── requirements.txt
├── README.md
└── STAN_PROJEKTU.md               # ten plik
```

---

## Najbliższe kroki (rekomendowana kolejność — patrz pełny plan)

### Etap 4 — PRG zamiast OSM, generator dla wielu gmin

- `scripts/fetch_addresses_prg.py` — oficjalne punkty adresowe GUGiK zamiast OSM.
- `scripts/generate_boundaries.py` — batch generator z flagą jakości (`official`/`generated`/`approximate`).

### Etap 5 — skala ogólnopolska (PMTiles + MapLibre GL)

### Etap 6 — publikacja (GitHub Pages) i szlif UX

---

## Czego świadomie nie zrobiono (jeszcze)

- mapy obwodów dla całej Polski z poligonami,
- integracji pilota z frontendem,
- geokodowania / punktów PRG ogólnopolskich,
- wersji React (usunięta — nie była budowana, dublowała `app.js`),
- publicznego deploymentu (GitHub Pages — planowane w Etapie 6).

---

## Źródła danych

| Cel | Źródło | Status |
|-----|--------|--------|
| Granice obwodów (Kraków) | [MSIP Kraków](https://msip.krakow.pl/dataset/2684) | używane |
| Wyniki wyborów | [wybory.gov.pl](https://wybory.gov.pl/) → Dane w arkuszach (CSV ZIP) | używane |
| Wyniki prez2025 | `prezydent2025.pkw.gov.pl/pl/dane_w_arkuszach` (ręczne pobranie) | używane |
| Metadane obwodów | Excel PKW (`data/metadata/obwody_glosowania_utf8.xlsx`) | używane |
| Adresy w pilocie | OpenStreetMap (Overpass) | tymczasowo |
| Adresy docelowo | PRG / Geoportal GUGiK | planowane |
| Granice gmin (widok krajowy) | [waszkiewiczja/GeoJSON-Polska-...-Gminy](https://github.com/waszkiewiczja/GeoJSON-Polska-Wojewodztwa-Powiaty-Gminy) (domena publiczna) | używane |

---

## Podsumowanie jednym zdaniem

**Aplikacja pokazuje teraz całą Polskę: mapa startuje jako kartogram 2479 gmin (frekwencja/zwycięzca) dla wyborów z pełnym pokryciem krajowym (sejm2023, prez2025 I/II tura), z Krakowa wciąż dostępny szczegółowy widok 411 obwodów z oficjalnymi granicami MSIP (przycisk „Pokaż obwody” w popupie gminy). Pilot generatora granic z opisu PKW ma pełny raport z poprawą po korektach parsera (Kraków: 53,9%→74,5% przypisanych adresów, IoU 0,172→0,569). Dalsze skalowanie na poziom obwodów w całym kraju wymaga PRG zamiast OSM (Etap 4) i kafelków wektorowych (Etap 5). Pełny plan wieloetapowy: `~/.claude/plans/przeanalizuj-dokladnie-caly-projekt-woolly-sparkle.md`.**
