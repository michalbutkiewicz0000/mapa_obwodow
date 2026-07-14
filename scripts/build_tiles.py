#!/usr/bin/env python3
"""Buduje kafelki wektorowe (PMTiles) — jeden zestaw per wybory — oraz osobne
pliki wyników per wybory, do wczytania przez klienta (feature-state w MapLibre GL).

Kluczowa decyzja architektoniczna: geometria i wyniki są rozdzielone, ale
GEOMETRIA JEST TEŻ PER WYBORY (nie jeden wspólny zbiór dla wszystkich) — obwody
zmieniają się w czasie (nowe/zniesione, przenumerowane), więc np. Kraków ma
inny zestaw poligonów dla sejm2023 (411, shapefile MSIP 2023) niż dla prez2025
(412, shapefile MSIP 2025). Źródłem geometrii są WYŁĄCZNIE pliki `areas[].file`
z manifestu (już poprawnie przypisane do konkretnych wyborów) — nigdy surowe
`data/generated/*.geojson`, co też eliminuje ryzyko zdublowania geometrii dla
tej samej gminy z dwóch źródeł (jak w Etapie 5 dla Krakowa).

Wymaga zbudowanego binarnie `tippecanoe` w PATH (patrz STAN_PROJEKTU.md — na
tej maszynie zbudowany ze źródeł, brew niedostępny).

Użycie:
    python scripts/build_tiles.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from utils import PROCESSED_DIR, ROOT

TILES_WORK_DIR = ROOT / "data" / "tiles"
OLD_SHARED_PMTILES = PROCESSED_DIR / "obwody.pmtiles"  # z Etapu 5, zastąpiony per-wyborczymi


def collect_geometry_for_election(areas: list[dict]) -> gpd.GeoDataFrame | None:
    """Zbiera geometrię obwodów wszystkich `areas` DANYCH wyborów. Każdy plik
    area ma już własną kolumnę `teryt` per wiersz (stałą dla zwykłej gminy,
    różną per dzielnicę dla Warszawy — patrz generate_boundaries.py)."""
    parts = []
    for area in areas:
        area_path = PROCESSED_DIR / area["file"]
        if not area_path.exists():
            continue
        gdf = gpd.read_file(area_path)
        if "teryt" not in gdf.columns:
            gdf["teryt"] = area["teryt"]
        gdf = gdf[["teryt", "obwod", "geometry"]].copy()
        gdf["quality"] = area["quality"]
        parts.append(gdf)

    if not parts:
        return None

    combined = pd.concat(parts, ignore_index=True)
    combined = gpd.GeoDataFrame(combined, geometry="geometry", crs="EPSG:4326")
    combined["obwod"] = combined["obwod"].astype(int)
    combined["teryt"] = combined["teryt"].astype(str)
    combined["key"] = combined["teryt"] + "_" + combined["obwod"].astype(str)
    return combined[["key", "teryt", "obwod", "quality", "geometry"]]


def build_pmtiles(gdf: gpd.GeoDataFrame, output_path: Path) -> None:
    if shutil.which("tippecanoe") is None:
        raise SystemExit(
            "Brak binarki tippecanoe w PATH. Zbuduj ją (brew install tippecanoe, "
            "lub ze źródeł github.com/felt/tippecanoe) i spróbuj ponownie."
        )

    TILES_WORK_DIR.mkdir(parents=True, exist_ok=True)
    source_path = TILES_WORK_DIR / f"{output_path.stem}_source.geojson"
    if source_path.exists():
        source_path.unlink()
    gdf.to_file(source_path, driver="GeoJSON")

    # NIE przerzedzamy obwodów przy oddaleniu — pełny zestaw poligonów daje solidny
    # kartogram na każdym zoomie. Efekt "potłuczonego szkła" w gęstych kafelkach nie
    # bierze się z liczby poligonów, tylko z OBRYSÓW (przy z6 tysiące ramek 0.7px
    # zakrywają wypełnienia) — rozwiązane po stronie klienta: szerokość obrysu obwodu
    # zależna od zoomu (0 przy oddaleniu, widoczna przy zbliżeniu). Patrz app.js
    # ensureObwodyLayer, "line-width".
    subprocess.run(
        [
            "tippecanoe",
            "-o", str(output_path),
            "-l", "obwody",
            "-zg",
            "--force",
            "--simplification=10",
            "--no-tile-compression",
            str(source_path),
        ],
        check=True,
    )


def _clean_str(value) -> str:
    """Zwraca string albo "" dla braków. Uwaga: NaN (float z GeoJSON, gdy pole
    puste) jest w Pythonie *truthy*, więc `value or ""` NIE odsiewa NaN — bez
    tego `json.dumps` zapisze `NaN`, co jest niepoprawnym JSON-em i wywala
    `JSON.parse` w przeglądarce (loadElection przerywa, warstwa obwodów zostaje
    na poprzednich wyborach)."""
    return value if isinstance(value, str) else ""


def collect_results_index(areas: list[dict]) -> dict:
    """Łączy wyniki wszystkich `areas` DANYCH wyborów w słownik kluczowany
    `{teryt}_{obwod}` — to jest to, co klient ładuje przy zmianie wyborów
    i wstrzykuje jako feature-state do warstwy wektorowej."""
    combined: dict[str, dict] = {}
    for area in areas:
        area_path = PROCESSED_DIR / area["file"]
        if not area_path.exists():
            continue
        gdf = gpd.read_file(area_path)
        has_teryt_col = "teryt" in gdf.columns
        for row in gdf.itertuples(index=False):
            teryt = str(row.teryt) if has_teryt_col else area["teryt"]
            key = f"{teryt}_{int(row.obwod)}"
            results = row.results
            if isinstance(results, str):
                try:
                    results = json.loads(results)
                except json.JSONDecodeError:
                    results = {}
            wyborcy = getattr(row, "wyborcy", None)
            combined[key] = {
                "frekwencja": float(row.frekwencja) if pd.notna(row.frekwencja) else 0,
                "glosy_wazne": int(row.glosy_wazne) if pd.notna(row.glosy_wazne) else 0,
                "winner": row.winner if isinstance(row.winner, str) else None,
                "results": results if isinstance(results, dict) else {},
                "komisja": _clean_str(getattr(row, "komisja", "")),
                "dzielnica": _clean_str(getattr(row, "dzielnica", "")),
                "wyborcy": int(wyborcy) if wyborcy is not None and pd.notna(wyborcy) else None,
                "opis_granic": _clean_str(getattr(row, "opis_granic", "")),
            }
    return combined


def main() -> None:
    manifest_path = PROCESSED_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    if OLD_SHARED_PMTILES.exists():
        OLD_SHARED_PMTILES.unlink()
        print(f"Usunięto przestarzały {OLD_SHARED_PMTILES.name} (z Etapu 5, zastąpiony kafelkami per wybory)\n")

    for entry in manifest["elections"]:
        election_id = entry["id"]
        areas = entry["areas"]
        print(f"=== {election_id} ===")

        gdf = collect_geometry_for_election(areas)
        if gdf is None or gdf.empty:
            print("  brak obszarów z poligonami — pomijam kafelki")
            entry["tiles"] = None
        else:
            tiles_name = f"obwody_{election_id}.pmtiles"
            output_path = PROCESSED_DIR / tiles_name
            build_pmtiles(gdf, output_path)
            size_mb = output_path.stat().st_size / 1e6
            print(f"  {len(gdf)} obwodów z {gdf['teryt'].nunique()} obszarów -> {tiles_name} ({size_mb:.2f} MB)")
            entry["tiles"] = tiles_name

        results = collect_results_index(areas)
        results_path = PROCESSED_DIR / f"results_{election_id}.json"
        # allow_nan=False: jeśli mimo _clean_str gdzieś przecieknie NaN/Inf,
        # niech build wywali się głośno, zamiast wygenerować niepoprawny JSON.
        results_path.write_text(json.dumps(results, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        print(f"  {len(results)} obwodów z wynikami -> {results_path.name}")

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nManifest zaktualizowany (dodano \"tiles\" per wybory): {manifest_path}")


if __name__ == "__main__":
    main()
