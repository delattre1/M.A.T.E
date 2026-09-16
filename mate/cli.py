"""The deterministic surface the agent calls.

Every command prints one JSON object and exits non-zero when it did not work.
A chat turn returns the turn's status, not the script's — without that the
agent will happily report a plan built on a function that failed.

`messages` in the output is the plan already worded and formatted. The agent
sends those as they are; it never retypes a figure out of them.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import constraints as cons
from . import sms
from .geo import Router
from .plan_state import StateError, read_state, render_html, write_state
from .schema import Intake, TripState
from .settings import Settings
from .solve import SolveError, solve


def emit(payload: dict, code: int = 0) -> int:
    payload["ok"] = code == 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return code


def summarise(state: TripState, path: Path) -> dict:
    return {
        "plan": str(path),
        "selected_option_id": state.selected_option_id,
        "constraints": [c.text for c in state.constraints],
        "assumptions": [a.text for a in state.assumptions],
        "degraded": [f"{d.step}: {d.detail}" for d in state.diagnostics if d.status != "ok"],
        "options": [
            {
                "id": o.id,
                "label": o.label,
                "why": o.why,
                "base": o.base.name,
                "city": o.base.city,
                "total_brl": o.costs.total_brl,
                "total": sms.brl(o.costs.total_brl),
                "per_person": sms.brl(o.costs.per_person_brl),
                "lodging": sms.brl(o.costs.lodging_brl),
                "travel_to": sms.brl(o.costs.travel_to_brl),
                "travel_within": sms.brl(o.costs.travel_within_brl),
                "food": sms.brl(o.costs.food_brl),
                "within_budget": o.costs.within_budget,
                "estimated_items": o.costs.estimated_items,
                "drive_km_round_trip": o.travel_to.distance_km,
                "stops": o.stop_count,
                "days": [
                    {
                        "date": d.date,
                        "mode": d.travel_within,
                        "why": d.mode_reason,
                        "walking_km": d.walking_km,
                        "stops": [s.name for s in d.stops],
                        "notes": d.notes,
                    }
                    for d in o.days
                ],
            }
            for o in state.visible_options()
        ],
    }


def _messages(state: TripState) -> list[str]:
    return sms.itinerary_messages(state) if state.selected() else sms.plan_messages(state)


def _resolve(state: TripState, path: str) -> int:
    s = Settings.from_env()
    state = solve(state, s, router=Router(s))
    out = write_state(path, state)
    payload = summarise(state, out)
    payload["messages"] = _messages(state)
    return emit(payload)


def cmd_plan(a) -> int:
    # Replanning over a plan that already exists silently discards every
    # correction the person has made. The agent will not reliably remember not
    # to — it imitates its own earlier calls — so the refusal lives here, and
    # it names the command that should have been run instead.
    existing = Path(a.out)
    if existing.exists() and not a.force:
        try:
            prior = read_state(existing)
        except StateError:
            prior = None
        if prior and prior.options:
            return emit({
                "error": "já existe um plano nesse arquivo; replanejar apaga as correções feitas até aqui",
                "use_instead": f"mate edit --plan {a.out} --kind <{'|'.join(cons.KINDS)}> --value <valor> --text \"<o que a pessoa disse>\"",
                "would_discard": [c.text for c in prior.constraints] or ["(nenhuma correção ainda)"],
                "if_this_is_a_different_trip": "use outro --out, ou --force para recomeçar este",
                "stage": "input",
            }, 2)

    towns = a.town or []
    # --region is a label, not an input to the search. Requiring it alongside
    # --town only creates a way to get the call wrong.
    region = a.region or " / ".join(t.split(",")[0].strip() for t in towns)
    if not region:
        return emit({"error": "informe --region ou pelo menos um --town", "stage": "input"}, 2)

    state = TripState(intake=Intake(
        origin=a.origin, region=region, start_date=a.start, end_date=a.end,
        party_size=a.people, budget_total_brl=a.budget, towns=towns,
    ))
    if a.tier:
        cons.add(state, "tier", a.tier, f"faixa de diária: {a.tier}")
    if not a.car:
        cons.add(state, "has_car", "false", "sem carro próprio")
    return _resolve(state, a.out)


def cmd_edit(a) -> int:
    state = read_state(a.plan)
    cons.add(state, a.kind, a.value, a.text or f"{a.kind}={a.value}")
    return _resolve(state, a.plan)


def cmd_lock(a) -> int:
    state = read_state(a.plan)
    ids = [o.id for o in state.options]
    if a.option not in ids:
        return emit({"error": f"opção {a.option} não existe", "options": ids}, 2)
    state.selected_option_id = a.option
    out = write_state(a.plan, state)
    payload = summarise(state, out)
    payload["messages"] = sms.itinerary_messages(state)
    return emit(payload)


def cmd_say(a) -> int:
    state = read_state(a.plan)
    if a.what == "options":
        messages = sms.options_messages(state)
    elif a.what == "itinerary":
        chosen = next((o for o in state.options if o.id == a.option), None) if a.option else None
        messages = sms.itinerary_messages(state, chosen)
    else:
        messages = [sms.constraints_message(state)]
    return emit({"messages": messages, "count": len(messages)})


def cmd_show(a) -> int:
    return emit(summarise(read_state(a.plan), Path(a.plan)))


def cmd_render(a) -> int:
    state = read_state(a.plan)
    out = render_html(state, a.out)
    return emit({"html": str(out), "note": "visualização local; o usuário recebe por SMS"})


def cmd_doctor(a) -> int:
    from .http import SourceError, get_json
    s = Settings.from_env()
    checks: dict[str, dict] = {}

    router = Router(s)
    for profile in ("car", "foot"):
        up = router.available(profile)
        checks[f"osrm:{profile}"] = {
            "ok": up,
            "detail": "roteamento medido" if up else "sem resposta; distâncias virão estimadas",
        }

    for name, url, params in (
        ("photon", s.photon_url, {"q": "Petrópolis", "limit": 1}),
        ("overpass", s.overpass_url, None),
    ):
        try:
            if params:
                get_json(url, params, s, rate_limit_key=name)
            checks[name] = {"ok": True, "detail": "disponível"}
        except SourceError as e:
            checks[name] = {"ok": False, "detail": str(e)}

    try:
        from .mcp_stdio import MCPError, airbnb_client
        with airbnb_client(timeout=120.0) as c:
            names = sorted(t.name for t in c.list_tools())
        checks["mcp:airbnb"] = {
            "ok": "airbnb_listing_details" in names,
            "detail": f"{names}; busca bloqueada por robots.txt (esperado), detalhes liberados",
        }
    except (MCPError, FileNotFoundError, OSError) as e:
        checks["mcp:airbnb"] = {"ok": False, "detail": str(e)}

    fatal = [k for k, v in checks.items() if not v["ok"] and k in ("photon", "overpass")]
    return emit({"checks": checks, "fatal": fatal}, 1 if fatal else 0)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mate", description="Make A Trip Easy")
    sub = p.add_subparsers(dest="cmd", required=True)

    plan = sub.add_parser("plan", help="montar um plano do zero")
    plan.add_argument("--origin", required=True)
    plan.add_argument("--region", default="",
                      help="rótulo da região; se omitido, vem das cidades")
    plan.add_argument("--town", action="append", default=[],
                      help="cidade candidata; repita para comparar várias")
    plan.add_argument("--start", required=True, help="YYYY-MM-DD")
    plan.add_argument("--end", required=True, help="YYYY-MM-DD")
    plan.add_argument("--people", type=int, default=2)
    plan.add_argument("--budget", type=float, default=0.0)
    plan.add_argument("--tier", default="", choices=["", "low", "medium", "high"])
    plan.add_argument("--no-car", dest="car", action="store_false", default=True)
    plan.add_argument("--out", default="plan.json")
    plan.add_argument("--force", action="store_true",
                      help="recomeçar do zero por cima de um plano existente")
    plan.set_defaults(fn=cmd_plan)

    edit = sub.add_parser("edit", help="registrar uma correção e recalcular")
    edit.add_argument("--plan", required=True)
    edit.add_argument("--kind", required=True, choices=list(cons.KINDS))
    edit.add_argument("--value", required=True)
    edit.add_argument("--text", default="")
    edit.set_defaults(fn=cmd_edit)

    lock = sub.add_parser("lock", help="fixar a opção escolhida")
    lock.add_argument("--plan", required=True)
    lock.add_argument("--option", required=True)
    lock.set_defaults(fn=cmd_lock)

    say = sub.add_parser("say", help="gerar as mensagens do plano")
    say.add_argument("--plan", required=True)
    say.add_argument("--what", default="itinerary",
                     choices=["options", "itinerary", "constraints"])
    say.add_argument("--option", default="")
    say.set_defaults(fn=cmd_say)

    show = sub.add_parser("show", help="ler o plano atual")
    show.add_argument("--plan", required=True)
    show.set_defaults(fn=cmd_show)

    render = sub.add_parser("render", help="gerar HTML local (demo, não é o que o usuário recebe)")
    render.add_argument("--plan", required=True)
    render.add_argument("--out", default="plan.html")
    render.set_defaults(fn=cmd_render)

    doctor = sub.add_parser("doctor", help="checar fontes de dados")
    doctor.set_defaults(fn=cmd_doctor)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except SolveError as e:
        return emit({"error": str(e), "stage": "solve"}, 1)
    except StateError as e:
        return emit({"error": str(e), "stage": "state"}, 2)
    except (FileNotFoundError, ValueError) as e:
        return emit({"error": str(e), "stage": "input"}, 2)


if __name__ == "__main__":
    raise SystemExit(main())
