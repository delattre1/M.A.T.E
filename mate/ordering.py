"""Best order for a day's stops.

Six stops is 720 orderings, so this brute-forces every one of them. No solver
library, no heuristic, no apology — at this scale exhaustive search is simply
cheaper than the code you would write to avoid it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import permutations

from .geo import Matrix
from .schema import Stop, TravelLeg
from .settings import Settings

BRUTE_FORCE_LIMIT = 8


def to_min(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def to_hhmm(minutes: float) -> str:
    m = int(round(minutes)) % (24 * 60)
    return f"{m // 60:02d}:{m % 60:02d}"


def dur_min(minutes: float) -> str:
    m = int(round(minutes))
    return f"{m}min" if m < 60 else f"{m // 60}h" + (f"{m % 60:02d}" if m % 60 else "")


@dataclass
class DayOrder:
    stops: list[Stop] = field(default_factory=list)
    legs: list[TravelLeg] = field(default_factory=list)
    distance_km: float = 0.0
    travel_duration_min: float = 0.0
    end_time: str = ""
    notes: list[str] = field(default_factory=list)


def _windows(s: Settings) -> list[tuple[int, int]]:
    return [
        (to_min(s.lunch_window_start), to_min(s.lunch_window_end)),
        (to_min(s.dinner_window_start), to_min(s.dinner_window_end)),
    ]


def _simulate(order: tuple[int, ...], stops: list[Stop], matrix: Matrix, start_min: int,
              s: Settings, *, enforce_meals: bool
              ) -> tuple[float, float, float, list[tuple[int, int]], float] | None:
    """Walk the day on the clock. None means this order does not fit."""
    windows = _windows(s)
    day_end = to_min(s.day_end)
    clock = float(start_min)
    travel_min = travel_km = idle_min = 0.0
    schedule: list[tuple[int, int]] = []
    prev = 0  # index 0 is the base

    for idx in order:
        leg_min = matrix.durations_min[prev][idx]
        travel_min += leg_min
        travel_km += matrix.distances_km[prev][idx]
        clock += leg_min
        stop = stops[idx - 1]
        if stop.kind == "meal" and enforce_meals:
            fits = next((w for w in windows if w[0] <= clock <= w[1]), None)
            if fits is None:
                nxt = next((w for w in windows if w[0] > clock), None)
                if nxt is None:
                    return None
                idle_min += nxt[0] - clock
                clock = float(nxt[0])  # wait for the kitchen to open
        arrive = clock
        clock += stop.dwell_min
        schedule.append((int(round(arrive)), int(round(clock))))
        prev = idx

    travel_min += matrix.durations_min[prev][0]
    travel_km += matrix.distances_km[prev][0]
    clock += matrix.durations_min[prev][0]
    if clock > day_end:
        return None
    return travel_min, travel_km, clock, schedule, idle_min


def order_day(stops: list[Stop], matrix: Matrix, s: Settings, *, mode: str,
              start_time: str = "") -> DayOrder:
    """`matrix` covers [base] + stops, base at index 0."""
    if not stops:
        return DayOrder(notes=["dia sem paradas"])

    start_min = to_min(start_time or s.day_start)
    idxs = list(range(1, len(stops) + 1))
    notes: list[str] = []

    if len(stops) > BRUTE_FORCE_LIMIT:
        notes.append(
            f"{len(stops)} paradas: ordenadas por vizinho mais próximo em vez de força bruta"
        )
        candidates = [tuple(_nearest_neighbour(idxs, matrix))]
    else:
        candidates = list(permutations(idxs))

    best = None
    for enforce in (True, False):
        scored = []
        for order in candidates:
            sim = _simulate(order, stops, matrix, start_min, s, enforce_meals=enforce)
            if sim:
                # Waiting counts against an order as much as driving does: an
                # itinerary with a dead three-hour hole before dinner is not a
                # better plan than one that drives ten minutes more.
                scored.append((sim[0] + sim[4], sim[0], sim[1], order, sim))
        if scored:
            scored.sort(key=lambda t: (round(t[0], 1), round(t[1], 1), round(t[2], 2)))
            best = scored[0]
            if not enforce:
                notes.append("não deu para encaixar a refeição na janela usual de horário")
            break

    if best is None:
        notes.append("o dia não fecha dentro do horário — encurtei para caber")
        order = tuple(_nearest_neighbour(idxs, matrix))
        sim = _simulate(order, stops, matrix, start_min, s, enforce_meals=False)
        while sim is None and len(order) > 1:
            order = order[:-1]
            notes.append("removi a última parada por falta de tempo")
            sim = _simulate(order, stops, matrix, start_min, s, enforce_meals=False)
        if sim is None:
            return DayOrder(notes=["não foi possível montar esse dia"])
        best = (sim[0] + sim[4], sim[0], sim[1], order, sim)

    _, travel_min, travel_km, order, (_, _, end_clock, schedule, idle_min) = best
    if idle_min > 90:
        notes.append(f"tem {dur_min(idle_min)} de espera até a refeição — dá para remanejar")

    ordered: list[Stop] = []
    for position, idx in enumerate(order):
        stop = stops[idx - 1]
        arrive, depart = schedule[position]
        stop.arrive, stop.depart = to_hhmm(arrive), to_hhmm(depart)
        ordered.append(stop)

    legs: list[TravelLeg] = []
    prev_idx, prev_id = 0, "base"
    for idx, stop in zip(order, ordered):
        legs.append(
            TravelLeg(
                from_id=prev_id,
                to_id=stop.id,
                mode=mode,
                distance_km=round(matrix.distances_km[prev_idx][idx], 2),
                duration_min=round(matrix.durations_min[prev_idx][idx], 1),
                provenance=matrix.provenance,
            )
        )
        prev_idx, prev_id = idx, stop.id
    legs.append(
        TravelLeg(
            from_id=prev_id,
            to_id="base",
            mode=mode,
            distance_km=round(matrix.distances_km[prev_idx][0], 2),
            duration_min=round(matrix.durations_min[prev_idx][0], 1),
            provenance=matrix.provenance,
        )
    )

    return DayOrder(
        stops=ordered,
        legs=legs,
        distance_km=round(travel_km, 2),
        travel_duration_min=round(travel_min, 1),
        end_time=to_hhmm(end_clock),
        notes=notes,
    )


def _nearest_neighbour(idxs: list[int], matrix: Matrix) -> list[int]:
    remaining, order, cur = list(idxs), [], 0
    while remaining:
        nxt = min(remaining, key=lambda i: matrix.durations_min[cur][i])
        remaining.remove(nxt)
        order.append(nxt)
        cur = nxt
    return order
