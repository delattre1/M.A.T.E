"""Tolls the route actually passes.

OSM tags a way as tolled but never says the price, and no free API publishes
Brazilian tariffs — so this is a curated table matched against route geometry.
Curated means approximate, and the plan labels it as such.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .geo import point_to_path_km
from .schema import Coord, Toll
from .text import fold

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "tolls_rj.json"


@dataclass(frozen=True)
class Plaza:
    id: str
    name: str
    road: str
    operator: str
    coord: Coord
    price_brl: float
    charges: str
    verify_url: str
    serves: tuple[str, ...] = ()


@lru_cache(maxsize=4)
def load_plazas(path: str = "") -> tuple[tuple[Plaza, ...], float, str]:
    raw = json.loads(Path(path or DATA_FILE).read_text(encoding="utf-8"))
    plazas = tuple(
        Plaza(
            id=p["id"],
            name=p["name"],
            road=p.get("road", ""),
            operator=p.get("operator", ""),
            coord=Coord(lat=p["lat"], lon=p["lon"]),
            price_brl=float(p["price_brl"]),
            charges=p.get("charges", "both_ways"),
            verify_url=p.get("verify_url", ""),
            serves=tuple(p.get("serves", [])),
        )
        for p in raw["plazas"]
    )
    return plazas, float(raw.get("match_km", 3.0)), raw.get("as_of", "")


def tolls_on_route(path: list[Coord], *, round_trip: bool = True, match_km: float | None = None,
                   data_file: str = "", destination: str = "",
                   geometry_measured: bool = True) -> tuple[list[Toll], str]:
    """Plazas the route passes.

    With a real OSRM polyline this is a road match. Without one, a straight line
    from Rio misses the BR-040 plaza by 12 km, so fall back to which towns each
    plaza serves — the curated table already knows.
    """
    plazas, default_match, as_of = load_plazas(data_file)
    threshold = match_km if match_km is not None else default_match
    town = fold(destination.split(",")[0])

    found: list[Toll] = []
    for p in plazas:
        hit = (point_to_path_km(p.coord, path) <= threshold) if geometry_measured else \
            any(town and town == fold(s) for s in p.serves)
        if hit:
            passes = 2 if (round_trip and p.charges == "both_ways") else 1
            found.append(
                Toll(
                    name=p.name + (" (ida e volta)" if passes == 2 else ""),
                    price_brl=round(p.price_brl * passes, 2),
                    road=p.road,
                    operator=p.operator,
                    verify_url=p.verify_url,
                )
            )
    return found, as_of


def total_brl(tolls: list[Toll]) -> float:
    return round(sum(t.price_brl for t in tolls), 2)
