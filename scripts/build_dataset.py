from __future__ import annotations

import json
import re
import sys
import traceback
from pathlib import Path

import geopandas as gpd
import pandas as pd

from utils import (
    CONFIG_PATH,
    PROCESSED_DIR,
    RAW_DIR,
    ROOT,
    download,
    ensure_dirs,
    extract_zip,
    find_shapefile,
    load_config,
    normalize_obwod,
    normalize_teryt,
    parse_int,
    read_csv_file,
    read_csv_from_zip,
    short_party_name,
)


def load_metadata(config: dict) -> pd.DataFrame:
    path = config["city"].get("metadata_xlsx")
    if not path:
        return pd.DataFrame()
    xlsx_path = Path(path)
    if not xlsx_path.is_absolute():
        xlsx_path = ROOT / xlsx_path
    if not xlsx_path.exists():
        return pd.DataFrame()
    df = pd.read_excel(xlsx_path)
    df["teryt"] = df["TERYT gminy"].map(normalize_teryt)
    df["obwod"] = df["Numer"].map(normalize_obwod)
    return df


def load_boundaries(election: dict, election_id: str) -> gpd.GeoDataFrame:
    boundaries_cfg = election["boundaries"]
    local_path = boundaries_cfg.get("local_path")
    if local_path:
        zip_path = Path(local_path)
        if not zip_path.is_absolute():
            zip_path = ROOT / zip_path
        if not zip_path.exists():
            raise FileNotFoundError(f"Brak lokalnego pliku granic: {zip_path}")
    else:
        zip_path = RAW_DIR / election_id / "boundaries.zip"
        download(boundaries_cfg["url"], zip_path)
    extract_dir = RAW_DIR / election_id / "boundaries"
    extract_zip(zip_path, extract_dir)
    shp = find_shapefile(extract_dir, election["boundaries"]["layer_suffix"])
    gdf = gpd.read_file(shp)
    gdf = gdf.to_crs(epsg=4326)
    gdf["obwod"] = gdf["nr_obwodu"].map(normalize_obwod)
    gdf["dzielnica"] = gdf.get("dzielnica", "")
    return gdf


def parse_sejm_results(df: pd.DataFrame, teryt: str) -> pd.DataFrame:
    df["teryt"] = df["TERYT Gminy"].map(normalize_teryt)
    df["obwod"] = df["Nr komisji"].map(normalize_obwod)
    df = df[df["teryt"] == teryt].copy()

    party_cols = [
        col
        for col in df.columns
        if col.startswith("KOMITET") or col.startswith("KOALICYJNY")
    ]
    rows = []
    for _, row in df.iterrows():
        eligible = parse_int(row.get("Liczba wyborców uprawnionych do głosowania"))
        voted = parse_int(row.get("Liczba wyborców, którym wydano karty do głosowania"))
        valid = parse_int(row.get("Liczba głosów ważnych oddanych łącznie na wszystkie listy kandydatów"))
        results = {}
        for col in party_cols:
            votes = parse_int(row.get(col))
            if votes > 0:
                results[short_party_name(col)] = votes
        winner = max(results, key=results.get) if results else None
        rows.append(
            {
                "obwod": row["obwod"],
                "frekwencja": round(voted / eligible, 4) if eligible else 0,
                "glosy_wazne": valid,
                "winner": winner,
                "results": results,
                "komisja": row.get("Siedziba", ""),
            }
        )
    return pd.DataFrame(rows)


