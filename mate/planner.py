"""Assemble a plan for one base, then pick the options worth showing.

Candidates are ranked on lodging + travel + time, never on nightly rate. That
ranking is the entire product: a cheaper place an hour further out is usually
the more expensive trip, and nobody does that sum by hand across six tabs.
"""

from __future__ import annotations

from datetime import date, timedelta

from .cluster import build_day_buckets, infer_mode
from .costs import food_cost, leg_cost
from .geo import CAR, FOOT, RIDEHAIL, Router, haversine
from .ordering import order_day, to_hhmm, to_min
from .schema import (CURATED, ESTIMATED, MEASURED, Coord, CostBreakdown, DayPlan, Intake,
                     Lodging, PlanOption, Stop, TravelTo)
from .settings import Settings
from .text import fold
from .tolls import tolls_on_route, total_brl


def trip_dates(intake: Intake) -> list[str]:
    start = date.fromisoformat(intake.start_date)
    return [(start + timedelta(days=i)).isoformat() for i in range(intake.nights + 1)]


def travel_to_base(origin: Coord, base: Coord, router: Router, s: Settings,
                   *, destination: str = "") -> TravelTo:
    route = router.route([origin, base], CAR)
    tolls, as_of = tolls_on_route(
        route.geometry, round_trip=True, destination=destination,
        geometry_measured=route.provenance.source == MEASURED,
    )
    tolls_brl = total_brl(tolls)
    cost = leg_cost(CAR, route.distance_km * 2, route.duration_min * 2, s,
                    tolls_brl=tolls_brl, distance_source=route.provenance.source)
    fuel_only = round(cost.cost_brl - tolls_brl, 2)
    return TravelTo(
        mode=CAR,
        distance_km=round(route.distance_km * 2, 1),
        duration_min=round(route.duration_min * 2, 1),
        fuel_cost_brl=fuel_only,
        toll_cost_brl=tolls_brl,
        tolls=tolls,
        total_cost_brl=cost.cost_brl,
        round_trip=True,
        liters=round(route.distance_km * 2 / s.consumption_km_per_liter, 2),
        provenance=cost.provenance,
    )


def _base_stop(lodging: Lodging) -> Stop:
    return Stop(id="base", name=lodging.name, lat=lodging.lat, lon=lodging.lon, kind="base")


def build_day(day_date: str, bucket: list[Stop], base: Coord, router: Router, s: Settings,
              *, has_car: bool, start_time: str) -> DayPlan:
    if not bucket:
        return DayPlan(date=day_date, travel_within=FOOT, mode_reason="dia livre", notes=["dia livre"])

    mode, spread, reason = infer_mode(bucket, base, has_car=has_car, s=s)
    profile = FOOT if mode == FOOT else CAR
    coords = [base] + [Coord(st.lat, st.lon) for st in bucket]
    matrix = router.table(coords, profile)

    ordered = order_day(bucket, matrix, s, mode=mode, start_time=start_time)

    cost_total = 0.0
    walking = 0.0
    notes = list(ordered.notes)
    for leg in ordered.legs:
        c = leg_cost(mode, leg.distance_km, leg.duration_min, s,
                     distance_source=matrix.provenance.source)
        leg.cost_brl = c.cost_brl
        cost_total += c.cost_brl
        if mode == FOOT:
            walking += leg.distance_km

    if mode == FOOT:
        load = leg_cost(FOOT, walking, 0.0, s, distance_source=matrix.provenance.source)
        if load.note:
            notes.append(load.note)
    if mode == RIDEHAIL:
        notes.append("Corridas de app são estimativa, não cotação.")

    return DayPlan(
        date=day_date,
        travel_within=mode,
        mode_reason=reason,
        spread_km=round(spread, 2),
        stops=ordered.stops,
        legs=ordered.legs,
        distance_km=ordered.distance_km,
        travel_duration_min=ordered.travel_duration_min,
        walking_km=round(walking, 2),
        travel_cost_brl=round(cost_total, 2),
        notes=notes,
    )


