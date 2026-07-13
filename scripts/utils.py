from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "elections.yaml"
RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "frontend" / "public" / "data"


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def ensure_dirs() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)


def download(url: str, dest: Path) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    dest.write_bytes(response.content)
    return dest


def extract_zip(zip_path: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(dest_dir)
    return dest_dir


def find_shapefile(directory: Path, layer_suffix: str) -> Path:
    matches = sorted(directory.rglob(f"*{layer_suffix}*.shp"))
    if not matches:
        matches = sorted(directory.rglob("*.shp"))
    if not matches:
        raise FileNotFoundError(f"Brak pliku .shp w {directory}")
    obwody = [path for path in matches if "Obwod" in path.name or "obwod" in path.name]
    return obwody[0] if obwody else matches[0]


def read_csv_from_zip(zip_path: Path, inner_name: str | None = None) -> pd.DataFrame:
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        if inner_name:
            selected = next((name for name in names if name.endswith(inner_name)), None)
            if not selected:
                raise FileNotFoundError(f"Brak pliku {inner_name} w {zip_path.name}")
        else:
            csv_names = [name for name in names if name.lower().endswith(".csv")]
            selected = csv_names[0]
        content = archive.read(selected)
    return pd.read_csv(io.BytesIO(content), sep=";", encoding="utf-8-sig", dtype=str)


def normalize_teryt(value: Any) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip().replace('"', "")
    if text.endswith(".0"):
        text = text[:-2]
    return text


def normalize_obwod(value: Any) -> int | None:
    if pd.isna(value):
        return None
    text = str(value).strip().replace('"', "")
    if not text:
        return None
    return int(float(text))


def short_party_name(name: str) -> str:
    upper = name.upper()
    mapping = [
        ("PRAWO I SPRAWIEDLIWOŚĆ", "PiS"),
        ("KOALICJA OBYWATELSKA", "KO"),
        ("TRZECIA DROGA", "Trzecia Droga"),
        ("NOWA LEWICA", "Lewica"),
        ("KONFEDERACJA", "Konfederacja"),
        ("POLSKA JEST JEDNA", "Polska jest Jedna"),
        ("BEZPARTYJNI SAMORZĄDOWCY", "Bezpartyjni"),
    ]
    for needle, short in mapping:
        if needle in upper:
            return short
    cleaned = re.sub(r"^KOMITET WYBORCZY\s+", "", upper, flags=re.I)
    cleaned = re.sub(r"^KOALICYJNY KOMITET WYBORCZY\s+", "", cleaned, flags=re.I)
    cleaned = re.sub(r"^KKW\s+", "", cleaned, flags=re.I)
    return cleaned.title()[:40]


def parse_int(value: Any) -> int:
    if pd.isna(value):
        return 0
    text = str(value).strip().replace('"', "").replace(" ", "")
    if not text:
        return 0
    try:
        return int(float(text))
    except ValueError:
        return 0
