"""The pipeline, start to finish.

Re-planning on disruption is not a separate feature: an attraction closed, the
booking fell through, the party got smaller — it is this same function with one
more constraint in the state.
"""

from __future__ import annotations

from . import constraints as cons
from .geo import Router, haversine
from .geocode import geocode
from .http import SourceError
from .lodging import find_lodging
from .overpass import bucket_by_center, find_pois
from .text import fold
from .planner import build_options
from .schema import Assumption, Coord, Diagnostic, Lodging, Stop, TripState
from .settings import Settings
from .wikidata import enrich


class SolveError(RuntimeError):
    """The plan cannot be built. Better a refusal than a confident fiction."""


def _gather(towns: list[str], intake, settings: Settings, filters, state: TripState
            ) -> list[tuple[Lodging, list[Stop]]]:
    """Every candidate town's stops and stays, in two requests total."""
    centers: list[Coord] = []
    names: list[str] = []
    for town in towns:
        try:
            centers.append(geocode(town, settings))
            names.append(town)
        except SourceError as e:
            state.diagnostics.append(Diagnostic(f"geocode:{town}", "failed", str(e)))
    if not centers:
        raise SolveError("não consegui localizar nenhum dos destinos")

    radius = settings.town_radius_km
    pois = cons.filter_pois(find_pois(centers, radius, settings), filters)
    pois, note = enrich(pois, settings)
    if note:
        state.diagnostics.append(Diagnostic("wikidata", "degraded", note))
    buckets = bucket_by_center(pois, centers, radius)

    stays = find_lodging(centers, names, settings, radius_km=radius, tier=filters.tier,
                         party_size=intake.party_size, nights=intake.nights)

    candidates: list[tuple[Lodging, list[Stop]]] = []
    for i, town in enumerate(names):
        label = fold(town.split(",")[0])
        here = [s for s in stays if fold(s.city) == label]
        here.sort(key=lambda l: haversine(Coord(l.lat, l.lon), centers[i]))
        state.diagnostics.append(Diagnostic(
            f"town:{town}", "ok" if here and buckets[i] else "degraded",
            f"{len(buckets[i])} paradas, {len(here)} hospedagens"))
        candidates += [(stay, buckets[i]) for stay in here[: settings.lodgings_per_town]
                       if buckets[i]]
    return candidates


def solve(state: TripState, s: Settings, *, router: Router | None = None) -> TripState:
    intake, settings, filters = cons.apply(state, s)
    state.diagnostics = []
    router = router or Router(settings)

    if not intake.start_date or not intake.end_date:
        raise SolveError("faltam as datas da viagem")

    towns = intake.towns or ([intake.region] if intake.region else [])
    if not towns:
        raise SolveError("faltam os destinos candidatos")

    try:
        intake.origin_coord = intake.origin_coord or geocode(intake.origin, settings)
    except SourceError as e:
        state.diagnostics.append(Diagnostic("geocode:origem", "failed", str(e)))
        raise SolveError(f"não consegui localizar a origem '{intake.origin}': {e}") from e

    try:
        candidates = _gather(towns, intake, settings, filters, state)
    except SourceError as e:
        state.diagnostics.append(Diagnostic("fontes", "failed", str(e)))
        raise SolveError(f"não consegui buscar os dados da viagem: {e}") from e

    if not candidates:
        raise SolveError("não achei hospedagem mapeada em: " + ", ".join(towns))

    for profile in ("car", "foot"):
        up = router.available(profile)
        state.diagnostics.append(Diagnostic(
            f"osrm:{profile}", "ok" if up else "degraded",
            "rotas medidas" if up else "OSRM fora do ar — distâncias estimadas em linha reta",
        ))

    options = build_options(candidates, intake, router, settings, has_car=filters.has_car)
    options = cons.drop_over_driving(options, filters)
    if not options:
        raise SolveError("não consegui montar nenhuma opção com essas restrições")

    state.intake = intake
    state.options = options
    state.assumptions = _assumptions(filters, state)
    state.touch()
    return state


def _assumptions(filters: cons.Filters, state: TripState) -> list[Assumption]:
    """Stated at the top, correctable in one message. A visible assumption beats
    a question asked up front."""
    out = [
        Assumption("has_car", "true",
                   "Assumi que vocês vão de carro próprio. Se não for, é só dizer.")
        if filters.has_car else
        Assumption("has_car", "false",
                   "Assumi que vocês não têm carro — contei corridas de app."),
        Assumption("tier", filters.tier,
                   f"Diárias na faixa '{filters.tier}' típica da região, não cotação de um anúncio."),
    ]
    if any(d.step.startswith("osrm") and d.status == "degraded" for d in state.diagnostics):
        out.append(Assumption("routing", "estimated",
                              "OSRM fora do ar: distâncias e tempos são estimativas."))
    return out
