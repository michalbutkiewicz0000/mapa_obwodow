from __future__ import annotations

import time
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

OVERPASS_URLS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]

BBOX_FALLBACK = {
    "Bolesławiec": (51.22, 15.52, 51.32, 15.62),
    "Kraków": (49.97, 19.79, 50.13, 20.17),
}


def overpass_query(query: str, timeout: int = 180) -> dict:
    last_error = None
    for url in OVERPASS_URLS:
        try:
            response = requests.post(
                url,
                data={"data": query},
                timeout=timeout,
                headers={"User-Agent": "mapa_obwodow_pilot/0.1"},
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"Overpass query failed: {last_error}")


def fetch_addresses_for_bbox(south: float, west: float, north: float, east: float) -> pd.DataFrame:
    query = f"""
    [out:json][timeout:120];
    (
      node["addr:street"]({south},{west},{north},{east});
      way["addr:street"]({south},{west},{north},{east});
    );
    out center;
    """
    payload = overpass_query(query)
    rows = []
    for element in payload.get("elements", []):
        tags = element.get("tags", {})
        street = tags.get("addr:street")
        if not street:
            continue
        if element["type"] == "node":
            lat, lon = element.get("lat"), element.get("lon")
        else:
            center = element.get("center") or {}
            lat, lon = center.get("lat"), center.get("lon")
        if lat is None or lon is None:
            continue
        number_raw = tags.get("addr:housenumber", "")
        number = None
        if number_raw:
            digits = "".join(ch for ch in str(number_raw) if ch.isdigit())
            number = int(digits) if digits else None
        rows.append(
            {
                "street": street,
                "number": number,
                "housenumber": number_raw,
                "lat": lat,
                "lon": lon,
            }
        )
    return pd.DataFrame(rows)


def normalize_place(name: str) -> str:
    return name.lower().replace(" ", "_").replace(".", "")


def fetch_addresses_for_place(place_name: str, cache_dir: Path) -> gpd.GeoDataFrame:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{normalize_place(place_name)}_addresses.parquet"
    if cache_file.exists():
        df = pd.read_parquet(cache_file)
        return gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df.lon, df.lat), crs="EPSG:4326")

    if place_name not in BBOX_FALLBACK:
        raise ValueError(f"Brak bbox dla {place_name}")
    south, west, north, east = BBOX_FALLBACK[place_name]

    df = fetch_addresses_for_bbox(south, west, north, east)
    if df.empty:
        raise RuntimeError(f"Brak adresów OSM dla {place_name}")
    df.to_parquet(cache_file, index=False)
    time.sleep(1)
    return gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df.lon, df.lat), crs="EPSG:4326")
