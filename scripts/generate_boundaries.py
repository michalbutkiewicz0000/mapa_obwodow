#!/usr/bin/env python3
"""Batch generator poligonów obwodów z opisu PKW dla dowolnej listy gmin.

Dla każdej gminy generuje poligony obwodów (Voronoi z punktów adresowych PRG),
łączy je z wynikami wyborów o pełnym pokryciu krajowym (patrz build_gminy.
COUNTRY_ELECTIONS) i dopisuje wpisy `areas` do manifest.json, żeby dana gmina
była klikalna na mapie tak samo jak Kraków.

Użycie:
    python scripts/generate_boundaries.py --teryt 126101 020101
    python scripts/generate_boundaries.py --all --limit 20
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_gminy import COUNTRY_ELECTIONS, load_gmina_boundaries, load_national_results
from fetch_addresses_prg import fetch_addresses_for_teryt
from parse_opis_granic import parse_opis_granic
from run_pilot import EXCEL_PATH, assign_addresses, assignment_report, build_voronoi_polygons
from utils import PROCESSED_DIR, ROOT, load_config, normalize_obwod, normalize_teryt

OUTPUT_DIR = ROOT / "data" / "generated"
CACHE_DIR = ROOT / "data" / "raw" / "prg"

# Próg jakości oparty wyłącznie o assignment_rate. Pierwotny plan zakładał też
# "0 brakujących obwodów", ale w praktyce niemal każda gmina ma 1-3 obwody czysto
# instytucjonalne (szpital, więzienie, dom opieki) bez adresów ulicznych w PRG —
# ich brak nie świadczy o gorszej jakości wygenerowanych granic, więc nie liczy
# się do progu (patrz STAN_PROJEKTU.md, Etap 4).
QUALITY_GENERATED_MIN_RATE = 0.90
QUALITY_APPROXIMATE_MIN_RATE = 0.70

# Warszawa jest w rejestrze PKW zapisana jako 18 osobnych "gmin" (dzielnic,
# TERYT 146502-146519) z niezależną numeracją obwodów w jednej wspólnej puli
# (numery unikalne w skali całego miasta — zweryfikowane na danych). Granice
# gmin i PRG znają tylko jeden kod całego miasta: 146501.
WARSZAWA_AREA_TERYT = "146501"
WARSZAWA_DZIELNICE_TERYT = [f"1465{n:02d}" for n in range(2, 20)]


def load_excel() -> pd.DataFrame:
    df = pd.read_excel(EXCEL_PATH)
    df["teryt"] = df["TERYT gminy"].map(normalize_teryt).str.zfill(6)
    df["obwod"] = df["Numer"].map(normalize_obwod)
    return df


def build_rules_for_gmina(excel: pd.DataFrame, teryt: str) -> tuple[list, str, dict, dict]:
    """Zwraca (reguły, nazwa gminy, obwod->teryt, obwod->dzielnica).

    Dla zwykłej gminy `obwod->teryt` mapuje każdy obwód na ten sam, stały teryt
    (spójne z Warszawą, gdzie mapowanie faktycznie się różni per obwód) —
    dzięki temu `generate_for_teryt`/`export_election_areas` nie potrzebują
    osobnej gałęzi dla obu przypadków."""
    if teryt == WARSZAWA_AREA_TERYT:
        subset = excel[excel["teryt"].isin(WARSZAWA_DZIELNICE_TERYT)]
        if subset.empty:
            raise ValueError("Brak obwodów w Excelu dla Warszawy")
        gmina_name = "Warszawa"
        obwod_dzielnica = dict(zip(subset["obwod"], subset["Gmina"]))
    else:
        subset = excel[excel["teryt"] == teryt]
        if subset.empty:
            raise ValueError(f"Brak obwodów w Excelu dla teryt={teryt}")
        gmina_name = subset["Gmina"].iloc[0]
        obwod_dzielnica = {}

    rules = [
        parse_opis_granic(int(row["obwod"]), str(row["Typ obszaru"]), str(row["Opis granic"]))
        for _, row in subset.iterrows()
    ]
    obwod_teryt = dict(zip(subset["obwod"], subset["teryt"]))
    return rules, gmina_name, obwod_teryt, obwod_dzielnica


def quality_flag(report: dict) -> str:
    if report["assignment_rate"] >= QUALITY_GENERATED_MIN_RATE:
        return "generated"
    if report["assignment_rate"] >= QUALITY_APPROXIMATE_MIN_RATE:
        return "approximate"
    return "poor"


def export_election_areas(
    area_teryt: str,
    gmina_name: str,
    polygons: gpd.GeoDataFrame,
    quality: str,
    national_results: dict[str, pd.DataFrame],
) -> dict[str, dict]:
    """Łączy wygenerowane poligony z wynikami per wybory i eksportuje GeoJSON
    gotowy do wpięcia jako `area` w manifest.json.

    `polygons` musi mieć własną kolumnę `teryt` per wiersz (dla zwykłej gminy
    stały, dla Warszawy różny per dzielnica) — złączenie z wynikami idzie po
    `["teryt", "obwod"]`, nie samym `obwod`, żeby poprawnie obsłużyć oba
    przypadki bez osobnej gałęzi kodu."""
    bounds = polygons.total_bounds  # minx, miny, maxx, maxy
    center = [round((bounds[1] + bounds[3]) / 2, 4), round((bounds[0] + bounds[2]) / 2, 4)]
    display_name = gmina_name.replace("m. ", "").replace("gm. ", "")
    has_dzielnica = "dzielnica" in polygons.columns

    areas = {}
    for election_id, results in national_results.items():
        merged = polygons.merge(results, on=["teryt", "obwod"], how="left")
        matched = int(merged["winner"].notna().sum())
        total = int(len(merged))

        merged["results_json"] = merged["results"].apply(
            lambda value: json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else "{}"
        )
        cols = ["obwod", "teryt", "komisja", "frekwencja", "glosy_wazne", "winner", "results_json"]
        if has_dzielnica:
            cols.append("dzielnica")
        cols.append("geometry")
        export = merged[cols].copy()
        export = export.rename(columns={"results_json": "results"})
        export["frekwencja"] = export["frekwencja"].fillna(0)
        export["glosy_wazne"] = export["glosy_wazne"].fillna(0)

        file_name = f"{election_id}_{area_teryt}.geojson"
        export.to_file(PROCESSED_DIR / file_name, driver="GeoJSON")

        areas[election_id] = {
            "name": display_name,
            "teryt": area_teryt,
            "file": file_name,
            "center": center,
            "zoom": 10 if area_teryt == WARSZAWA_AREA_TERYT else 13,
            "quality": quality,
            "matched": matched,
            "total": total,
        }
    return areas


def generate_for_teryt(
    teryt: str,
    excel: pd.DataFrame,
    gminy_boundaries: gpd.GeoDataFrame,
    national_results: dict[str, pd.DataFrame],
) -> dict:
    print(f"\n=== Generowanie: {teryt} ===")
    rules_list, gmina_name, obwod_teryt, obwod_dzielnica = build_rules_for_gmina(excel, teryt)
    print(f"  Gmina: {gmina_name}, obwody: {len(rules_list)}")

    addresses = fetch_addresses_for_teryt(teryt, CACHE_DIR)
    print(f"  Adresy PRG: {len(addresses)}")

    assigned = assign_addresses(addresses, rules_list)
    report = assignment_report(assigned, len(rules_list))
    print(
        f"  Przypisano: {report['addresses_assigned']}/{report['addresses_total']} "
        f"({report['assignment_rate']:.1%})"
    )
    print(f"  Obwody z punktami: {report['obwody_with_points']}/{report['obwody_expected']}")

    boundary_row = gminy_boundaries[gminy_boundaries["teryt"] == teryt]
    boundary = boundary_row.geometry.iloc[0] if not boundary_row.empty else None

    polygons = build_voronoi_polygons(assigned, boundary=boundary)
    polygons["obwod"] = polygons["obwod"].astype(int)
    polygons["teryt"] = polygons["obwod"].map(obwod_teryt).fillna(teryt)
    if obwod_dzielnica:
        polygons["dzielnica"] = polygons["obwod"].map(obwod_dzielnica)

    quality = quality_flag(report)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    geojson_path = OUTPUT_DIR / f"{teryt}.geojson"
    polygons.to_file(geojson_path, driver="GeoJSON")
    print(f"  Zapisano: {geojson_path} (jakość: {quality})")

    areas = export_election_areas(teryt, gmina_name, polygons, quality, national_results)

    return {
        "teryt": teryt,
        "gmina_nazwa": gmina_name,
        "quality": quality,
        "file": f"{teryt}.geojson",
        "areas": areas,
        **report,
    }


def update_manifest(quality_report: list[dict]) -> None:
    manifest_path = PROCESSED_DIR / "manifest.json"
    if not manifest_path.exists():
        print("Brak manifest.json — pomijam wpięcie do manifestu.", file=sys.stderr)
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    for entry in manifest["elections"]:
        election_id = entry["id"]
        for gmina_report in quality_report:
            if "areas" not in gmina_report or election_id not in gmina_report["areas"]:
                continue
            teryt = gmina_report["teryt"]
            new_area = gmina_report["areas"][election_id]
            # Upsert: nadpisz istniejący wpis dla tego teryt (np. po ponownym
            # wygenerowaniu gminy z poprawką), zamiast go po cichu pomijać.
            entry["areas"] = [area for area in entry["areas"] if area["teryt"] != teryt]
            entry["areas"].append(new_area)

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Manifest zaktualizowany: {manifest_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--teryt", nargs="+", help="lista TERYT gmin (6 cyfr, z zerem wiodącym)")
    parser.add_argument("--all", action="store_true", help="wszystkie gminy z Excela PKW")
    parser.add_argument("--limit", type=int, default=None, help="ogranicz liczbę gmin (z --all)")
    parser.add_argument("--no-manifest", action="store_true", help="nie wpinaj do manifest.json")
    args = parser.parse_args()

    excel = load_excel()

    if args.all:
        teryty = sorted(excel["teryt"].dropna().unique())
        if args.limit:
            teryty = teryty[: args.limit]
    elif args.teryt:
        teryty = [t.zfill(6) for t in args.teryt]
    else:
        parser.error("Podaj --teryt <lista> lub --all")
        return

    print(f"Gminy do przetworzenia: {len(teryty)}")
    gminy_boundaries = load_gmina_boundaries(simplify=None)

    config = load_config()
    elections_by_id = {e["id"]: e for e in config["elections"]}
    print("Wczytywanie wyników ogólnopolskich (raz, dla wszystkich gmin)...")
    national_results = {
        election_id: load_national_results(elections_by_id[election_id])
        for election_id in COUNTRY_ELECTIONS
        if election_id in elections_by_id
    }

    quality_report = []
    for teryt in teryty:
        try:
            quality_report.append(generate_for_teryt(teryt, excel, gminy_boundaries, national_results))
        except Exception:
            print(f"Błąd dla {teryt}:", file=sys.stderr)
            traceback.print_exc()
            quality_report.append({"teryt": teryt, "quality": "failed", "error": traceback.format_exc()})

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    quality_path = OUTPUT_DIR / "quality.json"
    quality_path.write_text(
        json.dumps(
            [{k: v for k, v in r.items() if k != "areas"} for r in quality_report],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nRaport jakości: {quality_path}")

    ok = sum(1 for r in quality_report if r.get("quality") in ("generated", "approximate", "poor"))
    print(f"Podsumowanie: {ok}/{len(quality_report)} przetworzone bez błędu krytycznego")

    if not args.no_manifest:
        update_manifest(quality_report)


if __name__ == "__main__":
    main()
