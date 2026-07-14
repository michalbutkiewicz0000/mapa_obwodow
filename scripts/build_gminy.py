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

EXCEL_PATH = ROOT / "data" / "metadata" / "obwody_glosowania_utf8.xlsx"

# Poziomy agregacji krajowej — TERYT gminy jest hierarchiczny (WWPPGG, 6 cyfr):
# pierwsze 2 cyfry = województwo, pierwsze 4 = powiat. Dissolve granic gmin po
# tych prefiksach daje granice powiatów/województw bez osobnego źródła geometrii.
LEVEL_PREFIX_LEN = {"gminy": 6, "powiaty": 4, "wojewodztwa": 2}

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


def load_unit_names() -> dict[str, dict[str, str]]:
    """Nazwy powiatów/województw z rejestru PKW, indeksowane wg prefiksu TERYT
    gminy (4 cyfry dla powiatu, 2 dla województwa) — granice gmin (JPT_NAZWA_)
    mają tylko nazwę gminy, nie nadrzędnych jednostek."""
    df = pd.read_excel(EXCEL_PATH, dtype=str)
    df["teryt"] = df["TERYT gminy"].str.strip().str.zfill(6)
    df = df.drop_duplicates("teryt")
    return {
        "powiaty": dict(zip(df["teryt"].str[:4], df["Powiat"])),
        "wojewodztwa": dict(zip(df["teryt"].str[:2], df["Województwo"])),
    }


def load_unit_boundaries(level: str, gmina_boundaries: gpd.GeoDataFrame, unit_names: dict[str, dict[str, str]]) -> gpd.GeoDataFrame:
    """Granice jednostki administracyjnej danego poziomu. Dla `gminy` to po
    prostu granice gmin; dla `powiaty`/`wojewodztwa` — dissolve granic gmin po
    prefiksie TERYT (patrz LEVEL_PREFIX_LEN)."""
    if level == "gminy":
        return gmina_boundaries.rename(columns={"gmina_nazwa": "nazwa"})

    prefix_len = LEVEL_PREFIX_LEN[level]
    gdf = gmina_boundaries[["teryt", "geometry"]].copy()
    gdf["unit_teryt"] = gdf["teryt"].str[:prefix_len]
    # dissolve zachowuje pierwotną kolumnę "teryt" (agregowaną przez "first"),
    # więc trzeba ją odrzucić przed zmianą nazwy "unit_teryt" na "teryt" —
    # inaczej powstają dwie kolumny o tej samej nazwie.
    dissolved = gdf.dissolve(by="unit_teryt").drop(columns=["teryt"]).reset_index()
    dissolved["nazwa"] = dissolved["unit_teryt"].map(unit_names[level])
    dissolved = dissolved.rename(columns={"unit_teryt": "teryt"})
    return dissolved[["teryt", "nazwa", "geometry"]]


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


def _aggregate_by_prefix(results: pd.DataFrame, prefix_len: int) -> pd.DataFrame:
    """Agreguje wyniki po prefiksie TERYT gminy o długości `prefix_len` (6 =
    gmina, 4 = powiat, 2 = województwo)."""
    results = results.dropna(subset=["teryt"]).copy()
    results["teryt"] = results["teryt"].str.zfill(6).str[:prefix_len]

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


def aggregate_by_gmina(results: pd.DataFrame) -> pd.DataFrame:
    results = results.dropna(subset=["teryt"]).copy()
    results["teryt"] = results["teryt"].str.zfill(6)
    results["teryt"] = results["teryt"].map(lambda t: WARSZAWA_DZIELNICE_TO_MIASTO.get(t, t))
    return _aggregate_by_prefix(results, 6)


def aggregate_by_powiat(results: pd.DataFrame) -> pd.DataFrame:
    # Bez remapu dzielnic Warszawy: "146502"[:4] == "146501"[:4] == "1465"
    # (Warszawa jest jednocześnie powiatem grodzkim), więc prefiks TERYT
    # naturalnie scala wszystkie dzielnice pod jednym kodem powiatu.
    return _aggregate_by_prefix(results, 4)


def aggregate_by_wojewodztwo(results: pd.DataFrame) -> pd.DataFrame:
    return _aggregate_by_prefix(results, 2)


AGGREGATORS = {
    "gminy": aggregate_by_gmina,
    "powiaty": aggregate_by_powiat,
    "wojewodztwa": aggregate_by_wojewodztwo,
}


def build_country_dataset(election: dict, level: str, boundaries: gpd.GeoDataFrame) -> dict:
    election_id = election["id"]
    print(f"Agregacja krajowa ({level}): {election_id}")

    results = load_national_results(election)
    aggregated = AGGREGATORS[level](results)

    merged = boundaries.merge(aggregated, on="teryt", how="left")
    matched = merged["winner"].notna().sum()
    total = len(merged)
    print(f"  {level}: {matched}/{total} z wynikami")

    merged["results_json"] = merged["results"].apply(
        lambda value: json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else "{}"
    )
    export = merged[["teryt", "nazwa", "frekwencja", "glosy_wazne", "winner", "results_json", "obwody", "geometry"]].copy()
    export = export.rename(columns={"results_json": "results"})
    export["frekwencja"] = export["frekwencja"].fillna(0)
    export["glosy_wazne"] = export["glosy_wazne"].fillna(0)
    export["obwody"] = export["obwody"].fillna(0)

    geojson_path = PROCESSED_DIR / f"{level}_{election_id}.geojson"
    export.to_file(geojson_path, driver="GeoJSON")
    print(f"  Zapisano {geojson_path}")

    return {"file": f"{level}_{election_id}.geojson", "unit": level, "matched": int(matched), "total": int(total)}


def main() -> None:
    config = load_config()
    manifest_path = PROCESSED_DIR / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit("Brak manifest.json — najpierw uruchom scripts/build_dataset.py")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    elections_by_id = {e["id"]: e for e in config["elections"]}
    gmina_boundaries = None
    unit_names = None
    level_boundaries: dict[str, gpd.GeoDataFrame] = {}

    for entry in manifest["elections"]:
        election_id = entry["id"]
        if election_id not in COUNTRY_ELECTIONS:
            continue
        election = elections_by_id.get(election_id)
        if election is None:
            continue
        if gmina_boundaries is None:
            gmina_boundaries = load_gmina_boundaries()
            unit_names = load_unit_names()

        entry["country"] = {}
        for level in LEVEL_PREFIX_LEN:
            try:
                if level not in level_boundaries:
                    level_boundaries[level] = load_unit_boundaries(level, gmina_boundaries, unit_names)
                entry["country"][level] = build_country_dataset(election, level, level_boundaries[level])
            except Exception as exc:
                print(f"Błąd agregacji krajowej ({level}) dla {election_id}: {exc}", file=sys.stderr)

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nManifest zaktualizowany: {manifest_path}")


if __name__ == "__main__":
    main()
