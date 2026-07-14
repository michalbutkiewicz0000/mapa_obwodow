# Stan projektu: mapa obwodów wyborczych

Dokument opisuje cel projektu, to co zostało zrealizowane, ograniczenia oraz najbliższe kroki. Stan na podstawie prac w repozytorium `mapa_obwodow`.

---

## Cel projektu

Celem jest **interaktywna strona internetowa z mapą obwodów głosowania w Polsce**, na której:

1. widać **granice obwodów** (poligony na mapie),
2. można **wybrać konkretne wybory** z listy,
3. po wyborze widać **siatkę obwodów z nałożonymi wynikami** (frekwencja, wyniki list/kandydatów, zwycięzca w obwodzie),
4. kliknięcie w obwód pokazuje **szczegóły** (numer, adres komisji, wyniki).

**MVP** zrealizowano dla **Krakowa** (411 obwodów z oficjalnymi poligonami MSIP). **Etap 3** dodał widok całej Polski (kartogram gmin). **Etap 5** przeniósł frontend na kafelki wektorowe (PMTiles + MapLibre GL) z automatycznym przełączaniem gminy↔obwody wg zoomu. **Etap 6** opublikował aplikację publicznie.

**Aplikacja działa pod adresem: https://michalbutkiewicz0000.github.io/mapa_obwodow/**

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

### 6. PRG zamiast OSM i generator granic dla wielu gmin (Etap 4, działający)

Pliki: [`scripts/fetch_addresses_prg.py`](scripts/fetch_addresses_prg.py) (nowy), [`scripts/generate_boundaries.py`](scripts/generate_boundaries.py) (nowy), zmiany w [`scripts/run_pilot.py`](scripts/run_pilot.py), [`scripts/build_gminy.py`](scripts/build_gminy.py)

Co zostało zrobione:
- **`fetch_addresses_prg.py`** — pobiera oficjalne punkty adresowe z usługi WFS GUGiK „Krajowa Integracja Numeracji Adresowej” (`mapy.geoportal.gov.pl/wss/ext/KrajowaIntegracjaNumeracjiAdresowej`, warstwa `ms:prg-adresy`), filtrowane po `teryt` (ten sam 6-cyfrowy format co PKW!) przez OGC Filter XML, stronicowane po 10 000 rekordów. Wynik ma ten sam schemat co dane OSM (`street`, `number`, `housenumber`, `lat`, `lon`) plus `miejscowosc` dla dopasowania wsi — `run_pilot.assign_addresses` reużyty bez zmian (dodano tylko opcjonalne przekazanie `village`).
- **`generate_boundaries.py`** — batch generator: `--teryt <lista>` lub `--all [--limit N]`. Dla każdej gminy: reguły z Excela → adresy PRG → przypisanie (równoległe, jak w pilocie) → Voronoi przycięty do pełnej (nieuproszczonej) granicy gminy z Etapu 3 → GeoJSON w `data/generated/{teryt}.geojson`. Dodatkowo łączy poligony z wynikami wyborów o pełnym pokryciu krajowym i eksportuje `frontend/public/data/{election_id}_{teryt}.geojson` gotowe do wpięcia jako `area`.
- **Walidacja Krakowa (PRG vs OSM, ta sama metodyka co Etap 2):**

  | Metryka | OSM (Etap 2 po poprawkach) | PRG (Etap 4) |
  |---|---|---|
  | Adresy przypisane | 74,5% (70847/95057) | **87,6% (61352/70012)** |
  | Obwody z punktami | 340/454 | **381/454** |
  | Średnie IoU vs MSIP | 0,569 | **0,6033** |

  PRG ma mniej, ale czystszych punktów (70k vs 95k) i wyraźnie lepszą jakość geometrii.
