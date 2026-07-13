#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from fetch_addresses_osm import fetch_addresses_for_place
from parse_opis_granic import assign_obwod, parse_opis_granic
from utils import download, extract_zip, find_shapefile, normalize_teryt

EXCEL_PATH = ROOT / "data" / "metadata" / "obwody_glosowania_utf8.xlsx"
OUTPUT_DIR = ROOT / "data" / "pilot"
CACHE_DIR = ROOT / "data" / "raw" / "pilot"

PILOT_GMINY = [
    {"name": "m. Bolesławiec", "place": "Bolesławiec", "teryt": "20101"},
    {"name": "m. Kraków", "place": "Kraków", "teryt": "126101", "validate_msip": True},
]

MSIP_KRAKOW_URL = "https://msip.um.krakow.pl/Dane/2023_10_15_Wybory_Parlamentarne.zip"


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


def assign_addresses(addresses: gpd.GeoDataFrame, rules_list: list) -> gpd.GeoDataFrame:
    assigned = addresses.copy()
    assigned["obwod"] = assigned.apply(
        lambda row: assign_obwod(rules_list, row["street"], row["number"]),
        axis=1,
    )
    return assigned


def build_voronoi_polygons(assigned: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    points = assigned.dropna(subset=["obwod"]).copy()
    if points.empty:
        raise RuntimeError("Brak przypisanych punktów adresowych")

    buffered = points.copy()
    buffered["geometry"] = buffered.geometry.buffer(0.00025)
    dissolved = buffered.dissolve(by="obwod").reset_index()
    dissolved = dissolved[["obwod", "geometry"]]
    return dissolved


def assignment_report(assigned: gpd.GeoDataFrame, expected_obwody: int) -> dict:
    total = len(assigned)
    matched = assigned["obwod"].notna().sum()
    conflicts = 0
    covered_obwody = assigned["obwod"].dropna().nunique()
    return {
        "addresses_total": int(total),
        "addresses_assigned": int(matched),
        "assignment_rate": round(matched / total, 4) if total else 0,
        "obwody_with_points": int(covered_obwody),
        "obwody_expected": int(expected_obwody),
        "obwody_missing": int(expected_obwody - covered_obwody),
    }


def validate_against_msip(generated: gpd.GeoDataFrame, assigned: gpd.GeoDataFrame) -> dict:
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

    polygons = build_voronoi_polygons(assigned)
    slug = gmina["place"].lower().replace("ł", "l").replace("ó", "o")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    assigned_path = OUTPUT_DIR / f"{slug}_assigned.parquet"
    polygons_path = OUTPUT_DIR / f"{slug}_polygons.geojson"
    assigned.drop(columns="geometry").to_parquet(assigned_path, index=False)
    polygons.to_file(polygons_path, driver="GeoJSON")
    print(f"  Zapisano: {polygons_path}")

    result = {"gmina": gmina["name"], "teryt": gmina["teryt"], **report}
    if gmina.get("validate_msip"):
        validation = validate_against_msip(polygons, assigned)
        result["validation"] = validation
        print(f"  Walidacja MSIP — średnie IoU: {validation['mean_iou']}")
        print(f"  Walidacja MSIP — trafność punktów: {validation['address_point_accuracy']}")
    return result


def main() -> None:
    reports = [run_pilot_for_gmina(gmina) for gmina in PILOT_GMINY]
    report_path = OUTPUT_DIR / "pilot_report.json"
    report_path.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nRaport pilota: {report_path}")


if __name__ == "__main__":
    main()