def parse_samorzad_results(df: pd.DataFrame, teryt: str) -> pd.DataFrame:
    df["teryt"] = df["Teryt Gminy"].map(normalize_teryt)
    df["obwod"] = df["Nr komisji"].map(normalize_obwod)
    df = df[df["teryt"] == teryt].copy()

    list_cols = [col for col in df.columns if str(col).startswith("Głosy na listę nr")]
    rows = []
    for _, row in df.iterrows():
        eligible = parse_int(row.get("Liczba wyborców uprawnionych do głosowania"))
        voted = parse_int(
            row.get(
                "Liczba wyborców, którym wydano karty do głosowania w lokalu wyborczym oraz w głosowaniu korespondencyjnym (łącznie)"
            )
        )
        valid = parse_int(
            row.get("Liczba głosów ważnych oddanych łącznie na wszystkie listy kandydatów")
        )
        results = {}
        for col in list_cols:
            votes = parse_int(row.get(col))
            if votes <= 0:
                continue
            match = re.search(r"Głosy na listę nr \d+ - (.+)$", str(col))
            label = short_party_name(match.group(1) if match else str(col))
            results[label] = results.get(label, 0) + votes
        winner = max(results, key=results.get) if results else None
        rows.append(
            {
                "obwod": row["obwod"],
                "frekwencja": round(voted / eligible, 4) if eligible else 0,
                "glosy_wazne": valid,
                "winner": winner,
                "results": results,
                "komisja": row.get("Siedziba", ""),
            }
        )
    return pd.DataFrame(rows)


def parse_prez_results(df: pd.DataFrame, teryt: str) -> pd.DataFrame:
    teryt_col = next((col for col in df.columns if "TERYT" in col.upper() and "GMI" in col.upper()), None)
    obwod_col = next((col for col in df.columns if "NR" in col.upper() and "KOMIS" in col.upper()), "Nr komisji")
    if not teryt_col:
        raise ValueError("Nie znaleziono kolumny TERYT w wynikach prezydenckich")
    df["teryt"] = df[teryt_col].map(normalize_teryt)
    df["obwod"] = df[obwod_col].map(normalize_obwod)
    df = df[df["teryt"] == teryt].copy()

    eligible_col = next(
        (col for col in df.columns if "Liczba wyborców uprawnionych do głosowania" in str(col)), None
    )
    voted_col = next(
        (
            col
            for col in df.columns
            if "wydano karty do głosowania" in str(col) and "łącznie" in str(col)
        ),
        None,
    )
    valid_col = next(
        (col for col in df.columns if str(col).startswith("Liczba głosów ważnych oddanych łącznie")),
        None,
    )
    if valid_col is None:
        raise ValueError("Nie znaleziono kolumny z liczbą głosów ważnych")

    # Kolumny kandydatów to wszystkie kolumny po kolumnie z liczbą głosów ważnych.
    valid_idx = df.columns.get_loc(valid_col)
    candidate_cols = [col for col in df.columns[valid_idx + 1 :] if col not in ("teryt", "obwod")]

    rows = []
    for _, row in df.iterrows():
        eligible = parse_int(row.get(eligible_col)) if eligible_col else 0
        voted = parse_int(row.get(voted_col)) if voted_col else 0
        valid = parse_int(row.get(valid_col))
        results = {}
        for col in candidate_cols:
            votes = parse_int(row.get(col))
            if votes > 0:
                results[str(col).strip()] = votes
        winner = max(results, key=results.get) if results else None
        rows.append(
            {
                "obwod": row["obwod"],
                "frekwencja": round(voted / eligible, 4) if eligible else 0,
                "glosy_wazne": valid,
                "winner": winner,
                "results": results,
                "komisja": row.get("Siedziba", ""),
            }
        )
    return pd.DataFrame(rows)