- **Progi jakości (`official`/`generated`/`approximate`) oparte tylko o `assignment_rate`** (≥90% / ≥70% / niżej) — pierwotny plan zakładał też „0 brakujących obwodów”, ale w praktyce niemal każda gmina ma 1-3 obwody czysto instytucjonalne (szpital, więzienie, dom opieki) bez adresów ulicznych w PRG; ich brak nie świadczy o gorszej jakości granic, więc próg go pomija.
- **Pierwsza fala: 20 miast średniej wielkości** (15-21 obwodów, różne województwa) wygenerowane i wpięte do `manifest.json` dla sejm2023 i prez2025 (I/II tura): **14 „generated”, 6 „approximate”**, 0 błędów krytycznych. Lista z wynikami w `data/generated/quality.json`.
- **UI**: badge jakości przy nazwie obszaru (`Kraków — granice oficjalne (MSIP)` / `Przasnysz — granice wygenerowane — dobra jakość` itd.) i w popupie gminy przed kliknięciem „Pokaż obwody”; nota ostrzegawcza w popupie obwodu dla granic niebędących `official`. Zweryfikowano wizualnie w headless Chrome.

**Wciąż otwarte:**
- reguły parsera nadal ograniczają jakość w miastach o gęstej, powtarzalnej siatce ulic (Kraków pozostaje najtrudniejszym przypadkiem mimo poprawy),
- rejestr miast z oficjalnymi shapefile (`config/official_sources.yaml` z planu) — nie zrobiony, na razie tylko Kraków ma `quality: official`,
- `--all` (wszystkie ~2500 gmin) nie zostało uruchomione — to zadanie na wiele godzin obliczeń i osobną decyzję o zakresie (Etap 5/6).

### 7. Kafelki wektorowe: PMTiles + MapLibre GL (Etap 5, działający)

Pliki: [`scripts/build_tiles.py`](scripts/build_tiles.py) (nowy), przepisany [`frontend/public/app.js`](frontend/public/app.js) i [`frontend/public/index.html`](frontend/public/index.html)

**Narzędzia:** `tippecanoe` niedostępny przez Homebrew na tej maszynie (brak brew) — zbudowany ze źródeł (`github.com/felt/tippecanoe`, `make`, wymaga tylko Xcode CLT) i zainstalowany do `~/.local/bin` (dodane do `PATH` w `~/.zshrc`).

Co zostało zrobione:
- **`build_tiles.py`** — kluczowa decyzja architektoniczna: **geometria i wyniki są rozdzielone**. Geometria (Kraków + 20 wygenerowanych gmin, właściwości ograniczone do `key`/`teryt`/`obwod`/`quality`) trafia do jednego `frontend/public/data/obwody.pmtiles` (kafelki `-zg`, automatyczny dobór zoomu). Wyniki per wybory eksportowane osobno do `results_{election_id}.json` (mapa `"{teryt}_{obwod}"` → `{frekwencja, winner, results, komisja, dzielnica}`), budowane ze wszystkich `areas` w manifeście. Dzięki temu dodanie nowych wyborów nie wymaga przebudowy kafelków.
- **Frontend przepisany na MapLibre GL + protokół `pmtiles`** (biblioteki z CDN, tło nadal proste kafelki rastrowe OSM). Leaflet usunięty.
- **Automatyczne przełączanie gminy↔obwody wg zoomu** — natywne `minzoom`/`maxzoom` na warstwach (próg `zoom 9`), zamiast ręcznego przełączania trybu jak w Etapie 3. Wyniki per obwód wstrzykiwane jako `feature-state` (klucz `key = "{teryt}_{obwod}"` przez `promoteId`), więc kafelki nie muszą znać wyników w momencie budowy.
- Przycisk „Pokaż obwody” / „Powrót do mapy Polski” zachowane jako wygodne skróty (`flyTo`/`jumpTo`), ale nie są już jedynym sposobem nawigacji — zwykłe przybliżenie mapy też przełącza widok.
- Statystyki boczne pokazują teraz **oba poziomy jednocześnie**: agregat krajowy (zawsze) + „widok obwodowy (na ekranie)” liczony z aktualnie widocznych kafli, gdy zoom ≥ 9.

