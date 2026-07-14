from __future__ import annotations

import io
import re
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

WFS_URL = "https://mapy.geoportal.gov.pl/wss/ext/KrajowaIntegracjaNumeracjiAdresowej"
PAGE_SIZE = 10000


def _filter_xml(teryt: str) -> str:
    return (
        '<Filter xmlns="http://www.opengis.net/fes/2.0">'
        "<PropertyIsEqualTo><ValueReference>teryt</ValueReference>"
        f"<Literal>{teryt}</Literal></PropertyIsEqualTo></Filter>"
    )


def _count_for_teryt(teryt: str) -> int:
    params = {
        "Service": "WFS",
        "Request": "GetFeature",
        "TypeName": "ms:prg-adresy",
        "Version": "2.0.0",
        "FILTER": _filter_xml(teryt),
        "resultType": "hits",
    }
    response = requests.get(WFS_URL, params=params, timeout=60)
    response.raise_for_status()
    match = re.search(r'numberMatched="(\d+)"', response.text)
    return int(match.group(1)) if match else 0


def _fetch_page(teryt: str, start: int, count: int) -> gpd.GeoDataFrame:
    params = {
        "Service": "WFS",
        "Request": "GetFeature",
        "TypeName": "ms:prg-adresy",
        "Version": "2.0.0",
        "FILTER": _filter_xml(teryt),
        "count": count,
        "STARTINDEX": start,
    }
    response = requests.get(WFS_URL, params=params, timeout=120)
    response.raise_for_status()
    return gpd.read_file(io.BytesIO(response.content))


def fetch_addresses_for_teryt(teryt: str, cache_dir: Path) -> gpd.GeoDataFrame:
    """Pobiera punkty adresowe PRG (usługa WFS GUGiK „Krajowa Integracja Numeracji
    Adresowej") dla gminy o danym TERYT (6-cyfrowy, format zgodny z PKW). Wynik ma
    ten sam schemat co fetch_addresses_osm.fetch_addresses_for_place (street, number,
    housenumber, lat, lon), plus miejscowosc dla dopasowania wsi."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{teryt}.parquet"
    if cache_file.exists():
        df = pd.read_parquet(cache_file)
        return gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df.lon, df.lat), crs="EPSG:4326")

    total = _count_for_teryt(teryt)
    if total == 0:
        raise RuntimeError(f"Brak punktów adresowych PRG dla teryt={teryt}")

    parts = []
    start = 0
    while start < total:
        parts.append(_fetch_page(teryt, start, PAGE_SIZE))
        start += PAGE_SIZE

    raw = pd.concat(parts, ignore_index=True)
    raw = gpd.GeoDataFrame(raw, geometry="geometry", crs="EPSG:2180").to_crs(epsg=4326)

    rows = []
    for row in raw.itertuples(index=False):
        numer_raw = row.numer or ""
        digits = "".join(ch for ch in str(numer_raw) if ch.isdigit())
        number = int(digits) if digits else None
        rows.append(
            {
                "street": row.ulica or "",
                "number": number,
                "housenumber": numer_raw,
                "miejscowosc": row.miejscowosc or "",
                "lat": row.geometry.y,
                "lon": row.geometry.x,
            }
        )
    df = pd.DataFrame(rows)
    df.to_parquet(cache_file, index=False)
    return gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df.lon, df.lat), crs="EPSG:4326")
