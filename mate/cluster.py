"""Which stops belong to which day.

Grouped by proximity to each other rather than to the base — that is the whole
trick that stops a day from zigzagging back and forth across a valley.
"""

from __future__ import annotations

import math

from .geo import CAR, FOOT, RIDEHAIL, centroid, haversine, project, spread_km
from .schema import Coord, Stop
from .settings import Settings
from .text import fold


def _coord(s: Stop) -> Coord:
    return Coord(lat=s.lat, lon=s.lon)


def _kmeans(points: list[tuple[float, float]], k: int, iters: int = 50) -> list[int]:
    """Farthest-first seeding, so the same input always gives the same days."""
    if k <= 1 or len(points) <= k:
        return list(range(len(points))) if len(points) <= k else [0] * len(points)

    centers = [points[0]]
    while len(centers) < k:
        far, best = None, -1.0
        for p in points:
            d = min((p[0] - c[0]) ** 2 + (p[1] - c[1]) ** 2 for c in centers)
            if d > best:
                far, best = p, d
        centers.append(far)

    labels = [0] * len(points)
    for _ in range(iters):
        moved = False
        for i, p in enumerate(points):
            best_c = min(
                range(k), key=lambda c: (p[0] - centers[c][0]) ** 2 + (p[1] - centers[c][1]) ** 2
            )
            if labels[i] != best_c:
                labels[i], moved = best_c, True
        for c in range(k):
            members = [p for i, p in enumerate(points) if labels[i] == c]
            if members:
                centers[c] = (
                    sum(p[0] for p in members) / len(members),
                    sum(p[1] for p in members) / len(members),
                )
        if not moved:
            break
    return labels


def build_day_buckets(pois: list[Stop], base: Coord, n_days: int, s: Settings,
                      *, max_stops: int | None = None) -> list[list[Stop]]:
    """Pick the stops worth the trip and split them into geographic days."""
    per_day = max_stops or s.max_stops_per_day
    sights = [p for p in pois if p.kind != "meal" and haversine(_coord(p), base) <= s.poi_search_radius_km]
    meals = [p for p in pois if p.kind == "meal" and haversine(_coord(p), base) <= s.poi_search_radius_km]

    sights.sort(key=lambda p: p.score, reverse=True)
    # Oversample before clustering: the top-N by score alone would ignore shape.
    pool = sights[: max(n_days * per_day * 2, n_days * per_day)]
    if not pool:
        return [[] for _ in range(n_days)]

    labels = _kmeans([project(_coord(p), base) for p in pool], n_days)
    clusters: list[list[Stop]] = [[] for _ in range(max(n_days, (max(labels) + 1) if labels else 1))]
    for stop, label in zip(pool, labels):
        clusters[label].append(stop)
    clusters = [c for c in clusters if c] or [pool]

    sight_slots = max(1, per_day - 1)
    days: list[list[Stop]] = []
    used: set[str] = set()
    for c in clusters:
        c.sort(key=lambda p: p.score, reverse=True)
        chosen = c[:sight_slots]
        used.update(p.id for p in chosen)
        days.append(chosen)

    while len(days) < n_days:
        days.append([])

    # A day too thin to be worth leaving the house borrows from the fullest one.
    leftovers = [p for p in pool if p.id not in used]
    for day in days:
        while len(day) < min(s.min_stops_per_day, sight_slots) and leftovers:
            anchor = centroid([_coord(p) for p in day]) if day else base
            nearest = min(leftovers, key=lambda p: haversine(_coord(p), anchor))
            leftovers.remove(nearest)
            day.append(nearest)

    eaten: set[str] = set()
    for day in days:
        if not day or not meals:
            continue
        anchor = centroid([_coord(p) for p in day])
        free = [m for m in meals if fold(m.name) not in eaten] or meals
        meal = min(free, key=lambda m: (haversine(_coord(m), anchor) - m.score * 0.1))
        if meal not in day:
            eaten.add(fold(meal.name))
            day.append(meal)

    # A national park mapped at two entrances 4 km apart is two OSM features and
    # one place to visit — and nobody wants it on the itinerary twice, on the
    # same day or on two. Overpass dedupe works on proximity, which cannot
    # catch that, so names are made unique across the whole trip here.
    days = _unique_across_trip(days)

    # Arrive, then go near. The short first day gets the cluster closest to base.
    days.sort(key=lambda d: haversine(centroid([_coord(p) for p in d]), base) if d else 0.0)
    return days[:n_days]


def _unique_across_trip(days: list[list[Stop]]) -> list[list[Stop]]:
    seen: set[str] = set()
    out: list[list[Stop]] = []
    for day in days:
        kept = []
        for s in day:
            key = fold(s.name)
            if key in seen:
                continue
            seen.add(key)
            kept.append(s)
        out.append(kept)
    return out


def infer_mode(stops: list[Stop], base: Coord, *, has_car: bool, s: Settings) -> tuple[str, float, str]:
    """Mode is an output of the geometry, never a question asked of the user."""
    coords = [base] + [_coord(p) for p in stops]
    spread = spread_km(coords)
    if spread <= s.foot_cluster_max_km:
        return FOOT, spread, f"paradas a até {spread:.1f} km umas das outras — dá para fazer a pé"
    if has_car:
        return CAR, spread, f"paradas espalhadas por {spread:.1f} km — precisa de carro"
    return RIDEHAIL, spread, f"paradas espalhadas por {spread:.1f} km e sem carro — corridas de app"
