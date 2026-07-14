from __future__ import annotations

import json
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_dataset import parse_prez_results, parse_sejm_results
from utils import PROCESSED_DIR, RAW_DIR, ROOT, download, load_config, read_csv_file, read_csv_from_zip

GMINY_GEOJSON_URL = (
    "https://raw.githubusercontent.com/waszkiewiczja/"
    "GeoJSON-Polska-Wojewodztwa-Powiaty-Gminy/main/gminy.json"
)
GMINY_RAW_PATH = RAW_DIR / "gminy_boundaries.json"
SIMPLIFY_TOLERANCE = 0.003  # ~2.8 MB dla 2479 gmin, wystarczające dla widoku krajowego

# Wybory z pełnym pokryciem krajowym w źródłowym CSV. samorzad2024 obejmuje w PKW
# tylko rady gmin >20k mieszkańców (per województwo) — to nie jest komplet Polski,
# więc pomijamy tu agregację krajową dla tych wyborów (patrz STAN_PROJEKTU.md).
COUNTRY_ELECTIONS = {"sejm2023", "prez2025_t1", "prez2025_t2"}

# PKW liczy Warszawę jako 18 osobnych "gmin" (dzielnic, TERYT 146502-146519),
# ale granice gmin i punkty adresowe PRG znają tylko jeden kod całego miasta
# (146501) — bez tego mapowania poligon Warszawy zostaje bez wyników.
WARSZAWA_DZIELNICE_TO_MIASTO = {f"1465{n:02d}": "146501" for n in range(2, 20)}


def load_gmina_boundaries(simplify: float | None = SIMPLIFY_TOLERANCE) -> gpd.GeoDataFrame:
    """Granice gmin. `simplify=None` zwraca pełną geometrię (do precyzyjnego
    przycinania Voronoi w generatorze); domyślna tolerancja jest dobra dla
    lekkiego widoku krajowego."""
    download(GMINY_GEOJSON_URL, GMINY_RAW_PATH)
    gdf = gpd.read_file(GMINY_RAW_PATH).to_crs(epsg=4326)
    gdf["teryt"] = gdf["JPT_KOD_JE"].astype(str).str[:6]
    gdf["gmina_nazwa"] = gdf["JPT_NAZWA_"]
    if simplify is not None:
        gdf["geometry"] = gdf.geometry.simplify(simplify, preserve_topology=True)
    return gdf[["teryt", "gmina_nazwa", "geometry"]]


def load_national_results(election: dict) -> pd.DataFrame:
    results_cfg = election["results"]
    result_type = results_cfg["type"]

    local_path = results_cfg.get("local_path")
    if local_path:
        csv_path = Path(local_path)
        if not csv_path.is_absolute():
            csv_path = ROOT / csv_path
        df = read_csv_file(csv_path)
    else:
        url = results_cfg["url"]
        zip_path = RAW_DIR / election["id"] / "results.zip"
        download(url, zip_path)
        inner_name = results_cfg.get("voivodeship_file")
        df = read_csv_from_zip(zip_path, inner_name=inner_name)

    if result_type == "sejm_lists":
        results = parse_sejm_results(df, teryt=None)
    elif result_type == "prez_candidates":
        results = parse_prez_results(df, teryt=None)
    else:
        raise ValueError(f"Agregacja krajowa nieobsługiwana dla typu wyników: {result_type}")

    # PKW zapisuje TERYT bez wiodącego zera dla jednocyfrowych województw
    # (np. "60401" zamiast "060401") — dopełniamy tu, w jednym miejscu, żeby
    # każdy odbiorca (aggregate_by_gmina, generate_boundaries.export_election_areas)
    # dostawał już znormalizowany, 6-cyfrowy klucz zgodny z resztą pipeline'u.
    results["teryt"] = results["teryt"].str.zfill(6)
    return results


def aggregate_by_gmina(results: pd.DataFrame) -> pd.DataFrame:
    results = results.dropna(subset=["teryt"]).copy()
    results["teryt"] = results["teryt"].str.zfill(6)
    results["teryt"] = results["teryt"].map(lambda t: WARSZAWA_DZIELNICE_TO_MIASTO.get(t, t))

    rows = []
    for teryt, group in results.groupby("teryt"):
        eligible = group["eligible"].fillna(0).sum()
        voted = group["voted"].fillna(0).sum()
        combined: dict[str, int] = {}
        for res in group["results"]:
            if not isinstance(res, dict):
                continue
            for name, votes in res.items():
                combined[name] = combined.get(name, 0) + votes
        winner = max(combined, key=combined.get) if combined else None
        rows.append(
            {
                "teryt": teryt,
                "frekwencja": round(voted / eligible, 4) if eligible else 0,
                "glosy_wazne": int(group["glosy_wazne"].fillna(0).sum()),
                "winner": winner,
                "results": combined,
                "obwody": int(len(group)),
            }
        )
    return pd.DataFrame(rows)


def build_gminy_dataset(election: dict, boundaries: gpd.GeoDataFrame) -> dict:
    election_id = election["id"]
    print(f"Agregacja krajowa: {election_id}")

    results = load_national_results(election)
    aggregated = aggregate_by_gmina(results)

    merged = boundaries.merge(aggregated, on="teryt", how="left")
    matched = merged["winner"].notna().sum()
    total = len(merged)
    print(f"  Gminy z wynikami: {matched}/{total}")

    merged["results_json"] = merged["results"].apply(
        lambda value: json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else "{}"
    )
    export = merged[["teryt", "gmina_nazwa", "frekwencja", "glosy_wazne", "winner", "results_json", "obwody", "geometry"]].copy()
    export = export.rename(columns={"results_json": "results"})
    export["frekwencja"] = export["frekwencja"].fillna(0)
    export["glosy_wazne"] = export["glosy_wazne"].fillna(0)
    export["obwody"] = export["obwody"].fillna(0)

    geojson_path = PROCESSED_DIR / f"gminy_{election_id}.geojson"
    export.to_file(geojson_path, driver="GeoJSON")
    print(f"  Zapisano {geojson_path}")

    return {"file": f"gminy_{election_id}.geojson", "unit": "gmina", "matched": int(matched), "total": int(total)}


def main() -> None:
    config = load_config()
    manifest_path = PROCESSED_DIR / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit("Brak manifest.json — najpierw uruchom scripts/build_dataset.py")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    elections_by_id = {e["id"]: e for e in config["elections"]}
    boundaries = None

    for entry in manifest["elections"]:
        election_id = entry["id"]
        if election_id not in COUNTRY_ELECTIONS:
            continue
        election = elections_by_id.get(election_id)
        if election is None:
            continue
        try:
            if boundaries is None:
                boundaries = load_gmina_boundaries()
            entry["country"] = build_gminy_dataset(election, boundaries)
        except Exception as exc:
            print(f"Błąd agregacji krajowej dla {election_id}: {exc}", file=sys.stderr)

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nManifest zaktualizowany: {manifest_path}")


if __name__ == "__main__":
    main()
