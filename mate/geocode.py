"""Place name -> coordinate, Photon first and Nominatim as the fallback.

A geocoder that answers confidently with the wrong city is worse than one that
fails, because every distance, cost and driving time downstream inherits the
error silently. So a candidate must survive three checks (country/bbox, state,
name tokens) before it is allowed to become a Coord.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import date

from .cache import FOREVER, Cache
from .http import SourceError, get_json
from .schema import MEASURED, Coord, Provenance
from .settings import Settings

BR_STATES: dict[str, str] = {
    "ac": "acre", "al": "alagoas", "ap": "amapa", "am": "amazonas", "ba": "bahia",
    "ce": "ceara", "df": "distrito federal", "es": "espirito santo", "go": "goias",
    "ma": "maranhao", "mt": "mato grosso", "ms": "mato grosso do sul",
    "mg": "minas gerais", "pa": "para", "pb": "paraiba", "pr": "parana",
    "pe": "pernambuco", "pi": "piaui", "rj": "rio de janeiro",
    "rn": "rio grande do norte", "rs": "rio grande do sul", "ro": "rondonia",
    "rr": "roraima", "sc": "santa catarina", "sp": "sao paulo", "se": "sergipe",
    "to": "tocantins",
}
# south, west, north, east — continental Brazil plus its islands' mainland side.
BR_BBOX = (-33.9, -74.1, 5.4, -28.6)
STOPWORDS = frozenset({"de", "da", "do", "das", "dos", "e", "br", "brasil", "brazil"})
PLACE_KEYS = frozenset({"place", "boundary", "landuse", "natural"})


class GeocodeError(SourceError):
    def __init__(self, message: str) -> None:
        super().__init__("geocode", message)


@dataclass
class GeoPlace:
    name: str = ""
    city: str = ""
    state: str = ""
    country: str = ""
    coord: Coord = field(default_factory=lambda: Coord(0.0, 0.0))
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)  # south, west, north, east
    kind: str = ""               # OSM key: place, boundary, amenity, building...
    provider: str = ""
    provenance: Provenance = field(default_factory=Provenance)

    @property
    def is_place(self) -> bool:
        return self.kind in PLACE_KEYS


def _fold(s: str) -> str:
    """Petrópolis and Petropolis must be one string everywhere: cache key,
    country check and token match alike."""
    decomposed = unicodedata.normalize("NFKD", s or "")
    plain = "".join(c for c in decomposed if not unicodedata.combining(c)).lower()
    return " ".join("".join(c if c.isalnum() else " " for c in plain).split())


def _tokens(s: str) -> list[str]:
    return _fold(s).split()


def _state_hint(tokens: list[str]) -> str:
    joined = " ".join(tokens)
    for code, name in BR_STATES.items():
        if code in tokens or name in joined:
            return name
    return ""


def _looks_brazilian(tokens: list[str]) -> bool:
    return bool({"brasil", "brazil"} & set(tokens)) or bool(_state_hint(tokens))


def _significant(tokens: list[str], state: str) -> list[str]:
    noise = set(state.split()) | set(BR_STATES)
    core = [t for t in tokens if len(t) >= 3 and t not in STOPWORDS and t not in noise]
    return core or [t for t in tokens if len(t) >= 3 and t not in STOPWORDS]


def _in_brazil(c: Coord) -> bool:
    s, w, n, e = BR_BBOX
    return s <= c.lat <= n and w <= c.lon <= e


def _accept(cand: GeoPlace, core: list[str], state: str, country: str) -> bool:
    """Whole tokens only, and the leading one must be in the feature's own name.
    Loose matching is how "serra fluminense" lands on Vila Serrana Fluminense in
    São Paulo; only an administrative feature may satisfy a token from its state."""
    cc = _fold(cand.country)
    if country and cc and cc != country and cc != BR_STATES.get(country, country):
        return False
    if country == "br" and not _in_brazil(cand.coord):
        return False
    if state and cand.state and _fold(cand.state) != state:
        return False
    name_tokens = set(_tokens(cand.name))
    if core and core[0] not in name_tokens:
        return False
    scope = [cand.name, cand.city] + ([cand.state, cand.country] if cand.is_place else [])
    return set(core) <= set(_tokens(" ".join(scope)))


def _photon(place: str, settings: Settings) -> list[GeoPlace]:
    data = get_json(
        settings.photon_url, {"q": place, "limit": 8}, settings, rate_limit_key="photon"
    )
    out: list[GeoPlace] = []
    for f in data.get("features", []):
        coords = (f.get("geometry") or {}).get("coordinates")
        if not coords:
            continue
        p = f.get("properties", {})
        lon, lat = float(coords[0]), float(coords[1])
        ext = p.get("extent") or [lon, lat, lon, lat]  # minLon, maxLat, maxLon, minLat
        out.append(GeoPlace(
            name=p.get("name", ""),
            city=p.get("city") or p.get("county") or p.get("name", ""),
            state=p.get("state", ""),
            country=p.get("countrycode", "") or p.get("country", ""),
            coord=Coord(lat, lon),
            bbox=(float(ext[3]), float(ext[0]), float(ext[1]), float(ext[2])),
            kind=p.get("osm_key", ""),
            provider="photon",
        ))
    return _places_first(out)


def _nominatim(place: str, settings: Settings, country: str) -> list[GeoPlace]:
    params: dict[str, object] = {"q": place, "format": "jsonv2", "limit": 8, "addressdetails": 1}
    if country:
        params["countrycodes"] = country
    data = get_json(settings.nominatim_url, params, settings, rate_limit_key="nominatim")
    out: list[GeoPlace] = []
    for r in data:
        addr = r.get("address", {})
        lat, lon = float(r["lat"]), float(r["lon"])
        bb = [float(v) for v in r.get("boundingbox") or [lat, lat, lon, lon]]
        out.append(GeoPlace(
            name=r.get("name") or r.get("display_name", "").split(",")[0],
            city=addr.get("city") or addr.get("town") or addr.get("village")
            or addr.get("municipality") or "",
            state=addr.get("state", ""),
            country=addr.get("country_code", ""),
            coord=Coord(lat, lon),
            bbox=(bb[0], bb[2], bb[1], bb[3]),
            kind=r.get("category") or r.get("class", ""),
            provider="nominatim",
        ))
    return _places_first(out)


def _places_first(rows: list[GeoPlace]) -> list[GeoPlace]:
    return sorted(rows, key=lambda p: not p.is_place)


def geocode_detailed(
    place: str, settings: Settings, *, require_country: str | None = None
) -> GeoPlace:
    """require_country=None auto-enforces 'br' when the query names a Brazilian
    state or the country; pass '' to disable it."""
    tokens = _tokens(place)
    if not tokens:
        raise GeocodeError("empty place name")
    state = _state_hint(tokens)
    country = ("br" if _looks_brazilian(tokens) else "") if require_country is None \
        else require_country
    core = _significant(tokens, state)
    cache = Cache(settings)

    providers = (
        ("photon", lambda: _photon(place, settings)),
        ("nominatim", lambda: _nominatim(place, settings, country)),
    )

    def fetch() -> dict:
        notes = []
        for name, call in providers:
            try:
                cands = call()
            except SourceError as e:
                notes.append(str(e))
                continue
            for c in cands:
                if _accept(c, core, state, country):
                    return {
                        "name": c.name, "city": c.city, "state": c.state,
                        "country": c.country, "lat": c.coord.lat, "lon": c.coord.lon,
                        "bbox": list(c.bbox), "kind": c.kind, "provider": c.provider,
                    }
            notes.append(f"{name}: {len(cands)} hits, none matching {'+'.join(core)}")
        raise GeocodeError(f"could not place {place!r} ({'; '.join(notes)})")

    d = cache.cached("geocode", (_fold(place), country, state), fetch, ttl_hours=FOREVER)
    return GeoPlace(
        name=d["name"], city=d["city"], state=d["state"], country=d["country"],
        coord=Coord(d["lat"], d["lon"]), bbox=tuple(d["bbox"]),
        kind=d.get("kind", ""), provider=d["provider"],
        provenance=Provenance(
            MEASURED, f"{d['provider']} (OpenStreetMap)", date.today().isoformat()
        ),
    )


def geocode(place: str, settings: Settings, *, require_country: str | None = None) -> Coord:
    return geocode_detailed(place, settings, require_country=require_country).coord
