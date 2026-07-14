import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "frontend" / "public" / "data"
MANIFEST_PATH = DATA_DIR / "manifest.json"


@pytest.fixture(scope="module")
def manifest():
    if not MANIFEST_PATH.exists():
        pytest.skip("manifest.json nie istnieje — uruchom scripts/build_dataset.py")
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_manifest_has_elections(manifest):
    assert len(manifest["elections"]) > 0


def test_country_files_exist_on_disk(manifest):
    for election in manifest["elections"]:
        if election["country"]:
            path = DATA_DIR / election["country"]["file"]
            assert path.exists(), f"Brak pliku {path} dla wyborów {election['id']}"


def test_area_files_exist_on_disk(manifest):
    for election in manifest["elections"]:
        for area in election["areas"]:
            path = DATA_DIR / area["file"]
            assert path.exists(), f"Brak pliku {path} dla obszaru {area['name']} ({election['id']})"


def test_area_teryt_is_six_digits(manifest):
    for election in manifest["elections"]:
        for area in election["areas"]:
            assert len(area["teryt"]) == 6, f"TERYT {area['teryt']} nie ma 6 cyfr ({area['name']})"


def test_results_json_exists_per_election(manifest):
    for election in manifest["elections"]:
        path = DATA_DIR / f"results_{election['id']}.json"
        assert path.exists(), f"Brak {path} — uruchom scripts/build_tiles.py"


def test_tiles_exist_per_election(manifest):
    # Geometria obwodów jest per wybory (Etap 8) — nie ma już jednego wspólnego
    # obwody.pmtiles, tylko obwody_{election_id}.pmtiles, zgodnie z manifest["tiles"].
    for election in manifest["elections"]:
        tiles = election.get("tiles")
        if tiles is None:
            continue
        path = DATA_DIR / tiles
        assert path.exists(), f"Brak {path} dla wyborów {election['id']} — uruchom scripts/build_tiles.py"


def test_no_stale_shared_pmtiles():
    # Pozostałość z Etapu 5 — jeśli istnieje, build_tiles.py nie został uruchomiony
    # po migracji na kafelki per wybory (Etap 8).
    assert not (DATA_DIR / "obwody.pmtiles").exists()


def test_every_area_has_some_matched_results(manifest):
    # Łapie klasę buga z Etapu 7: obszar klikalny na mapie, ale z 0 dopasowanych
    # wyników we wszystkich obwodach (np. przez niezgodność formatu TERYT).
    for election in manifest["elections"]:
        for area in election["areas"]:
            assert area["matched"] > 0, (
                f"{area['name']} ({election['id']}): 0/{area['total']} dopasowanych wyników"
            )