**Napotkane i naprawione problemy (wszystkie potwierdzone w headless Chrome, nie tylko czytane z kodu):**
- MapLibre interpoluje kolory liniowo w przestrzeni RGB między przystankami `interpolate`, a nie przez obrót odcieniem HSL jak stary wzór z Leaflet — dawało to brudny, nieczytelny gradient. Naprawiono generując 11 pośrednich przystanków tym samym wzorem HSL, żeby lokalna interpolacja RGB dobrze przybliżała łuk.
- Plik `data/generated/126101.geojson` (artefakt walidacji generatora PRG dla Krakowa z Etapu 4.4) kolidował z oficjalną geometrią MSIP w kafelkach (ten sam `key`, różne `quality`) — `build_tiles.py` jawnie pomija Kraków przy skanowaniu `data/generated/`.
- Statystyki „widok obwodowy” wymagają zdarzenia `idle` (kafelki wektorowe ładują się asynchronicznie) — samo `zoomend`/`moveend` czasem odpalało się przed wyrenderowaniem kafli.
- `renderStats()` zakładało, że `currentElection.country` zawsze istnieje — rzucało wyjątkiem dla `samorzad2024` (`country: null`), po cichu psując też `updateContext()` wywoływane zaraz po. Naprawione (blok krajowy renderowany warunkowo).
- Wyrażenie `match` dla koloru „zwycięzca” bez żadnych zwycięzców (pusta lista) jest nieprawidłowe składniowo w MapLibre (`match` wymaga ≥4 argumentów) — dodano fallback do stałego koloru.
- `loadElection()` nie miało zabezpieczenia przed race condition przy szybkim przełączaniu wyborów (odpowiedź z wolniejszego, starszego żądania mogła nadpisać nowszy stan) — dodano token wersji odrzucający nieaktualne odpowiedzi; dodano też `.catch` na handlerze `change`, żeby błędy nie ginęły cicho.
- Lokalny serwer testowy (`python -m http.server`) nie obsługuje HTTP Range requests wymaganych przez PMTiles (biblioteka `pmtiles.js` jawnie to wykrywa i zgłasza błąd zamiast ciszej degradacji) — do lokalnych testów napisano prosty serwer z obsługą Range; docelowy hosting (GitHub Pages, Etap 6) obsługuje Range natywnie.

**Wciąż otwarte:**
- kafelki dziś obejmują tylko 21 gmin (Kraków + pierwsza fala z Etapu 4) — reszta Polski nadal widoczna tylko na poziomie gmin, zgodnie z planem (docelowo `--all` z Etapu 4 zasili też kafelki bez zmian w `build_tiles.py`),
- brak testu z realnie pełną Polską na poziomie obwodów (~31,5 tys.) — rozmiar `obwody.pmtiles` przy obecnych 698 obwodach to 0,37 MB; szacunek „kilkadziesiąt-150 MB" dla całego kraju z planu pozostaje niesprawdzony.

### 8. Publikacja: GitHub Pages i szlif produktowy (Etap 6, działający)

**Aplikacja jest publicznie dostępna: https://michalbutkiewicz0000.github.io/mapa_obwodow/**

Repo: `github.com/michalbutkiewicz0000/mapa_obwodow` (publiczne). `gh` CLI nie było zainstalowane na tej maszynie — pobrany bezpośrednio jako binarka z GitHub Releases (bez Homebrew) do `~/.local/bin`; użytkownik zalogował się interaktywnie (`gh auth login`, przeglądarka).

