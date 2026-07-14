#!/usr/bin/env python3
"""Buduje kafelki wektorowe (PMTiles) z geometrii obwodów oraz osobne pliki
wyników per wybory, do wczytania przez klienta (feature-state w MapLibre GL).

Kluczowa decyzja architektoniczna: geometria (rzadko się zmienia, kosztowna w
budowie) i wyniki (zmieniają się per wybory, tanie w budowie) są rozdzielone.
Dzięki temu dodanie nowych wyborów nie wymaga przebudowy kafelków.

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

GENERATED_DIR = ROOT / "data" / "generated"
TILES_WORK_DIR = ROOT / "data" / "tiles"
QUALITY_PATH = GENERATED_DIR / "quality.json"
PMTILES_OUTPUT = PROCESSED_DIR / "obwody.pmtiles"

KRAKOW_TERYT = "126101"
# Ten sam zestaw 411 poligonów Krakowa co w innych plikach per-wybory — bierzemy
# jeden z nich tylko jako źródło czystej geometrii (bez wyników).
KRAKOW_GEOMETRY_SOURCE = PROCESSED_DIR / "sejm2023.geojson"


def load_quality_map() -> dict[str, str]:
    if not QUALITY_PATH.exists():
        return {}
    report = json.loads(QUALITY_PATH.read_text(encoding="utf-8"))
    return {r["teryt"]: r.get("quality", "approximate") for r in report if "teryt" in r}


def collect_geometry() -> gpd.GeoDataFrame:
    """Zbiera samą geometrię obwodów (teryt, obwod, quality) z Krakowa (oficjalne
    MSIP) i wszystkich wygenerowanych gmin (data/generated/*.geojson)."""
    quality_map = load_quality_map()
    parts = []

    krakow = gpd.read_file(KRAKOW_GEOMETRY_SOURCE)[["obwod", "geometry"]].copy()
    krakow["teryt"] = KRAKOW_TERYT
    krakow["quality"] = "official"
    parts.append(krakow)

    for geojson_path in sorted(GENERATED_DIR.glob("*.geojson")):
        teryt = geojson_path.stem
        if teryt == KRAKOW_TERYT:
            # Kraków ma już oficjalne granice MSIP wyżej — plik w data/generated/
            # to tylko artefakt walidacji generatora PRG z Etapu 4.4, nie do kafelkowania.
            continue
        gdf = gpd.read_file(geojson_path)[["obwod", "geometry"]].copy()
        gdf["teryt"] = teryt
        gdf["quality"] = quality_map.get(teryt, "approximate")
        parts.append(gdf)

    combined = pd.concat(parts, ignore_index=True)
    combined = gpd.GeoDataFrame(combined, geometry="geometry", crs="EPSG:4326")
    combined["obwod"] = combined["obwod"].astype(int)
    combined["key"] = combined["teryt"] + "_" + combined["obwod"].astype(str)
    return combined[["key", "teryt", "obwod", "quality", "geometry"]]


def build_pmtiles(gdf: gpd.GeoDataFrame) -> Path:
    if shutil.which("tippecanoe") is None:
        raise SystemExit(
            "Brak binarki tippecanoe w PATH. Zbuduj ją (brew install tippecanoe, "
            "lub ze źródeł github.com/felt/tippecanoe) i spróbuj ponownie."
        )

    TILES_WORK_DIR.mkdir(parents=True, exist_ok=True)
    source_path = TILES_WORK_DIR / "obwody_source.geojson"
    if source_path.exists():
        source_path.unlink()
    gdf.to_file(source_path, driver="GeoJSON")

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "tippecanoe",
            "-o", str(PMTILES_OUTPUT),
            "-l", "obwody",
            "-zg",
            "--force",
            "--simplification=10",
            "--no-tile-compression",
            str(source_path),
        ],
        check=True,
    )
    return PMTILES_OUTPUT


def build_results_json() -> None:
    """Dla każdych wyborów w manifeście łączy wyniki wszystkich `areas` w jeden
    plik JSON kluczowany `{teryt}_{obwod}` — to jest to, co klient ładuje przy
    zmianie wyborów i wstrzykuje jako feature-state do warstwy wektorowej."""
    manifest_path = PROCESSED_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    for entry in manifest["elections"]:
        election_id = entry["id"]
        combined: dict[str, dict] = {}
        for area in entry["areas"]:
            area_path = PROCESSED_DIR / area["file"]
            if not area_path.exists():
                continue
            gdf = gpd.read_file(area_path)
            for row in gdf.itertuples(index=False):
                key = f"{area['teryt']}_{int(row.obwod)}"
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
                    "komisja": getattr(row, "komisja", "") or "",
                    "dzielnica": getattr(row, "dzielnica", "") or "",
                    "wyborcy": int(wyborcy) if wyborcy is not None and pd.notna(wyborcy) else None,
                    "opis_granic": getattr(row, "opis_granic", "") or "",
                }

        out_path = PROCESSED_DIR / f"results_{election_id}.json"
        out_path.write_text(json.dumps(combined, ensure_ascii=False), encoding="utf-8")
        print(f"  {election_id}: {len(combined)} obwodów -> {out_path.name}")


def main() -> None:
    print("Zbieranie geometrii obwodów...")
    gdf = collect_geometry()
    print(f"  {len(gdf)} obwodów z {gdf['teryt'].nunique()} gmin")

    print("Budowa kafelków PMTiles...")
    output_path = build_pmtiles(gdf)
    size_mb = output_path.stat().st_size / 1e6
    print(f"  Zapisano: {output_path} ({size_mb:.2f} MB)")

    print("Budowa results_{wybory}.json...")
    build_results_json()

    print("\nGotowe.")


if __name__ == "__main__":
    main()
