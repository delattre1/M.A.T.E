"""Where the plan lives between messages.

The rendered page was cut; the state was not. The solver cannot consume prose —
you cannot run permutations over a sentence — and constraints gathered across a
conversation need somewhere to live. So the plan persists as JSON and every
message the agent sends is rendered from it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .schema import TripState, state_from_dict, to_dict

TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "plan_template.html"

BLOCK = re.compile(r'(<script[^>]*\bid="plan"[^>]*>)(.*?)(</script>)', re.DOTALL | re.IGNORECASE)


class StateError(RuntimeError):
    pass


def dumps(state: TripState) -> str:
    return json.dumps(to_dict(state), ensure_ascii=False, indent=2)


def read_state(path: str | Path) -> TripState:
    p = Path(path)
    if not p.exists():
        raise StateError(f"{p}: não existe plano aqui ainda")
    try:
        return state_from_dict(json.loads(p.read_text(encoding="utf-8")))
    except json.JSONDecodeError as e:
        raise StateError(f"{p}: plano corrompido ({e})") from e


def write_state(path: str | Path, state: TripState) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    state.touch()
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(dumps(state), encoding="utf-8")
    tmp.replace(target)
    return target


def render_html(state: TripState, out: str | Path, *, template: str | Path = "") -> Path:
    """Optional. Not what the user receives — a local view for demos."""
    source = Path(template or TEMPLATE)
    if not source.exists():
        raise StateError(f"sem template em {source}")
    html = source.read_text(encoding="utf-8")
    if not BLOCK.search(html):
        raise StateError(f"{source}: sem bloco <script id=\"plan\">")
    # A stop named with a stray "</script>" would otherwise end the block early.
    payload = dumps(state).replace("<", "\\u003c")
    html = BLOCK.sub(lambda m: m.group(1) + "\n" + payload + "\n" + m.group(3), html, count=1)
    target = Path(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")
    return target