Co zostało zrobione:
- **`.github/workflows/deploy-pages.yml`** — publikuje `frontend/public` na GitHub Pages (`actions/deploy-pages`) przy push do `main` (filtr `paths: frontend/public/**`) lub ręcznie (`workflow_dispatch`). Pages włączone w ustawieniach repo z `build_type: workflow`. Zweryfikowano: strona ładuje się (200), a **Range requests dla PMTiles działają (206 Partial Content)** — GitHub Pages obsługuje je natywnie, zgodnie z założeniem z planu.
- **`.github/workflows/test.yml`** — uruchamia `pytest tests/` na każdy push/PR.
- **`tests/`** — 32 testy: parser `parse_opis_granic` (zakresy, parzystość, `streets_equal` po całych słowach, rozstrzyganie konfliktów wg specyficzności), `utils.normalize_teryt/obwod/short_party_name/parse_int`, `build_gminy.aggregate_by_gmina` (suma głosów, frekwencja ważona, zero-padding TERYT), spójność `manifest.json` z plikami na dysku.
- **Permalinki** — stan aplikacji (wybory, metryka, środek i zoom mapy) kodowany w hashu URL (`#election=...&metric=...&lng=...&lat=...&zoom=...`), aktualizowany na `moveend`/zmianę wyborów/metryki (`history.replaceState`, bez zaśmiecania historii). Odtwarzanie stanu z linku działa przy świeżym wczytaniu strony (zweryfikowane w headless Chrome).
- **Wyszukiwarka gmin/miast** — pole tekstowe z `<datalist>` zbudowanym z nazw gmin bieżącego widoku krajowego + nazw obszarów z poligonami obwodów; wybór/Enter przenosi mapę (`flyTo`) do wyliczonego środka geometrii (dla gmin) lub zapisanego `center`/`zoom` (dla obszarów).
- **Popup obwodu** — rozwijana sekcja „Więcej szczegółów” z liczbą wyborców i pełnym opisem granic PKW (pola dodane do `results_{election}.json` w `build_tiles.py`, wcześniej eksportowane, ale niewyświetlane).
- **Strona `o-danych.html`** — źródła danych, wyjaśnienie znaczenia badge'y jakości granic (official/generated/approximate), zakres danych (które wybory mają widok krajowy i dlaczego samorzad2024 nie), link do repo.
- **Responsywność mobilna** — sprawdzona w emulacji 390×844 (iPhone): sidebar nad mapą, kontrolki czytelne, popup ograniczony do 88vw na wąskich ekranach.
- Tryb porównania dwóch wyborów **pominięty** (oznaczony w planie jako opcjonalny) — poza zakresem tej sesji.

