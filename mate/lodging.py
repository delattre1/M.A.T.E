"""Where you sleep.

Airbnb's robots.txt disallows /s/*/* — the search path — so MATE does not
discover listings there, and the override that would bypass it stays off. The
places here are real and named, from OpenStreetMap; the nightly figure is a
curated tier until the user pins a real listing, and the page says which.

airbnb_listing_details reads /rooms/<id>, which robots.txt does NOT disallow,
so a listing the user actually pastes gets its real price.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from functools import lru_cache
from pathlib import Path

from .geo import haversine
from .overpass import fetch_raw
from .schema import CURATED, ESTIMATED, MEASURED, Coord, Lodging, Provenance
from .text import fold
from .settings import Settings

TIER_FILE = Path(__file__).resolve().parent.parent / "data" / "lodging_tiers_rj.json"

KITCHEN_LIKELY = {"chalet", "apartment"}


@lru_cache(maxsize=2)
def _tiers(path: str = "") -> dict:
    return json.loads(Path(path or TIER_FILE).read_text(encoding="utf-8"))


def tier_price(town: str, tier: str, party_size: int, *, table: dict | None = None) -> tuple[float, str]:
    """OSM writes the same town as "petropolis", "Petrópolis" and "PETROPOLIS"."""
    data = table or _tiers()
    by_fold = {fold(k): v for k, v in data["towns"].items()}
    row = by_fold.get(fold(town)) or data["default"]
    base = float(row.get(tier, row["medium"]))
    extra = max(0, party_size - 2) * base * float(data.get("per_extra_guest_pct", 0.0))
    return round(base + extra, 2), data.get("as_of", "")


def find_lodging(centers: list[Coord], towns: list[str], s: Settings, *,
                 radius_km: float = 20.0, tier: str = "medium",
                 party_size: int = 2, nights: int = 1) -> list[Lodging]:
    """Named stays from OSM, priced by the tier of whichever town they sit in."""
    out: list[Lodging] = []
    for el in fetch_raw(centers, radius_km, s, ["lodging"]):
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name:
            continue
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        if lat is None or lon is None:
            continue
        here = Coord(float(lat), float(lon))
        nearest = min(range(len(centers)), key=lambda i: haversine(here, centers[i]))
        if haversine(here, centers[nearest]) > radius_km:
            continue
        town = towns[nearest].split(",")[0].strip()
        stay_type = tags.get("tourism", "")
        nightly, as_of = tier_price(town, tier, party_size)
        out.append(
            Lodging(
                id=f"{el.get('type', 'node')}/{el.get('id')}",
                name=name,
                city=town,
                lat=float(lat),
                lon=float(lon),
                price_per_night_brl=nightly,
                total_price_brl=round(nightly * nights, 2),
                url=tags.get("website") or tags.get("contact:website") or "",
                capacity=int(tags.get("capacity", 0) or 0),
                has_kitchen=stay_type in KITCHEN_LIKELY or tags.get("kitchen") == "yes",
                provenance=Provenance(
                    CURATED,
                    f"{name} vem do OpenStreetMap; diária é faixa típica '{tier}' para {town or 'a região'}",
                    as_of,
                ),
            )
        )
    return out


LISTING_ID = re.compile(r"/rooms/(?:plus/)?(\d+)")


def listing_id_from(text: str) -> str:
    m = LISTING_ID.search(text)
    return m.group(1) if m else (text.strip() if text.strip().isdigit() else "")


def pin_real_listing(client, url_or_id: str, *, checkin: str, checkout: str,
                     party_size: int, nights: int) -> Lodging:
    """Fetch a listing the user actually chose. /rooms/<id> is allowed by robots.txt."""
    listing = listing_id_from(url_or_id)
    if not listing:
        raise ValueError(f"não reconheci um id de anúncio em {url_or_id!r}")

    data = client.call_tool("airbnb_listing_details", {
        "id": listing, "checkin": checkin, "checkout": checkout, "adults": party_size,
    })
    blob = json.dumps(data, ensure_ascii=False) if not isinstance(data, str) else data
    name = _first(data, ("name", "title")) or f"anúncio {listing}"
    nightly = _price_from(blob)
    coord = _coord_from(data)

    return Lodging(
        id=f"airbnb/{listing}",
        name=name,
        city=_first(data, ("city", "localizedCity")) or "",
        lat=coord[0],
        lon=coord[1],
        price_per_night_brl=nightly or 0.0,
        total_price_brl=round((nightly or 0.0) * nights, 2),
        url=f"https://www.airbnb.com.br/rooms/{listing}",
        has_kitchen="cozinha" in blob.lower() or "kitchen" in blob.lower(),
        provenance=Provenance(
            MEASURED if nightly else ESTIMATED,
            "anúncio informado pelo usuário, lido via airbnb_listing_details",
        ),
    )


def booking_search_url(town: str, checkin: str, checkout: str, adults: int) -> str:
    """We plan and link out; the human books."""
    q = urllib.parse.quote(town)
    return (
        f"https://www.airbnb.com.br/s/{q}/homes?checkin={checkin}"
        f"&checkout={checkout}&adults={adults}"
    )


def _first(data, keys: tuple[str, ...]) -> str:
    if isinstance(data, dict):
        for k in keys:
            v = data.get(k)
            if isinstance(v, str) and v:
                return v
        for v in data.values():
            found = _first(v, keys)
            if found:
                return found
    elif isinstance(data, list):
        for v in data:
            found = _first(v, keys)
            if found:
                return found
    return ""


def _price_from(blob: str) -> float:
    m = re.search(r"R\$\s?([\d.]+),?(\d{2})?", blob)
    if not m:
        return 0.0
    whole = m.group(1).replace(".", "")
    return float(f"{whole}.{m.group(2) or '00'}")


def _coord_from(data) -> tuple[float, float]:
    if isinstance(data, dict):
        lat = data.get("lat") or data.get("latitude")
        lon = data.get("lng") or data.get("lon") or data.get("longitude")
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            return float(lat), float(lon)
        for v in data.values():
            got = _coord_from(v)
            if got != (0.0, 0.0):
                return got
    elif isinstance(data, list):
        for v in data:
            got = _coord_from(v)
            if got != (0.0, 0.0):
                return got
    return 0.0, 0.0
