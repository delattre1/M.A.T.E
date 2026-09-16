"""POIs from OpenStreetMap, via Overpass.

Overpass answers with 200 and a `remark` when a query times out server-side, so
the empty-list case has to be told apart from the failure case here: a plan
built on a silently-failed fetch is the exact thing this project exists to avoid.
"""

from __future__ import annotations

import re
from datetime import date

from .cache import Cache
from .geo import haversine
from .http import SourceError, post_form_json
from .schema import MEASURED, Coord, Provenance, Stop
from .settings import Settings
from .text import fold

# What is actually worth a weekend in the serra, as OSM tags it.
#
# Each entry is a complete selector; the element type is part of it and matters
# for cost. `natural=waterfall` as `nwr` makes Overpass walk every way and
# relation carrying `natural` across mountain terrain — woods, scrub, peaks —
# and times out. As `node` it returns in seconds, and waterfalls are nodes.
CATEGORIES: dict[str, tuple[str, ...]] = {
    "tourism": ('nwr["tourism"~"^(attraction|museum|viewpoint|artwork|gallery|theme_park|zoo)$"]',),
    "waterfall": ('node["natural"="waterfall"]', 'node["waterway"="waterfall"]'),
    # Bare ["historic"] matches a long tail of plaques and milestones and makes
    # the query far more expensive than the handful of results worth visiting.
    "historic": ('nwr["historic"~"^(castle|palace|monument|ruins|church|manor|fort|'
                 'memorial|archaeological_site|city_gate|tower)$"]',),
    "food": ('nwr["amenity"~"^(restaurant|cafe|ice_cream)$"]',),
    "lodging": ('nwr["tourism"~"^(hotel|guest_house|chalet|apartment|hostel|motel)$"]',),
}
FOOD_AMENITIES = frozenset({"restaurant", "cafe", "ice_cream"})
QID = re.compile(r"^Q\d+$")

# Weight by what the feature is. A museum anchors a day; a park bench artwork
# does not. Historic values not listed fall back to HISTORIC_DEFAULT.
TYPE_WEIGHT: dict[str, float] = {
    "museum": 3.0, "theme_park": 2.5, "zoo": 2.5, "attraction": 2.5,
    "waterfall": 3.0, "viewpoint": 2.0, "gallery": 2.0, "artwork": 1.0,
    "nature_reserve": 2.0, "park": 1.5,
    "castle": 3.0, "palace": 3.0, "monument": 2.0, "ruins": 1.5, "church": 1.5,
    "memorial": 0.8, "wayside_cross": 0.3,
    "restaurant": 1.0, "cafe": 0.8, "ice_cream": 0.5,
}
HISTORIC_DEFAULT = 1.5