**Wciąż otwarte:**
- brak automatycznego triggera dla `test.yml`/`deploy-pages.yml` przy zmianach w `data/`/`scripts/` poza `frontend/public` — budowa danych pozostaje w pełni lokalna/ręczna, zgodnie z planem,
- limit rozmiaru repo (100 MB/plik, ~1 GB miękki limit) nieprzetestowany dla pełnopolskiego `obwody.pmtiles` — plan B (Cloudflare R2 + URL w manifeście) pozostaje niezrealizowany, bo dziś kafelki mają tylko 0,37 MB.

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
│   ├── raw/                       # pobrane ZIP/shapefile/CSV/PRG (gitignore)
│   ├── pilot/                     # wyniki pilota (Bolesławiec + Kraków, OSM)
│   └── generated/                 # poligony z generate_boundaries.py (PRG) + quality.json
├── scripts/
│   ├── build_dataset.py           # ETL: MSIP + PKW → GeoJSON per miasto (Kraków)
│   ├── build_gminy.py             # agregacja krajowa wyników per gmina
│   ├── generate_boundaries.py     # batch generator granic (PRG) dla dowolnych gmin
│   ├── build_tiles.py             # kafelki PMTiles (geometria) + results_{election}.json
│   ├── utils.py                   # pobieranie, normalizacja TERYT, CSV
│   ├── parse_opis_granic.py       # parser Opis granic
│   ├── fetch_addresses_osm.py     # adresy z OSM (pilot/fallback)
│   ├── fetch_addresses_prg.py     # adresy z PRG/GUGiK (WFS)
│   └── run_pilot.py               # pilot Bolesławiec + Kraków, funkcje reużywane przez generator
├── frontend/
│   └── public/                    # jedyna, statyczna wersja aplikacji (MapLibre GL + PMTiles)
│       ├── index.html, app.js, styles.css
│       └── data/                  # manifest.json (v2), obwody.pmtiles, gminy_*.geojson, results_*.json
├── requirements.txt
├── README.md
└── STAN_PROJEKTU.md               # ten plik
```

---

## Najbliższe kroki

Wszystkie 6 etapów pierwotnego planu zrealizowane. Dalszy rozwój (nieplanowany szczegółowo):

- `scripts/generate_boundaries.py --all` — wygenerować granice dla pozostałych ~2500 gmin (wiele godzin obliczeń, wymaga decyzji o zakresie/priorytetyzacji),
- rejestr miast z oficjalnymi shapefile (`config/official_sources.yaml`) — Wrocław, Warszawa itd., w miarę znajdowania źródeł,
- tryb porównania dwóch wyborów (oznaczony jako opcjonalny w oryginalnym planie, pominięty),
- monitorowanie limitu rozmiaru repo GitHub w miarę wzrostu `obwody.pmtiles` — plan B: Cloudflare R2 + URL kafelków w manifeście (architektura już to wspiera).

---

## Czego świadomie nie zrobiono (jeszcze)

- mapy obwodów dla całej Polski z poligonami (dziś 21/~2500 gmin),
- geokodowania / punktów PRG dla reszty kraju,
- wersji React (usunięta — nie była budowana, dublowała `app.js`),
- trybu porównania dwóch wyborów (opcjonalny punkt planu).

---

## Źródła danych

| Cel | Źródło | Status |
|-----|--------|--------|
| Granice obwodów (Kraków) | [MSIP Kraków](https://msip.krakow.pl/dataset/2684) | używane |
| Wyniki wyborów | [wybory.gov.pl](https://wybory.gov.pl/) → Dane w arkuszach (CSV ZIP) | używane |
| Wyniki prez2025 | `prezydent2025.pkw.gov.pl/pl/dane_w_arkuszach` (ręczne pobranie) | używane |
| Metadane obwodów | Excel PKW (`data/metadata/obwody_glosowania_utf8.xlsx`) | używane |
| Adresy (generator, docelowe) | [PRG/GUGiK WFS](https://mapy.geoportal.gov.pl/wss/ext/KrajowaIntegracjaNumeracjiAdresowej) („Krajowa Integracja Numeracji Adresowej") | używane |
| Adresy (pilot, historyczne) | OpenStreetMap (Overpass) | zastąpione przez PRG |
| Granice gmin (widok krajowy i przycinanie Voronoi) | [waszkiewiczja/GeoJSON-Polska-...-Gminy](https://github.com/waszkiewiczja/GeoJSON-Polska-Wojewodztwa-Powiaty-Gminy) (domena publiczna) | używane |

---

## Podsumowanie jednym zdaniem

**Aplikacja jest publicznie dostępna pod https://michalbutkiewicz0000.github.io/mapa_obwodow/ i pokazuje całą Polskę jako kartogram 2479 gmin, płynnie przełączając się (bez przeładowań, kafelki PMTiles + MapLibre GL) na szczegółowe poligony obwodów po przybliżeniu do jednego z 21 miast: Krakowa (oficjalne granice MSIP) oraz 20 miast średniej wielkości z automatycznie wygenerowanymi granicami (PRG + Voronoi). Ma permalinki, wyszukiwarkę, stronę o metodologii, CI z 32 testami i działa na telefonie. Architektura (geometria i wyniki rozdzielone) jest gotowa na skalowanie do pełnej Polski — brakuje tylko wygenerowania większej liczby gmin (`generate_boundaries.py --all`). Pełny plan wieloetapowy: `~/.claude/plans/przeanalizuj-dokladnie-caly-projekt-woolly-sparkle.md`.**
