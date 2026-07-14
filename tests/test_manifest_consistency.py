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


def test_obwody_pmtiles_exists():
    if not MANIFEST_PATH.exists():
        pytest.skip("manifest.json nie istnieje")
    assert (DATA_DIR / "obwody.pmtiles").exists()
