#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import traceback
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from fetch_addresses_osm import fetch_addresses_for_place
from parse_opis_granic import parse_opis_granic, resolve_obwod
from utils import RAW_DIR, download, extract_zip, find_shapefile, normalize_teryt

EXCEL_PATH = ROOT / "data" / "metadata" / "obwody_glosowania_utf8.xlsx"
OUTPUT_DIR = ROOT / "data" / "pilot"
CACHE_DIR = ROOT / "data" / "raw" / "pilot"

PILOT_GMINY = [
    {"name": "m. Bolesławiec", "place": "Bolesławiec", "teryt": "20101"},
    {"name": "m. Kraków", "place": "Kraków", "teryt": "126101", "validate_msip": True},
]

# Wyniki przed poprawkami parsera z Etapu 2.4 (streets_equal substring, brak
# rozstrzygania konfliktów wg specyficzności, bufor+dissolve zamiast Voronoi) —
# zachowane do porównania przed/po w raporcie końcowym.
BASELINE_METRICS = {
    "m. Bolesławiec": {
        "addresses_total": 6405,
        "addresses_assigned": 5472,
        "assignment_rate": 0.8543,
        "conflicts": 389,
        "obwody_missing": 4,
    },
    "m. Kraków": {
        "addresses_total": 95057,
        "addresses_assigned": 51187,
        "assignment_rate": 0.5385,
        "conflicts": 33479,
        "obwody_missing": 140,
        "mean_iou": 0.172,
        "address_point_accuracy": 0.9709,
    },
}

MSIP_KRAKOW_URL = "https://msip.um.krakow.pl/Dane/2023_10_15_Wybory_Parlamentarne.zip"
# Ten sam shapefile jest już pobrany przez build_dataset.py dla sejm2023 — reużywamy go
# zamiast pobierać ponownie.
SEJM2023_BOUNDARIES_DIR = RAW_DIR / "sejm2023" / "boundaries"


def load_obwody(gmina_name: str, teryt: str) -> pd.DataFrame:
    df = pd.read_excel(EXCEL_PATH)
    df["teryt"] = df["TERYT gminy"].map(normalize_teryt)
    subset = df[(df["Gmina"] == gmina_name) & (df["teryt"] == teryt)].copy()
    subset["obwod"] = subset["Numer"].astype(int)
    return subset


def build_rules(subset: pd.DataFrame) -> list:
    rules = []
    for _, row in subset.iterrows():
        rules.append(parse_opis_granic(int(row["obwod"]), str(row["Typ obszaru"]), str(row["Opis granic"])))
    return rules


def _assign_chunk(args: tuple[list[tuple[str, object]], list]) -> list[tuple[object, int]]:
    rows, rules_list = args
    return [resolve_obwod(rules_list, street, number) for street, number in rows]