class OverpassError(SourceError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__("overpass", message, status)


# Where you sleep is not a sight: lodging is fetched on purpose, never by default.
DEFAULT_CATEGORIES = [c for c in CATEGORIES if c != "lodging"]


def build_query(centers: list[Coord], radius_km: float, settings: Settings,
                categories: list[str] | None = None) -> str:
    """All candidate towns in one request.

    Five towns asked separately is five round trips against a shared public
    endpoint that takes ~50 s each. Unioned, it is one — faster for us and
    lighter on Overpass, which is the whole point of the usage policy.
    """
    names = categories or DEFAULT_CATEGORIES
    unknown = [c for c in names if c not in CATEGORIES]
    if unknown:
        raise ValueError(f"unknown POI categories: {unknown}")
    arounds = [
        f"(around:{radius_km * 1000:.0f},{c.lat:.6f},{c.lon:.6f})" for c in centers
    ]
    # The selective tag goes first: ["name"] leading would make Overpass scan
    # every named object in the area before narrowing, which times out.
    clauses = "\n".join(
        f'  {f}["name"]{a};' for a in arounds for c in names for f in CATEGORIES[c]
    )
    return (
        f"[out:json][timeout:{settings.overpass_query_timeout_s}];\n"
        f"(\n{clauses}\n);\nout center;"
    )


def bucket_by_center(stops: list[Stop], centers: list[Coord], radius_km: float
                     ) -> list[list[Stop]]:
    """Each stop belongs to the town it is closest to."""
    out: list[list[Stop]] = [[] for _ in centers]
    for s in stops:
        here = Coord(s.lat, s.lon)
        best = min(range(len(centers)), key=lambda i: haversine(here, centers[i]))
        if haversine(here, centers[best]) <= radius_km:
            out[best].append(s)
    return out


def _category(tags: dict[str, str]) -> str:
    if "tourism" in tags:
        return tags["tourism"]
    if tags.get("natural") == "waterfall" or tags.get("waterway") == "waterfall":
        return "waterfall"
    if "historic" in tags:
        v = tags["historic"]
        return "historic" if v in ("yes", "building") else v
    if tags.get("leisure") in ("park", "nature_reserve"):
        return tags["leisure"]
    return tags.get("amenity", "")


def _url(tags: dict[str, str]) -> str:
    raw = tags.get("website") or tags.get("contact:website") or tags.get("url") or ""
    return f"https://{raw}" if raw and "://" not in raw else raw


def _qid(tags: dict[str, str]) -> str:
    q = (tags.get("wikidata") or "").split(";")[0].strip()
    return q if QID.match(q) else ""


def _parse(el: dict, settings: Settings) -> tuple[Stop, dict[str, str]] | None:
    tags = el.get("tags") or {}
    name = tags.get("name") or tags.get("name:pt") or ""
    center = el.get("center") or el
    if not name or "lat" not in center:
        return None
    # A historic road is tagged historic and has a centre point, but you cannot
    # visit the middle of a highway. Same for rivers and boundaries.
    if tags.keys() & {"highway", "waterway", "boundary", "railway"} and not tags.get("tourism"):
        if tags.get("waterway") != "waterfall":
            return None
    kind = "meal" if tags.get("amenity") in FOOD_AMENITIES else "sight"
    stop = Stop(
        id=f"{el['type'][0]}{el['id']}",
        name=name,
        lat=float(center["lat"]),
        lon=float(center["lon"]),
        kind=kind,
        category=_category(tags),
        description=tags.get("description:pt-BR") or tags.get("description:pt")
        or tags.get("description", ""),
        dwell_min=settings.meal_dwell_min if kind == "meal" else settings.default_dwell_min,
        opening_hours=tags.get("opening_hours", ""),
        url=_url(tags),
        osm_id=f"{el['type']}/{el['id']}",
        wikidata_id=_qid(tags),
        provenance=Provenance(MEASURED, "OpenStreetMap via Overpass", date.today().isoformat()),
    )
    return stop, tags


def score_pois(stops: list[Stop], tags_by_id: dict[str, dict[str, str]] | None = None
               ) -> list[Stop]:
    """A proxy for notability, not a popularity ranking: OSM has no visitor
    counts, so this counts the effort mappers spent on a feature (wikidata,
    wikipedia, heritage listing, a website, opening hours) plus a weight for
    what it is. It sorts a good candidate near the top; it does not know whether
    anyone enjoyed the visit."""
    for s in stops:
        t = (tags_by_id or {}).get(s.id, {})
        base = TYPE_WEIGHT.get(s.category, HISTORIC_DEFAULT if "historic" in t else 1.0)
        score = base
        score += 3.0 if (s.wikidata_id or t.get("wikidata")) else 0.0
        score += 2.5 if any(k.startswith("wikipedia") for k in t) else 0.0
        score += 2.0 if any(k.startswith("heritage") for k in t) else 0.0
        score += 1.0 if s.url else 0.0
        score += 0.75 if s.opening_hours else 0.0
        score += 0.5 if t.get("image") or t.get("wikimedia_commons") else 0.0
        score += 0.5 if s.description else 0.0
        score += 0.25 if t.get("phone") or t.get("contact:phone") or t.get("addr:street") else 0.0
        s.score = round(score, 2)
    return sorted(stops, key=lambda s: (-s.score, s.name))


def _dedupe(stops: list[Stop]) -> list[Stop]:
    """The same museum is often mapped as a node and as a building way, and the
    same waterfall twice by two people who disagreed about the accent."""
    out: list[Stop] = []
    for s in stops:
        name = fold(s.name)
        near = any(
            name == fold(k.name) and haversine(Coord(s.lat, s.lon), Coord(k.lat, k.lon)) < 0.5
            for k in out
        )
        if not near:
            out.append(s)
    return out


def fetch_raw(centers: list[Coord], radius_km: float, settings: Settings,
              categories: list[str] | None = None) -> list[dict]:
    query = build_query(centers, radius_km, settings, categories)
    cache = Cache(settings)
    key = (sorted((round(c.lat, 5), round(c.lon, 5)) for c in centers), radius_km,
           sorted(categories or DEFAULT_CATEGORIES))

    def fetch() -> list[dict]:
        endpoints = [settings.overpass_url]
        if settings.overpass_mirror_url:
            endpoints.append(settings.overpass_mirror_url)
        last: SourceError | None = None
        for url in endpoints:
            try:
                data = post_form_json(url, {"data": query}, settings, rate_limit_key="overpass",
                                      timeout=settings.overpass_query_timeout_s + 30)
            except SourceError as e:
                last = e
                continue
            remark = data.get("remark", "")
            if remark:
                raise OverpassError(remark)
            if "elements" not in data:
                raise OverpassError("response carried no elements key")
            return data["elements"]
        raise OverpassError(f"todos os endpoints falharam: {last}",
                            getattr(last, "status", None))

    return cache.cached("overpass", key, fetch)


def find_pois(centers: list[Coord], radius_km: float, settings: Settings,
              categories: list[str] | None = None) -> list[Stop]:
    parsed = [p for p in (_parse(el, settings) for el in
                          fetch_raw(centers, radius_km, settings, categories)) if p]
    stops = [s for s, _ in parsed]
    tags_by_id = {s.id: t for s, t in parsed}
    return _dedupe(score_pois(stops, tags_by_id))
