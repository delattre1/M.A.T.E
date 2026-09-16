"""Money and time by mode.

Every number the agent says out loud about cost comes from a function here.
"""

from __future__ import annotations

from dataclasses import dataclass

from .geo import CAR, FOOT, RIDEHAIL
from .schema import CURATED, ESTIMATED, MEASURED, Provenance
from .settings import Settings


@dataclass
class ModeCost:
    mode: str
    cost_brl: float
    duration_min: float
    provenance: Provenance
    note: str = ""


def fuel_brl(distance_km: float, s: Settings) -> tuple[float, float]:
    liters = distance_km / s.consumption_km_per_liter
    return round(liters * s.fuel_price_brl_per_liter, 2), round(liters, 2)


def car_cost(distance_km: float, duration_min: float, tolls_brl: float, s: Settings,
             *, distance_source: str = MEASURED) -> ModeCost:
    fuel, liters = fuel_brl(distance_km, s)
    detail = (
        f"{distance_km:.1f} km / {s.consumption_km_per_liter:g} km/l "
        f"x R$ {s.fuel_price_brl_per_liter:.2f} = R$ {fuel:.2f} ({liters:g} l)"
    )
    if tolls_brl:
        detail += f" + R$ {tolls_brl:.2f} em pedágios"
    # Tolls are curated even when the distance was measured, so the weaker of
    # the two sources is what the line as a whole can honestly claim.
    source = CURATED if tolls_brl and distance_source == MEASURED else (
        ESTIMATED if distance_source != MEASURED else MEASURED
    )
    return ModeCost(
        mode=CAR,
        cost_brl=round(fuel + tolls_brl, 2),
        duration_min=round(duration_min, 1),
        provenance=Provenance(source, detail),
        note="",
    )


def foot_cost(distance_km: float, duration_min: float, s: Settings,
              *, distance_source: str = MEASURED) -> ModeCost:
    """Free in money, spent in legs. The ceiling is what makes an itinerary
    followable — plenty of plans fail only because nobody walks 14 km."""
    note = ""
    if distance_km > s.max_walking_km_per_day:
        note = (
            f"{distance_km:.1f} km a pé num dia só — acima do limite de "
            f"{s.max_walking_km_per_day:g} km. Considere um trecho de carro."
        )
    return ModeCost(
        mode=FOOT,
        cost_brl=0.0,
        duration_min=round(duration_min, 1),
        provenance=Provenance(distance_source, f"{distance_km:.1f} km a {s.walking_speed_kmh:g} km/h"),
        note=note,
    )


def ridehail_cost(distance_km: float, duration_min: float, s: Settings) -> ModeCost:
    """No real price without their API. Estimate it, label it, never dress it up
    as a quote — that lie lands exactly when the user opens the app."""
    raw = s.ridehail_base_brl + distance_km * s.ridehail_per_km_brl
    return ModeCost(
        mode=RIDEHAIL,
        cost_brl=round(max(raw, s.ridehail_minimum_brl), 2),
        duration_min=round(duration_min, 1),
        provenance=Provenance(
            ESTIMATED,
            f"R$ {s.ridehail_base_brl:.2f} + {distance_km:.1f} km x R$ {s.ridehail_per_km_brl:.2f} "
            "(estimativa, não é cotação)",
        ),
        note="Preço de corrida é estimado; confira no app antes de sair.",
    )


def leg_cost(mode: str, distance_km: float, duration_min: float, s: Settings,
             *, tolls_brl: float = 0.0, distance_source: str = MEASURED) -> ModeCost:
    if mode == FOOT:
        return foot_cost(distance_km, duration_min, s, distance_source=distance_source)
    if mode == RIDEHAIL:
        return ridehail_cost(distance_km, duration_min, s)
    return car_cost(distance_km, duration_min, tolls_brl, s, distance_source=distance_source)


def food_cost(party_size: int, days: int, s: Settings) -> tuple[float, Provenance]:
    total = round(party_size * days * s.food_brl_per_person_per_day, 2)
    return total, Provenance(
        ESTIMATED,
        f"{party_size} x {days} dia(s) x R$ {s.food_brl_per_person_per_day:.2f}/pessoa/dia",
    )