def plan_for_lodging(lodging: Lodging, intake: Intake, pois: list[Stop], router: Router,
                     s: Settings, *, has_car: bool = True) -> PlanOption:
    base = Coord(lodging.lat, lodging.lon)
    origin = intake.origin_coord or base
    dates = trip_dates(intake)

    travel_to = travel_to_base(origin, base, router, s,
                               destination=lodging.city or intake.region)
    buckets = build_day_buckets([_clone(p) for p in pois], base, len(dates), s)

    days: list[DayPlan] = []
    for i, (day_date, bucket) in enumerate(zip(dates, buckets)):
        # You cannot tour at 09:00 on a day you are still driving up the serra.
        start = to_hhmm(to_min(s.day_start) + travel_to.duration_min / 2) if i == 0 else s.day_start
        days.append(build_day(day_date, bucket, base, router, s, has_car=has_car, start_time=start))

    within = round(sum(d.travel_cost_brl for d in days), 2)
    food, food_prov = food_cost(intake.party_size, len(dates), s)
    lodging_total = round(lodging.price_per_night_brl * intake.nights, 2)
    total = round(lodging_total + travel_to.total_cost_brl + within + food, 2)

    soft = []
    if lodging.provenance.source != MEASURED:
        soft.append("hospedagem")
    if travel_to.provenance.source != MEASURED:
        soft.append("viagem de ida e volta")
    if any(d.travel_within == RIDEHAIL for d in days):
        soft.append("corridas de app")
    soft.append("alimentação")

    costs = CostBreakdown(
        lodging_brl=lodging_total,
        travel_to_brl=travel_to.total_cost_brl,
        travel_within_brl=within,
        food_brl=food,
        total_brl=total,
        per_person_brl=round(total / max(1, intake.party_size), 2),
        budget_brl=intake.budget_total_brl,
        within_budget=(not intake.budget_total_brl) or total <= intake.budget_total_brl,
        estimated_items=soft,
    )

    lodging.total_price_brl = lodging_total
    return PlanOption(
        id=f"opt-{lodging.id.replace('/', '-')}",
        base=lodging,
        travel_to=travel_to,
        days=days,
        costs=costs,
        total_travel_min=round(travel_to.duration_min + sum(d.travel_duration_min for d in days), 1),
        stop_count=sum(len(d.stops) for d in days),
    )


AXES = (
    ("cheapest", "Mais barato", "menor custo total da viagem, não menor diária"),
    ("least_travel", "Menos deslocamento", "menos tempo em trânsito somando ida, volta e os dias"),
    ("most_to_see", "Mais para ver", "mais paradas sem estourar o orçamento"),
)


def build_options(candidates: list[tuple[Lodging, list[Stop]]], intake: Intake, router: Router,
                  s: Settings, *, has_car: bool = True, limit: int = 3) -> list[PlanOption]:
    """Three options that differ on something that matters. Three variations of
    the same plan help nobody choose.

    Each candidate carries its own town's stops: a base in Teresópolis is not
    judged against Petrópolis's museums.
    """
    plans = [plan_for_lodging(l, intake, pois, router, s, has_car=has_car)
             for l, pois in candidates]
    if not plans:
        return []

    picks: dict[str, PlanOption] = {}
    rankers = {
        "cheapest": lambda p: (p.costs.total_brl, p.total_travel_min),
        "least_travel": lambda p: (p.total_travel_min, p.costs.total_brl),
        "most_to_see": lambda p: (-p.stop_count, p.costs.total_brl),
    }
    for axis, label, why in AXES:
        for cand in sorted(plans, key=rankers[axis]):
            if cand.id in picks:
                continue
            cand.axis, cand.label, cand.why = axis, label, why
            picks[cand.id] = cand
            break

    kept = _only_honest_labels(list(picks.values()), rankers)
    kept.sort(key=lambda p: p.costs.total_brl)
    return _drop_near_duplicates(kept)[:limit]


def _meaningfully_different(a: PlanOption, b: PlanOption) -> bool:
    """Two stays in the same town, fifty centavos apart, are one option shown
    twice — which is the thing offering options at all was supposed to avoid."""
    if fold(a.base.city) != fold(b.base.city):
        return True
    gap = abs(a.costs.total_brl - b.costs.total_brl)
    if gap >= max(50.0, 0.05 * max(a.costs.total_brl, b.costs.total_brl, 1.0)):
        return True
    return abs(a.stop_count - b.stop_count) >= 2


def _drop_near_duplicates(options: list[PlanOption]) -> list[PlanOption]:
    kept: list[PlanOption] = []
    for o in options:
        if all(_meaningfully_different(o, k) for k in kept):
            kept.append(o)
    return kept


def _only_honest_labels(picks: list[PlanOption], rankers: dict) -> list[PlanOption]:
    """Every option shown must really be the best of the shown set on the axis
    it claims.

    One base often wins two axes at once. Handing the runner-up the label
    anyway puts "least travel" on the option with more travel than its
    neighbour, which is the exact failure three near-identical options were
    supposed to avoid. Dropping it leaves fewer options and no lie.
    """
    kept = list(picks)
    while len(kept) > 1:
        liar = next((o for o in kept if min(kept, key=rankers[o.axis]) is not o), None)
        if liar is None:
            break
        kept.remove(liar)
    return kept


def _clone(stop: Stop) -> Stop:
    from dataclasses import replace
    return replace(stop)
