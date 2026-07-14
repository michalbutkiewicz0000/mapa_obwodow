#!/usr/bin/env python3
"""Pobiera punkty adresowe PRG (GUGiK WFS) dla wszystkich gmin w Polsce.

Zaprojektowane do uruchomienia na noc i bezpiecznego przerwania w dowolnym
momencie: każda gmina jest zapisywana do osobnego pliku cache
(`data/raw/prg/{teryt}.parquet`) dopiero po pełnym pobraniu, więc ponowne
uruchomienie tego skryptu pomija już pobrane gminy i kontynuuje od miejsca
przerwania. Dane PRG się nie zmieniają w czasie, więc nic nie jest nadpisywane.

Użycie:
    python scripts/download_all_prg.py            # wszystkie gminy
    python scripts/download_all_prg.py --limit 5   # test na próbce

Uruchomienie na noc:
    nohup python scripts/download_all_prg.py > /tmp/prg_download.log 2>&1 &
    tail -f /tmp/prg_download.log
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_addresses_prg import fetch_addresses_for_teryt
from utils import ROOT, normalize_teryt

EXCEL_PATH = ROOT / "data" / "metadata" / "obwody_glosowania_utf8.xlsx"
CACHE_DIR = ROOT / "data" / "raw" / "prg"
FAILED_PATH = CACHE_DIR / "_failed.json"

RETRY_DELAYS = [5, 30, 120]  # sekundy między próbami

# PRG koduje całą Warszawę pod jednym TERYT miasta, nie per dzielnica —
# patrz STAN_PROJEKTU.md / plan Etapu 7. Bez tego remapu 18 dzielnic dałoby
# 18 pustych zapytań (0 punktów) zamiast jednego pełnego.
WARSZAWA_DZIELNICE_TO_MIASTO = {f"1465{n:02d}": "146501" for n in range(2, 20)}


def load_all_terytow() -> list[str]:
    df = pd.read_excel(EXCEL_PATH)
    teryty = df["TERYT gminy"].map(normalize_teryt)
    teryty = teryty[teryty != ""].str.zfill(6)  # puste = obwody odrębne (statki, zagranica) bez TERYT
    teryty = teryty.map(lambda t: WARSZAWA_DZIELNICE_TO_MIASTO.get(t, t))
    return sorted(set(teryty))


def format_eta(seconds: float) -> str:
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m"


def fetch_with_retry(teryt: str) -> int:
    last_error = None
    for attempt, delay in enumerate([0, *RETRY_DELAYS]):
        if delay:
            time.sleep(delay)
        try:
            gdf = fetch_addresses_for_teryt(teryt, CACHE_DIR)
            return len(gdf)
        except Exception as exc:  # noqa: BLE001 - chcemy złapać wszystko i spróbować ponownie
            last_error = exc
            print(f"    próba {attempt + 1} nieudana: {exc}", flush=True)
    raise RuntimeError(f"Wyczerpano próby dla {teryt}: {last_error}")


def load_failed() -> dict:
    if FAILED_PATH.exists():
        return json.loads(FAILED_PATH.read_text(encoding="utf-8"))
    return {}


def save_failed(failed: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    FAILED_PATH.write_text(json.dumps(failed, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="ogranicz liczbę gmin (do testów)")
    args = parser.parse_args()

    teryty = load_all_terytow()
    if args.limit:
        teryty = teryty[: args.limit]

    total = len(teryty)
    print(f"Gmin do sprawdzenia: {total} (już w cache zostaną pominięte)")

    failed = load_failed()
    downloaded = 0
    skipped = 0
    newly_failed = []
    start_time = time.time()

    try:
        for i, teryt in enumerate(teryty, start=1):
            cache_file = CACHE_DIR / f"{teryt}.parquet"
            if cache_file.exists():
                skipped += 1
                continue

            elapsed = time.time() - start_time
            done_so_far = downloaded + skipped
            eta = ""
            if downloaded > 0:
                rate = elapsed / downloaded
                remaining = (total - i + 1) * rate
                eta = f" (ETA ~{format_eta(remaining)})"

            print(f"[{i}/{total}] {teryt}...{eta}", end=" ", flush=True)
            try:
                count = fetch_with_retry(teryt)
                downloaded += 1
                print(f"{count} adresów", flush=True)
                failed.pop(teryt, None)
            except Exception as exc:  # noqa: BLE001
                print(f"POMINIĘTO (błąd): {exc}", flush=True)
                newly_failed.append(teryt)
                failed[teryt] = str(exc)

            time.sleep(0.5)  # kultura wobec serwera GUGiK

    except KeyboardInterrupt:
        print("\n\nPrzerwano (Ctrl-C). Postęp jest zapisany — uruchom skrypt ponownie, żeby kontynuować.")
    finally:
        save_failed(failed)
        print(
            f"\nPodsumowanie: pobrano {downloaded}, pominięto (już w cache) {skipped}, "
            f"nieudane {len(newly_failed)}"
        )
        if failed:
            print(f"Nieudane gminy (łącznie, patrz {FAILED_PATH.name}): {len(failed)}")


if __name__ == "__main__":
    main()
