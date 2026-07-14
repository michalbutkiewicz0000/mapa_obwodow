#!/usr/bin/env python3
"""Równoległe generowanie obwodów dla całej Polski.

Uruchamia generate_boundaries.generate_for_teryt() na puli procesów,
co pozwala wykorzystać wszystkie rdzenie procesora.

Użycie:
    python scripts/run_all_parallel.py [--workers N] [--limit N] [--resume]
                                       [--registry CSV] [--elections id1 id2]
                                       [--suffix SUFFIX]

Domyślnie workers = liczba rdzeni - 2 (żeby zostawić coś na system).
--resume pomija gminy, dla których istnieje już plik data/generated/{teryt}.geojson.
--suffix zapisuje surowe poligony do data/generated/{suffix}/ (jak w generate_boundaries.py).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from generate_boundaries import (
    OUTPUT_DIR,
    WARSZAWA_AREA_TERYT,
    WARSZAWA_DZIELNICE_TERYT,
    build_rules_for_gmina,
    export_election_areas,
    generate_for_teryt,
    load_excel,
    quality_flag,
    update_manifest,
)
from build_gminy import COUNTRY_ELECTIONS, load_gmina_boundaries, load_national_results
from utils import PROCESSED_DIR, load_config, normalize_teryt


def _worker(args: tuple) -> dict:
    """Uruchamiany w osobnym procesie — importuje wszystko od zera."""
    teryt, excel_path, registry_path, gminy_boundaries_pkl, national_results, suffix = args
    try:
        import geopandas as gpd
        import pickle

        excel = load_excel(registry_path) if registry_path else load_excel(excel_path)
        gminy_boundaries = pickle.loads(gminy_boundaries_pkl)  # noqa: S301
        return generate_for_teryt(teryt, excel, gminy_boundaries, national_results, output_suffix=suffix)
    except Exception:
        return {"teryt": teryt, "quality": "failed", "error": traceback.format_exc()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=max(1, os.cpu_count() - 2))
    parser.add_argument("--limit", type=int, default=None, help="ogranicz do N gmin (do testów)")
    parser.add_argument("--resume", action="store_true", help="pomiń gminy z już gotowym GeoJSON")
    parser.add_argument("--registry", default=None, help="alternatywny rejestr CSV/xlsx zamiast PKW")
    parser.add_argument("--elections", nargs="+", default=None, help="ogranicz do podanych ID wyborów")
    parser.add_argument("--suffix", default=None, help="podkatalog w data/generated/ na surowe poligony")
    parser.add_argument("--no-manifest", action="store_true", help="nie aktualizuj manifest.json na końcu")
    parser.add_argument("--fragment-dir", default=None,
                        help="katalog na fragmenty manifestu (zamiast manifest.json); scal przez merge_manifest_fragments.py")
    args = parser.parse_args()

    excel = load_excel(args.registry) if args.registry else load_excel()
    teryty = sorted(excel["teryt"].dropna().unique().tolist())

    # Warszawa: usuń dzielnice (146502-146519), zastąp jednym rekordem 146501
    teryty = [t for t in teryty if t not in WARSZAWA_DZIELNICE_TERYT]
    if WARSZAWA_AREA_TERYT not in teryty and any(t.startswith("1465") for t in excel["teryt"].unique()):
        teryty.append(WARSZAWA_AREA_TERYT)
    teryty = sorted(set(teryty))

    if args.resume:
        geojson_dir = OUTPUT_DIR / args.suffix if args.suffix else OUTPUT_DIR
        done = {p.stem for p in geojson_dir.glob("*.geojson")} if geojson_dir.exists() else set()
        before = len(teryty)
        teryty = [t for t in teryty if t not in done]
        print(f"--resume: pominięto {before - len(teryty)} gmin z gotowym GeoJSON, zostało {len(teryty)}")

    if args.limit:
        teryty = teryty[: args.limit]

    print(f"Gminy do przetworzenia: {len(teryty)}, workers: {args.workers}")

    config = load_config()
    elections_by_id = {e["id"]: e for e in config["elections"]}
    allowed = set(args.elections) if args.elections else None

    print("Wczytywanie wyników ogólnopolskich...")
    national_results = {
        eid: load_national_results(elections_by_id[eid])
        for eid in COUNTRY_ELECTIONS
        if eid in elections_by_id and (allowed is None or eid in allowed)
    }

    print("Wczytywanie granic gmin...")
    gminy_boundaries = load_gmina_boundaries(simplify=None)

    import pickle
    gminy_pkl = pickle.dumps(gminy_boundaries)

    excel_path = str(Path(__file__).resolve().parent.parent / "data" / "metadata" / "obwody_glosowania_utf8.xlsx")

    worker_args = [
        (teryt, excel_path, args.registry, gminy_pkl, national_results, args.suffix)
        for teryt in teryty
    ]

    quality_report: list[dict] = []
    start = time.time()
    done_count = 0
    failed_count = 0

    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_worker, wa): wa[0] for wa in worker_args}
        for future in as_completed(futures):
            teryt = futures[future]
            result = future.result()
            quality_report.append(result)
            done_count += 1
            q = result.get("quality", "?")
            if q == "failed":
                failed_count += 1
                print(f"[{done_count}/{len(teryty)}] BŁĄD {teryt}: {result.get('error','')[:120]}", file=sys.stderr)
            else:
                elapsed = time.time() - start
                rate = done_count / elapsed
                remaining = (len(teryty) - done_count) / rate if rate > 0 else 0
                print(f"[{done_count}/{len(teryty)}] {teryt} → {q}  ({remaining/60:.0f} min pozostało)")

            # Zapis fragmentu manifestu jeśli podano katalog
            if args.fragment_dir and "areas" in result:
                fdir = Path(args.fragment_dir)
                fdir.mkdir(parents=True, exist_ok=True)
                fpath = fdir / f"{teryt}.json"
                fpath.write_text(json.dumps([result], ensure_ascii=False, indent=2), encoding="utf-8")

    # Raport jakości
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    qname = f"quality_{args.suffix}.json" if args.suffix else "quality.json"
    (OUTPUT_DIR / qname).write_text(
        json.dumps(
            [{k: v for k, v in r.items() if k != "areas"} for r in quality_report],
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )

    total_time = time.time() - start
    ok = sum(1 for r in quality_report if r.get("quality") in ("generated", "approximate", "poor"))
    print(f"\nGotowe: {ok}/{len(teryty)} w {total_time/60:.1f} min, błędów: {failed_count}")
    print(f"Raport: {OUTPUT_DIR / qname}")

    if not args.no_manifest and not args.fragment_dir:
        update_manifest(quality_report)


if __name__ == "__main__":
    main()
