"""The trip state: the single object the solver writes and the page renders.

This is the contract between the Python that computes and the HTML that shows.
It round-trips through the <script type="application/json" id="plan"> block.

Every computed figure carries `source`, so the page can mark an estimate as an
estimate instead of presenting it with the same confidence as a measured value.
"""

from __future__ import annotations

import types
import typing
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime, timezone

SCHEMA_VERSION = 1

# Where a number came from. Anything not MEASURED renders with a "≈" and a note.
MEASURED = "measured"      # OSRM, Overpass, Airbnb: a real source answered
ESTIMATED = "estimated"    # our formula stood in for a source we could not reach
CURATED = "curated"        # a human-maintained table in data/, with an as_of date


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Coord:
    lat: float
    lon: float


@dataclass
class Provenance:
    """Why a number is what it is, in words the page can print."""
    source: str = ESTIMATED
    detail: str = ""
    as_of: str = ""


@dataclass
class Intake:
    """The four answers. Everything else is a guess the user corrects."""
    origin: str = ""
    region: str = ""
    start_date: str = ""
    end_date: str = ""
    party_size: int = 2
    budget_total_brl: float = 0.0
    origin_coord: Coord | None = None
    # Candidate bases. More than one is what lets the ranking discover that the
    # nearer town wins on total cost despite a higher nightly rate.
    towns: list[str] = field(default_factory=list)

    @property
    def nights(self) -> int:
        if not self.start_date or not self.end_date:
            return 1
        a = datetime.fromisoformat(self.start_date)
        b = datetime.fromisoformat(self.end_date)
        return max(1, (b - a).days)


@dataclass
class Stop:
    id: str = ""
    name: str = ""
    lat: float = 0.0
    lon: float = 0.0
    kind: str = "sight"          # sight | meal | base
    category: str = ""
    description: str = ""
    dwell_min: int = 60
    arrive: str = ""
    depart: str = ""
    opening_hours: str = ""
    url: str = ""
    osm_id: str = ""
    wikidata_id: str = ""
    score: float = 0.0
    provenance: Provenance = field(default_factory=Provenance)


@dataclass
class TravelLeg:
    from_id: str = ""
    to_id: str = ""
    mode: str = "car"            # car | foot | ridehail
    distance_km: float = 0.0
    duration_min: float = 0.0
    cost_brl: float = 0.0
    provenance: Provenance = field(default_factory=Provenance)


@dataclass
class Toll:
    name: str = ""
    price_brl: float = 0.0
    road: str = ""
    operator: str = ""
    verify_url: str = ""


@dataclass
class TravelTo:
    """One decision per trip, and it dominates cost."""
    mode: str = "car"
    distance_km: float = 0.0
    duration_min: float = 0.0
    fuel_cost_brl: float = 0.0
    toll_cost_brl: float = 0.0
    tolls: list[Toll] = field(default_factory=list)
    total_cost_brl: float = 0.0
    round_trip: bool = True
    liters: float = 0.0
    provenance: Provenance = field(default_factory=Provenance)


@dataclass
class DayPlan:
    """One decision per day, and it dominates the shape of the itinerary."""
    date: str = ""
    travel_within: str = "foot"
    mode_reason: str = ""
    spread_km: float = 0.0
    stops: list[Stop] = field(default_factory=list)
    legs: list[TravelLeg] = field(default_factory=list)
    distance_km: float = 0.0
    travel_duration_min: float = 0.0
    walking_km: float = 0.0
    travel_cost_brl: float = 0.0
    notes: list[str] = field(default_factory=list)


@dataclass
class Lodging:
    id: str = ""
    name: str = ""
    city: str = ""
    lat: float = 0.0
    lon: float = 0.0
    price_per_night_brl: float = 0.0
    total_price_brl: float = 0.0
    url: str = ""
    rating: float = 0.0
    capacity: int = 0
    has_kitchen: bool = False
    provenance: Provenance = field(default_factory=Provenance)


@dataclass
class CostBreakdown:
    """Total trip cost, itemised. Not a nightly rate."""
    lodging_brl: float = 0.0
    travel_to_brl: float = 0.0
    travel_within_brl: float = 0.0
    food_brl: float = 0.0
    total_brl: float = 0.0
    per_person_brl: float = 0.0
    budget_brl: float = 0.0
    within_budget: bool = True
    estimated_items: list[str] = field(default_factory=list)


@dataclass
class PlanOption:
    id: str = ""
    label: str = ""
    axis: str = ""               # cheapest | least_travel | most_to_see
    why: str = ""
    base: Lodging = field(default_factory=Lodging)
    travel_to: TravelTo = field(default_factory=TravelTo)
    days: list[DayPlan] = field(default_factory=list)
    costs: CostBreakdown = field(default_factory=CostBreakdown)
    total_travel_min: float = 0.0
    stop_count: int = 0


@dataclass
class Assumption:
    """A guess the plan states out loud so one message can correct it."""
    key: str = ""
    value: str = ""
    text: str = ""


@dataclass
class Constraint:
    """A correction, kept so it survives the next re-solve."""
    id: str = ""
    text: str = ""
    kind: str = "note"           # party_size | max_drive_min | exclude | include | budget | pace | note
    value: str = ""
    added_at: str = field(default_factory=_now)


@dataclass
class Diagnostic:
    """A step that ran, and whether it worked. The output contract, in data."""
    step: str = ""
    status: str = "ok"           # ok | degraded | failed
    detail: str = ""
    at: str = field(default_factory=_now)


@dataclass
class TripState:
    version: int = SCHEMA_VERSION
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    intake: Intake = field(default_factory=Intake)
    assumptions: list[Assumption] = field(default_factory=list)
    constraints: list[Constraint] = field(default_factory=list)
    options: list[PlanOption] = field(default_factory=list)
    selected_option_id: str = ""
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def selected(self) -> PlanOption | None:
        for o in self.options:
            if o.id == self.selected_option_id:
                return o
        return None

    def visible_options(self) -> list[PlanOption]:
        """Once locked in, only that plan renders."""
        chosen = self.selected()
        return [chosen] if chosen else self.options

    def touch(self) -> None:
        self.updated_at = _now()

    def failed_steps(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.status == "failed"]


def to_dict(obj):
    if is_dataclass(obj):
        return {f.name: to_dict(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, list):
        return [to_dict(v) for v in obj]
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    return obj


def from_dict(cls, data):
    """Tolerant inflate: unknown keys ignored, missing keys keep their default."""
    if data is None:
        return None
    origin = typing.get_origin(cls)
    if origin is list:
        (inner,) = typing.get_args(cls)
        return [from_dict(inner, v) for v in (data or [])]
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(cls) if a is not type(None)]
        return from_dict(args[0], data)
    if is_dataclass(cls):
        hints = typing.get_type_hints(cls)
        kwargs = {}
        for f in fields(cls):
            if f.name in data:
                kwargs[f.name] = from_dict(hints[f.name], data[f.name])
        return cls(**kwargs)
    return data


def state_from_dict(data: dict) -> TripState:
    return from_dict(TripState, data)
