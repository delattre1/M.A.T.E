"""The plan, as messages someone reads on a phone.

The agent writes the conversation around these, but never the figures inside
them: every number here is formatted from solver output, so there is no step
where a model retypes a total.
"""

from __future__ import annotations

from datetime import date

from .lodging import booking_search_url
from .schema import DayPlan, PlanOption, TripState

WEEKDAYS = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")
MODE = {"foot": "a pé", "car": "de carro", "ridehail": "de app"}
SMS_LIMIT = 4000


def brl(v: float) -> str:
    return "R$ " + f"{v:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")


def approx(v: float) -> str:
    return "~" + brl(v)


def dm(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{WEEKDAYS[d.weekday()]} {d.day:02d}/{d.month:02d}"


def dur(minutes: float) -> str:
    m = int(round(minutes))
    if m < 60:
        return f"{m}min"
    return f"{m // 60}h" + (f"{m % 60:02d}" if m % 60 else "")


def km(v: float) -> str:
    return f"{v:.1f} km".replace(".", ",")


def _header(state: TripState) -> str:
    i = state.intake
    head = [f"{i.region} · {dm(i.start_date)} a {dm(i.end_date)} · {i.party_size} pessoas"]
    if i.budget_total_brl:
        head.append(f"Teto: {brl(i.budget_total_brl)}")
    return "\n".join(head)


def _caveats(state: TripState) -> list[str]:
    out = [a.text for a in state.assumptions]
    covered = {a.key for a in state.assumptions}
    seen: set[str] = set()
    for d in state.diagnostics:
        # car and foot report the same outage, and an assumption already said it.
        if d.status != "degraded" or d.detail in seen:
            continue
        if d.step.startswith("osrm") and "routing" in covered:
            continue
        seen.add(d.detail)
        out.append(f"Atenção: {d.detail}")
    return out


def _body(state: TripState, o: PlanOption) -> list[str]:
    """The plan itself: the days with their stops named, then the money."""
    msgs = [day_message(d) for d in o.days if d.stops]
    msgs.append(costs_message(o))

    # We plan and link out; the human books.
    i = state.intake
    if o.base.url:
        msgs.append(f"Pra reservar: {o.base.url}")
    elif o.base.city:
        msgs.append(
            "Não achei o site dessa pousada. Pra procurar hospedagem na cidade:\n"
            + booking_search_url(o.base.city, i.start_date, i.end_date, i.party_size)
        )
    return msgs


def plan_messages(state: TripState) -> list[str]:
    """What arrives first: the actual plan for the best option, not a menu.

    Asking someone to choose between three totals before they have seen a
    single attraction is asking them to decide on the one axis a spreadsheet
    could already give them. Lead with the trip; offer the swap afterwards.
    """
    options = state.visible_options()
    if not options:
        return ["Ainda não consegui montar um plano."]

    best = options[0]
    where = f"{best.base.name}" + (f", {best.base.city}" if best.base.city else "")
    opening = (
        f"{_header(state)}\n\n"
        f"Melhor opção: {where}\n"
        f"{brl(best.costs.total_brl)} no total · {brl(best.costs.per_person_brl)}/pessoa\n"
        f"{best.why}"
    )

    msgs = [opening] + _body(state, best)

    tail = []
    others = options[1:]
    if others:
        tail.append("Outra opção, se quiser comparar:" if len(others) == 1
                    else "Outras opções, se quiser comparar:")
        for o in others:
            delta = o.costs.total_brl - best.costs.total_brl
            sign = f"+{brl(delta)}" if delta >= 0 else f"-{brl(abs(delta))}"
            tail.append(f"· {o.label} — {o.base.name}, {brl(o.costs.total_brl)} ({sign})")
    tail += _caveats(state)
    tail.append("Te mando o roteiro completo de outra se preferir, ou ajusto essa aqui."
                if others else "Posso ajustar qualquer coisa.")
    msgs.append("\n".join(tail))
    return _fit(msgs)


def options_messages(state: TripState) -> list[str]:
    """The comparison on its own, for when someone asks to see the choices."""
    lines = [_header(state), ""]
    for n, o in enumerate(state.visible_options(), 1):
        fits = "" if o.costs.within_budget else "  (acima do teto)"
        lines.append(
            f"{n}) {o.label} — {o.base.name}"
            f"\n   {brl(o.costs.total_brl)} no total · {brl(o.costs.per_person_brl)}/pessoa{fits}"
            f"\n   {km(o.travel_to.distance_km)} de estrada · {dur(o.total_travel_min)} em trânsito"
            f" · {o.stop_count} paradas"
            f"\n   {o.why}"
        )
    first = "\n".join(lines).strip()
    tail = _caveats(state) + ["Qual delas? Posso ajustar qualquer coisa."]
    return _fit([first, "\n".join(tail)])


def day_message(day: DayPlan) -> str:
    head = f"{dm(day.date)} · {MODE.get(day.travel_within, day.travel_within)}"
    if day.mode_reason:
        head += f"\n({day.mode_reason})"
    rows = [head, ""]
    for s in day.stops:
        when = f"{s.arrive} " if s.arrive else ""
        rows.append(f"{when}{s.name}" + (f" · {dur(s.dwell_min)}" if s.dwell_min else ""))
    foot = []
    if day.walking_km:
        foot.append(f"{km(day.walking_km)} a pé")
    if day.travel_within != "foot" and day.distance_km:
        foot.append(f"{km(day.distance_km)} rodados")
    if day.travel_cost_brl:
        foot.append(approx(day.travel_cost_brl) if day.travel_within == "ridehail"
                    else brl(day.travel_cost_brl))
    if foot:
        rows += ["", " · ".join(foot)]
    rows += [n for n in day.notes if n]
    return "\n".join(rows).strip()


def costs_message(o: PlanOption) -> str:
    t = o.travel_to
    rows = [
        f"Custo total: {brl(o.costs.total_brl)}",
        "",
        f"Hospedagem  ~{brl(o.costs.lodging_brl)}",
        f"Ida e volta  {brl(o.costs.travel_to_brl)}"
        + (f"  ({brl(t.fuel_cost_brl)} combustível + ~{brl(t.toll_cost_brl)} pedágio)"
           if t.toll_cost_brl else f"  ({t.liters:g} l)"),
    ]
    if o.costs.travel_within_brl:
        rows.append(f"No destino   {brl(o.costs.travel_within_brl)}")
    rows.append(f"Comida      ~{brl(o.costs.food_brl)}")
    rows += ["", f"{brl(o.costs.per_person_brl)} por pessoa"]
    if o.costs.budget_brl:
        rows.append(
            f"Cabe no teto de {brl(o.costs.budget_brl)}." if o.costs.within_budget
            else f"Passou {brl(o.costs.total_brl - o.costs.budget_brl)} do teto de {brl(o.costs.budget_brl)}."
        )
    if o.costs.estimated_items:
        rows += ["", "~ é estimativa: " + ", ".join(o.costs.estimated_items) + "."]
    return "\n".join(rows)


def itinerary_messages(state: TripState, option: PlanOption | None = None) -> list[str]:
    o = option or state.selected() or (state.options[0] if state.options else None)
    if o is None:
        return ["Ainda não tem plano montado."]

    head = f"{o.label} — {o.base.name}" + (f", {o.base.city}" if o.base.city else "")
    return _fit([head] + _body(state, o))


def constraints_message(state: TripState) -> str:
    if not state.constraints:
        return "Nenhum ajuste registrado ainda."
    return "O que já combinamos:\n" + "\n".join(f"· {c.text}" for c in state.constraints)


def _fit(messages: list[str]) -> list[str]:
    """Split anything over the per-message ceiling on a line boundary."""
    out: list[str] = []
    for m in messages:
        m = m.strip()
        if not m:
            continue
        while len(m) > SMS_LIMIT:
            cut = m.rfind("\n", 0, SMS_LIMIT)
            cut = cut if cut > 0 else SMS_LIMIT
            out.append(m[:cut].strip())
            m = m[cut:].strip()
        out.append(m)
    return out
