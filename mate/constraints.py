"""Corrections, kept so they survive the next re-solve.

The model turns "now it's two of us, not four" into one of these. Code never
parses the sentence; the model never does the arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from .schema import Constraint, Intake, PlanOption, TripState
from .settings import Settings
from .text import fold

KINDS = (
    "party_size", "budget", "max_drive_min", "exclude", "include",
    "pace", "has_car", "tier", "dates",
)

PACE = {"relaxed": 4, "tranquilo": 4, "normal": 5, "packed": 6, "puxado": 6}


@dataclass
class Filters:
    exclude: list[str] = field(default_factory=list)
    include: list[str] = field(default_factory=list)
    max_drive_min: float | None = None
    has_car: bool = True
    tier: str = "medium"
    max_stops_per_day: int | None = None


def apply(state: TripState, s: Settings) -> tuple[Intake, Settings, Filters]:
    intake = replace(state.intake)
    filters = Filters()
    settings = s

    for c in state.constraints:
        value = (c.value or "").strip()
        if c.kind == "party_size" and value.isdigit():
            intake.party_size = int(value)
        elif c.kind == "budget":
            intake.budget_total_brl = _money(value) or intake.budget_total_brl
        elif c.kind == "max_drive_min":
            filters.max_drive_min = _money(value)
        elif c.kind == "exclude" and value:
            filters.exclude.append(value)
        elif c.kind == "include" and value:
            filters.include.append(value)
        elif c.kind == "pace":
            filters.max_stops_per_day = PACE.get(value.casefold())
        elif c.kind == "has_car":
            filters.has_car = value.casefold() not in ("false", "no", "não", "nao", "0")
        elif c.kind == "tier" and value:
            filters.tier = value.casefold()
        elif c.kind == "dates" and "," in value:
            start, end = (v.strip() for v in value.split(",", 1))
            intake.start_date, intake.end_date = start, end

    if filters.max_stops_per_day:
        settings = replace(s, max_stops_per_day=filters.max_stops_per_day)
    return intake, settings, filters


# What people say, and the vocabulary a stop is actually tagged with. "Tira as
# cachoeiras" has to reach one tagged `waterfall` and named "Cascata Fischer",
# or the filter half-works — which is worse than not working, because the
# person believes they are gone.
SYNONYMS: tuple[tuple[str, ...], ...] = (
    ("cachoeira", "cascata", "waterfall", "queda", "poco", "salto"),
    ("museu", "museum", "memorial"),
    ("igreja", "church", "capela", "catedral", "santuario"),
    ("parque", "park", "nature_reserve", "jardim"),
    ("castelo", "castle", "palacio", "palace"),
    ("mirante", "viewpoint", "vista"),
    ("trilha", "trail", "caminhada", "trekking"),
    ("restaurante", "restaurant", "comida"),
    ("bar", "pub", "cervejaria", "brewery"),
)


def expand(term: str) -> tuple[str, ...]:
    folded = fold(term)
    for group in SYNONYMS:
        if folded in group:
            return group
    return (folded,)


def filter_pois(pois: list, filters: Filters) -> list:
    if not filters.exclude:
        return pois
    terms = {t for term in filters.exclude for t in expand(term)}
    kept = []
    for p in pois:
        hay = fold(f"{p.name} {p.category} {p.description}")
        if any(t in hay for t in terms):
            continue
        kept.append(p)
    return kept


def drop_over_driving(options: list[PlanOption], filters: Filters) -> list[PlanOption]:
    """"No more than an hour of driving on day one" is a real thing people say."""
    if filters.max_drive_min is None:
        return options
    limit = filters.max_drive_min
    return [
        o for o in options
        if all(d.travel_duration_min <= limit for d in o.days if d.travel_within != "foot")
    ] or options


def add(state: TripState, kind: str, value: str, text: str) -> Constraint:
    c = Constraint(id=f"c{len(state.constraints) + 1}", text=text, kind=kind, value=value)
    state.constraints.append(c)
    return c


def _money(value: str) -> float | None:
    cleaned = value.replace("R$", "").replace(".", "").replace(",", ".").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None