def load_results(election: dict, election_id: str, teryt: str) -> pd.DataFrame:
    results_cfg = election["results"]
    result_type = results_cfg["type"]

    local_path = results_cfg.get("local_path")
    if local_path:
        csv_path = Path(local_path)
        if not csv_path.is_absolute():
            csv_path = ROOT / csv_path
        if not csv_path.exists():
            raise FileNotFoundError(f"Brak lokalnego pliku wyników: {csv_path}")
        df = read_csv_file(csv_path)
    else:
        url = results_cfg["url"]
        zip_path = RAW_DIR / election_id / "results.zip"
        download(url, zip_path)
        inner_name = results_cfg.get("voivodeship_file")
        df = read_csv_from_zip(zip_path, inner_name=inner_name)

    if result_type == "sejm_lists":
        return parse_sejm_results(df, teryt)
    if result_type == "samorzad_lists":
        return parse_samorzad_results(df, teryt)
    if result_type == "prez_candidates":
        return parse_prez_results(df, teryt)
    raise ValueError(f"Nieobsługiwany typ wyników: {result_type}")


def enrich_with_metadata(results: pd.DataFrame, metadata: pd.DataFrame, teryt: str) -> pd.DataFrame:
    if metadata.empty:
        return results
    meta = metadata[metadata["teryt"] == teryt][["obwod", "Pełna siedziba", "Wyborcy", "Opis granic"]]
    meta = meta.rename(
        columns={
            "Pełna siedziba": "komisja_meta",
            "Wyborcy": "wyborcy",
            "Opis granic": "opis_granic",
        }
    )
    merged = results.merge(meta, on="obwod", how="left")
    merged["komisja"] = merged["komisja"].where(merged["komisja"].astype(bool), merged["komisja_meta"])
    return merged.drop(columns=["komisja_meta"], errors="ignore")


def build_election_dataset(election: dict, config: dict, metadata: pd.DataFrame) -> dict:
    election_id = election["id"]
    teryt = config["city"]["teryt"]
    print(f"Budowanie: {election_id}")

    gdf = load_boundaries(election, election_id)
    results = load_results(election, election_id, teryt)
    results = enrich_with_metadata(results, metadata, teryt)

    merged = gdf.merge(results, on="obwod", how="left")
    matched = merged["winner"].notna().sum()
    total = len(merged)
    print(f"  Dopasowano wyniki: {matched}/{total}")

    merged["results_json"] = merged["results"].apply(
        lambda value: json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else "{}"
    )
    export = merged[
        [
            "obwod",
            "dzielnica",
            "komisja",
            "frekwencja",
            "glosy_wazne",
            "winner",
            "results_json",
            "wyborcy",
            "opis_granic",
            "geometry",
        ]
    ].copy()
    export = export.rename(columns={"results_json": "results"})
    export["frekwencja"] = export["frekwencja"].fillna(0)
    export["glosy_wazne"] = export["glosy_wazne"].fillna(0)

    geojson_path = PROCESSED_DIR / f"{election_id}.geojson"
    export.to_file(geojson_path, driver="GeoJSON")
    print(f"  Zapisano {geojson_path}")

    return {
        "id": election_id,
        "label": election["label"],
        "matched": int(matched),
        "total": int(total),
    }


def main() -> None:
    ensure_dirs()
    config = load_config()
    metadata = load_metadata(config)
    manifest = {
        "city": config["city"]["name"],
        "teryt": config["city"]["teryt"],
        "center": config["city"]["center"],
        "zoom": config["city"]["zoom"],
        "elections": [],
    }

    failed = []
    for election in config["elections"]:
        try:
            info = build_election_dataset(election, config, metadata)
            manifest["elections"].append(info)
        except Exception:
            print(f"Błąd dla {election['id']}:", file=sys.stderr)
            traceback.print_exc()
            failed.append(election["id"])

    manifest_path = PROCESSED_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Manifest: {manifest_path}")

    succeeded = [e["id"] for e in manifest["elections"]]
    print(f"\nPodsumowanie: {len(succeeded)} zbudowane ({', '.join(succeeded) or '-'}), "
          f"{len(failed)} nieudane ({', '.join(failed) or '-'})")

    if not succeeded:
        print("Żadne wybory nie zostały zbudowane.", file=sys.stderr)
        sys.exit(1)
    if failed:
        sys.exit(2)


if __name__ == "__main__":
    main()
