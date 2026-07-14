#!/usr/bin/env python3
"""Scala fragmenty manifestu z równoległych przebiegów generate_boundaries.py
(uruchomionych z --manifest-fragment) w jeden bezpieczny zapis do manifest.json.

Kontekst: przy generowaniu granic dla całej Polski (~2477 gmin) opłaca się
uruchamiać wiele procesów generate_boundaries.py równolegle (podzielony na
kawałki --teryt), każdy na osobnym rdzeniu/maszynie. Gdyby każdy z nich sam
wołał update_manifest() na współdzielonym manifest.json, równoczesny
read-modify-write mógłby po cichu zgubić wpisy innych procesów (klasyczny
race condition na pliku). Zamiast tego każdy proces zapisuje własny fragment
(pełny quality_report z polem "areas") do osobnego pliku, a ten skrypt czyta
wszystkie fragmenty i robi upsert do manifest.json jednym, sekwencyjnym
przebiegiem — bez współbieżności, więc bez ryzyka wyścigu.

Użycie:
    python scripts/merge_manifest_fragments.py data/generated/fragments/*.json
    python scripts/merge_manifest_fragments.py data/generated/fragments/
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from generate_boundaries import update_manifest


def collect_fragment_paths(args: list[str]) -> list[Path]:
    paths: list[Path] = []
    for arg in args:
        path = Path(arg)
        if path.is_dir():
            paths.extend(sorted(path.glob("*.json")))
        elif path.is_file():
            paths.append(path)
        else:
            print(f"Pominięto (nie plik ani katalog): {arg}", file=sys.stderr)
    return paths


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(
            "Podaj ścieżki do fragmentów (lub katalog z nimi), np.:\n"
            "  python scripts/merge_manifest_fragments.py data/generated/fragments/*.json"
        )

    fragment_paths = collect_fragment_paths(sys.argv[1:])
    if not fragment_paths:
        raise SystemExit("Nie znaleziono żadnych plików fragmentów.")

    combined: list[dict] = []
    for path in fragment_paths:
        report = json.loads(path.read_text(encoding="utf-8"))
        combined.extend(report)
        print(f"  {path}: {len(report)} gmin")

    total_areas = sum(len(r.get("areas", {})) for r in combined)
    print(f"\nScalanie {len(combined)} wpisów gmin ({total_areas} wpisów areas łącznie) z {len(fragment_paths)} fragmentów...")
    update_manifest(combined)


if __name__ == "__main__":
    main()
