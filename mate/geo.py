"""Distance and time between points: measured by OSRM, estimated when it is down.

The fallback exists so a missing container degrades the plan instead of killing
it, but every figure it produces is stamped ESTIMATED and the page says so.
"""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date

from .schema import ESTIMATED, MEASURED, Coord, Provenance
from .settings import Settings

EARTH_KM = 6371.0088
CAR = "car"
FOOT = "foot"
RIDEHAIL = "ridehail"


class RoutingError(RuntimeError):
    pass


def haversine(a: Coord, b: Coord) -> float:
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp, dl = p2 - p1, math.radians(b.lon - a.lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(h))


def spread_km(coords: list[Coord]) -> float:
    """Widest gap between any two points — how big the day actually is."""
    return max(
        (haversine(a, b) for i, a in enumerate(coords) for b in coords[i + 1:]),
        default=0.0,
    )


def centroid(coords: list[Coord]) -> Coord:
    if not coords:
        raise ValueError("centroid of nothing")
    return Coord(
        lat=sum(c.lat for c in coords) / len(coords),
        lon=sum(c.lon for c in coords) / len(coords),
    )


def project(c: Coord, ref: Coord) -> tuple[float, float]:
    """Equirectangular km offsets. Fine for one region, wrong for a continent."""
    return (
        (c.lon - ref.lon) * 111.32 * math.cos(math.radians(ref.lat)),
        (c.lat - ref.lat) * 110.57,
    )


def point_to_path_km(point: Coord, path: list[Coord]) -> float:
    """Closest approach of a point to a polyline, in km."""
    if not path:
        return float("inf")
    if len(path) == 1:
        return haversine(point, path[0])
    ref = point
    px, py = project(point, ref)
    best = float("inf")
    for a, b in zip(path, path[1:]):
        ax, ay = project(a, ref)
        bx, by = project(b, ref)
        dx, dy = bx - ax, by - ay
        span = dx * dx + dy * dy
        if span == 0:
            d = math.hypot(px - ax, py - ay)
        else:
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / span))
            d = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
        best = min(best, d)
    return best


@dataclass
class Matrix:
    distances_km: list[list[float]]
    durations_min: list[list[float]]
    provenance: Provenance


@dataclass
class RouteResult:
    distance_km: float
    duration_min: float
    geometry: list[Coord] = field(default_factory=list)
    provenance: Provenance = field(default_factory=Provenance)


class Router:
    """One OSRM container per profile; the URL's profile segment is ignored by
    OSRM, so the port is what actually selects car vs foot."""

    def __init__(self, settings: Settings):
        self.s = settings
        self._up: dict[str, bool] = {}

    def _base(self, profile: str) -> str:
        return self.s.osrm_foot_url if profile == FOOT else self.s.osrm_car_url

    def _speed(self, profile: str) -> float:
        return self.s.walking_speed_kmh if profile == FOOT else self.s.fallback_car_speed_kmh

    def _get(self, url: str) -> dict:
        req = urllib.request.Request(url, headers={"User-Agent": self.s.user_agent})
        with urllib.request.urlopen(req, timeout=self.s.osrm_timeout_s) as r:
            return json.loads(r.read().decode("utf-8"))

    def available(self, profile: str) -> bool:
        if profile in self._up:
            return self._up[profile]
        try:
            url = f"{self._base(profile)}/route/v1/driving/-43.18,-22.51;-43.19,-22.52?overview=false"
            self._up[profile] = self._get(url).get("code") == "Ok"
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            self._up[profile] = False
        return self._up[profile]

    @staticmethod
    def _coords_param(coords: list[Coord]) -> str:
        return ";".join(f"{c.lon:.6f},{c.lat:.6f}" for c in coords)

    def table(self, coords: list[Coord], profile: str) -> Matrix:
        n = len(coords)
        if self.available(profile):
            try:
                url = (
                    f"{self._base(profile)}/table/v1/driving/{self._coords_param(coords)}"
                    "?annotations=duration,distance"
                )
                data = self._get(url)
                if data.get("code") == "Ok" and data.get("distances"):
                    return Matrix(
                        distances_km=[[(v or 0.0) / 1000.0 for v in row] for row in data["distances"]],
                        durations_min=[[(v or 0.0) / 60.0 for v in row] for row in data["durations"]],
                        provenance=Provenance(MEASURED, f"OSRM {profile} table", date.today().isoformat()),
                    )
            except (urllib.error.URLError, OSError, ValueError, KeyError, TimeoutError):
                self._up[profile] = False

        speed = self._speed(profile)
        factor = 1.0 if profile == FOOT else self.s.road_factor
        dist = [[haversine(a, b) * factor for b in coords] for a in coords]
        return Matrix(
            distances_km=dist,
            durations_min=[[(d / speed) * 60.0 for d in row] for row in dist],
            provenance=Provenance(
                ESTIMATED,
                f"straight-line x{factor:g} at {speed:g} km/h (OSRM {profile} unreachable)",
                date.today().isoformat(),
            ),
        )

    def route(self, coords: list[Coord], profile: str) -> RouteResult:
        if len(coords) < 2:
            raise RoutingError("a route needs two points")
        if self.available(profile):
            try:
                url = (
                    f"{self._base(profile)}/route/v1/driving/{self._coords_param(coords)}"
                    "?overview=full&geometries=geojson"
                )
                data = self._get(url)
                if data.get("code") == "Ok" and data.get("routes"):
                    r = data["routes"][0]
                    line = r.get("geometry", {}).get("coordinates", [])
                    return RouteResult(
                        distance_km=r["distance"] / 1000.0,
                        duration_min=r["duration"] / 60.0,
                        geometry=[Coord(lat=p[1], lon=p[0]) for p in line],
                        provenance=Provenance(MEASURED, f"OSRM {profile} route", date.today().isoformat()),
                    )
            except (urllib.error.URLError, OSError, ValueError, KeyError, TimeoutError):
                self._up[profile] = False

        speed = self._speed(profile)
        factor = 1.0 if profile == FOOT else self.s.road_factor
        km = sum(haversine(a, b) for a, b in zip(coords, coords[1:])) * factor
        return RouteResult(
            distance_km=km,
            duration_min=(km / speed) * 60.0,
            geometry=list(coords),
            provenance=Provenance(
                ESTIMATED,
                f"straight-line x{factor:g} at {speed:g} km/h (OSRM {profile} unreachable)",
                date.today().isoformat(),
            ),
        )