def assign_addresses(addresses: gpd.GeoDataFrame, rules_list: list) -> gpd.GeoDataFrame:
    assigned = addresses.copy()
    total = len(assigned)
    rows = list(zip(assigned["street"], assigned["number"]))

    workers = min(os.cpu_count() or 1, 10)
    if total < 2000 or workers <= 1:
        chunk_results = _assign_chunk((rows, rules_list))
    else:
        chunk_size = max(1, -(-total // workers))  # ceil
        chunks = [rows[i : i + chunk_size] for i in range(0, total, chunk_size)]
        print(f"    przypisywanie adresów: {total} adresów na {len(chunks)} procesach", flush=True)
        chunk_results = []
        done_chunks = 0
        with ProcessPoolExecutor(max_workers=workers) as executor:
            for result in executor.map(_assign_chunk, [(chunk, rules_list) for chunk in chunks]):
                chunk_results.extend(result)
                done_chunks += 1
                pct = round(done_chunks / len(chunks) * 100)
                print(f"    przypisywanie adresów: {pct}% ({done_chunks}/{len(chunks)} paczek)", flush=True)

    assigned["obwod"] = [pair[0] for pair in chunk_results]
    assigned["match_count"] = [pair[1] for pair in chunk_results]
    return assigned


def load_gmina_boundary() -> object | None:
    """Granica gminy do przycięcia diagramu Voronoi — reużywa shapefile MSIP Krakowa
    pobrany już przez build_dataset.py. Brak odpowiednika dla Bolesławca (brak
    oficjalnych poligonów) — wtedy przycinanie jest pomijane."""
    if not SEJM2023_BOUNDARIES_DIR.exists():
        return None
    shp = find_shapefile(SEJM2023_BOUNDARIES_DIR, "Obwody")
    official = gpd.read_file(shp).to_crs(epsg=4326)
    return official.union_all()


def build_voronoi_polygons(assigned: gpd.GeoDataFrame, boundary=None) -> gpd.GeoDataFrame:
    points = assigned.dropna(subset=["obwod"]).copy()
    if points.empty:
        raise RuntimeError("Brak przypisanych punktów adresowych")

    multipoint = shapely.geometry.MultiPoint(points.geometry.tolist())
    envelope = boundary if boundary is not None else multipoint.convex_hull.buffer(0.01)
    regions = shapely.voronoi_polygons(multipoint, extend_to=envelope)

    cells = gpd.GeoDataFrame(geometry=list(regions.geoms), crs=points.crs)
    joined = gpd.sjoin(cells, points[["obwod", "geometry"]], predicate="contains", how="inner")
    if boundary is not None:
        joined["geometry"] = joined.geometry.intersection(boundary)
    dissolved = joined.dissolve(by="obwod").reset_index()
    dissolved = dissolved[["obwod", "geometry"]]
    return dissolved


def assignment_report(assigned: gpd.GeoDataFrame, expected_obwody: int) -> dict:
    total = len(assigned)
    matched = assigned["obwod"].notna().sum()
    has_match_count = "match_count" in assigned
    # Konflikty nierozstrzygnięte mimo preferowania bardziej specyficznej reguły.
    conflicts = (
        int(((assigned["match_count"] > 1) & assigned["obwod"].isna()).sum()) if has_match_count else 0
    )
    # Konflikty rozstrzygnięte dzięki specyficzności reguły (dla informacji).
    resolved_by_specificity = (
        int(((assigned["match_count"] > 1) & assigned["obwod"].notna()).sum()) if has_match_count else 0
    )
    unmatched_no_rule = int(
        ((assigned["match_count"] == 0) & assigned["obwod"].isna()).sum()
    ) if has_match_count else int(total - matched)
    covered_obwody = assigned["obwod"].dropna().nunique()
    return {
        "addresses_total": int(total),
        "addresses_assigned": int(matched),
        "assignment_rate": round(matched / total, 4) if total else 0,
        "conflicts": conflicts,
        "resolved_by_specificity": resolved_by_specificity,
        "unmatched_no_rule": unmatched_no_rule,
        "obwody_with_points": int(covered_obwody),
        "obwody_expected": int(expected_obwody),
        "obwody_missing": int(expected_obwody - covered_obwody),
    }


def validate_against_msip(generated: gpd.GeoDataFrame, assigned: gpd.GeoDataFrame) -> dict:
    if SEJM2023_BOUNDARIES_DIR.exists():
        shp = find_shapefile(SEJM2023_BOUNDARIES_DIR, "Obwody")
    else:
        zip_path = CACHE_DIR / "krakow_msip.zip"
        download(MSIP_KRAKOW_URL, zip_path)
        extract_dir = CACHE_DIR / "krakow_msip"
        extract_zip(zip_path, extract_dir)
        shp = find_shapefile(extract_dir, "Obwody")
    official = gpd.read_file(shp).to_crs(epsg=4326)
    official["obwod"] = official["nr_obwodu"].astype(int)

    joined = generated.merge(official[["obwod", "geometry"]], on="obwod", suffixes=("_gen", "_off"))
    ious = []
    for _, row in joined.iterrows():
        gen = row["geometry_gen"]
        off = row["geometry_off"]
        if gen.is_empty or off.is_empty:
            continue
        inter = gen.intersection(off).area
        union = gen.union(off).area
        ious.append(inter / union if union else 0)

    points = assigned.dropna(subset=["obwod"]).copy()
    official_sindex = official.sindex
    correct = 0
    checked = 0
    for _, point in points.iterrows():
        obwod = int(point["obwod"])
        possible = list(official_sindex.intersection(point.geometry.bounds))
        official_match = None
        for idx in possible:
            geom = official.geometry.iloc[idx]
            if geom.contains(point.geometry):
                official_match = int(official.iloc[idx]["obwod"])
                break
        if official_match is None:
            continue
        checked += 1
        if official_match == obwod:
            correct += 1

    return {
        "polygons_compared": len(ious),
        "mean_iou": round(sum(ious) / len(ious), 4) if ious else None,
        "median_iou": round(sorted(ious)[len(ious) // 2], 4) if ious else None,
        "address_point_accuracy": round(correct / checked, 4) if checked else None,
        "address_points_checked": checked,
    }


def run_pilot_for_gmina(gmina: dict) -> dict:
    print(f"\n=== Pilot: {gmina['name']} ===")
    subset = load_obwody(gmina["name"], gmina["teryt"])
    rules_list = build_rules(subset)
    print(f"  Obwody w Excelu: {len(subset)}")
    print(f"  Sparsowane reguły ulic: {sum(len(r.streets) for r in rules_list)}")

    addresses = fetch_addresses_for_place(gmina["place"], CACHE_DIR / "osm")
    print(f"  Adresy OSM w bbox: {len(addresses)}")

    assigned = assign_addresses(addresses, rules_list)
    report = assignment_report(assigned, len(subset))
    print(f"  Przypisano adresów: {report['addresses_assigned']}/{report['addresses_total']} ({report['assignment_rate']:.1%})")
    print(f"  Obwody z punktami: {report['obwody_with_points']}/{report['obwody_expected']}")

    # Granica gminy do przycięcia diagramu Voronoi jest dziś dostępna tylko dla Krakowa
    # (reużywamy shapefile MSIP pobrany dla sejm2023); inne gminy nie mają jeszcze
    # źródła granic administracyjnych (patrz Etap 4 planu — PRG).
    boundary = load_gmina_boundary() if gmina.get("validate_msip") else None
    polygons = build_voronoi_polygons(assigned, boundary=boundary)
    slug = gmina["place"].lower().replace("ł", "l").replace("ó", "o")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    assigned_path = OUTPUT_DIR / f"{slug}_assigned.parquet"
    polygons_path = OUTPUT_DIR / f"{slug}_polygons.geojson"
    assigned.drop(columns="geometry").to_parquet(assigned_path, index=False)
    polygons.to_file(polygons_path, driver="GeoJSON")
    print(f"  Zapisano: {polygons_path}")

    result = {"gmina": gmina["name"], "teryt": gmina["teryt"], **report}
    if gmina.get("validate_msip"):
        try:
            validation = validate_against_msip(polygons, assigned)
            result["validation"] = validation
            print(f"  Walidacja MSIP — średnie IoU: {validation['mean_iou']}")
            print(f"  Walidacja MSIP — trafność punktów: {validation['address_point_accuracy']}")
        except Exception:
            print("  Walidacja MSIP nie powiodła się:", file=sys.stderr)
            traceback.print_exc()
            result["validation_error"] = traceback.format_exc()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    gmina_report_path = OUTPUT_DIR / f"{slug}_report.json"
    gmina_report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  Zapisano raport gminy: {gmina_report_path}")
    return result


def build_comparison(reports: list[dict]) -> dict:
    comparison = {}
    for report in reports:
        name = report.get("gmina")
        before = BASELINE_METRICS.get(name)
        if not before or "error" in report:
            continue
        after = {
            "addresses_total": report.get("addresses_total"),
            "addresses_assigned": report.get("addresses_assigned"),
            "assignment_rate": report.get("assignment_rate"),
            "conflicts": report.get("conflicts"),
            "obwody_missing": report.get("obwody_missing"),
        }
        validation = report.get("validation") or {}
        if "mean_iou" in before:
            after["mean_iou"] = validation.get("mean_iou")
            after["address_point_accuracy"] = validation.get("address_point_accuracy")
        comparison[name] = {"before_etap_2_4": before, "after_etap_2_4": after}
    return comparison


def main() -> None:
    reports = []
    for gmina in PILOT_GMINY:
        try:
            reports.append(run_pilot_for_gmina(gmina))
        except Exception:
            print(f"Pilot dla {gmina['name']} nie powiódł się:", file=sys.stderr)
            traceback.print_exc()
            reports.append({"gmina": gmina["name"], "teryt": gmina["teryt"], "error": traceback.format_exc()})

    output = {
        "gminy": reports,
        "comparison_before_after_etap_2_4": build_comparison(reports),
    }
    report_path = OUTPUT_DIR / "pilot_report.json"
    report_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nRaport pilota: {report_path}")


if __name__ == "__main__":
    main()
