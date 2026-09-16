"""Tunables that turn geometry into money and time.

No rate is hard-coded anywhere else. If a number reaches the user it came from
here or from a live source, and the plan says which.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields, replace


@dataclass(frozen=True)
class Settings:
    fuel_price_brl_per_liter: float = 6.20
    consumption_km_per_liter: float = 11.0

    walking_speed_kmh: float = 4.8
    max_walking_km_per_day: float = 8.0

    ridehail_base_brl: float = 6.50
    ridehail_per_km_brl: float = 2.40
    ridehail_minimum_brl: float = 9.00

    # A day whose stops all sit within this of each other is walkable.
    foot_cluster_max_km: float = 2.0

    min_stops_per_day: int = 3
    max_stops_per_day: int = 6
    default_dwell_min: int = 60
    meal_dwell_min: int = 75

    day_start: str = "09:00"
    day_end: str = "21:00"
    lunch_window_start: str = "11:30"
    lunch_window_end: str = "14:30"
    dinner_window_start: str = "18:30"
    dinner_window_end: str = "21:30"

    food_brl_per_person_per_day: float = 90.0

    # Straight-line to road distance, used only when OSRM is unreachable.
    road_factor: float = 1.30
    fallback_car_speed_kmh: float = 55.0

    osrm_car_url: str = "http://localhost:5000"
    osrm_foot_url: str = "http://localhost:5001"
    # Short on purpose: a down container must not stall a whole plan.
    osrm_timeout_s: float = 8.0
    overpass_url: str = "https://overpass-api.de/api/interpreter"
    # The main endpoint is frequently saturated; a 504 there is not a dead end.
    overpass_mirror_url: str = "https://overpass.kumi.systems/api/interpreter"
    # One unioned query over several towns legitimately runs long.
    overpass_query_timeout_s: int = 180
    photon_url: str = "https://photon.komoot.io/api"
    nominatim_url: str = "https://nominatim.openstreetmap.org/search"
    wikidata_url: str = "https://www.wikidata.org/w/api.php"

    user_agent: str = "MATE-trip-planner/0.1 (open-source trip planner; one trip at a time)"
    http_timeout_s: float = 60.0
    cache_ttl_hours: float = 168.0
    cache_dir: str = ".cache"

    poi_search_radius_km: float = 25.0
    town_radius_km: float = 10.0
    lodging_candidates: int = 12
    lodgings_per_town: int = 3

    @classmethod
    def from_env(cls, **overrides) -> "Settings":
        """Read MATE_<FIELD> for every field, then apply explicit overrides."""
        values = {}
        for f in fields(cls):
            raw = os.environ.get("MATE_" + f.name.upper())
            if raw is None:
                continue
            try:
                values[f.name] = int(raw) if f.type == "int" else (
                    float(raw) if f.type == "float" else raw
                )
            except ValueError:
                continue
        return replace(cls(**values), **overrides) if overrides else cls(**values)


DEFAULTS = Settings()
