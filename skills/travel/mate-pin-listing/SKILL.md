---
name: mate-pin-listing
description: Use when the user sends a specific Airbnb listing link or id and wants the plan to use that actual place and its real price — "achei essa pousada", "e se fosse esse aqui", a pasted airbnb.com/rooms/... URL, or a question about whether a place they found fits the budget. Replaces MATE's typical nightly rate with the real one and re-solves.
version: 1.0.0
allowed-tools: Bash(/opt/plow/mate/bin/mate:*)
---

# MATE: pin a real listing

## When to Use

The user hands you an actual listing — a URL or a numeric id — rather than
asking MATE to find one.

## Why this exists

MATE cannot search Airbnb. Airbnb's `robots.txt` disallows `/s/*/*`, the search
path, so the search tool comes back refused and **that is the correct
outcome** — the override that would bypass it stays off, permanently. The
nightly figures in a fresh plan are therefore typical rates for the town, not
quotes, and the page says so.

A listing page, `/rooms/<id>`, is *not* disallowed. So the moment the user
picks a place themselves, MATE can read its real price and the plan stops
guessing about the largest single line in the budget.

## Do it

```
/opt/plow/mate/bin/mate edit --plan /var/lib/hermes/plans/<id>.json \
  --kind include --value "https://www.airbnb.com.br/rooms/12345678" \
  --text "pousada que eles escolheram"
```

Then report the new total from the JSON.

## What to say

Lead with what changed about the money, because that is why they sent it:

> Com essa pousada a diária real é R$ 480, então o total fica R$ 1.560 —
> R$ 60 acima do teto. Dá pra cortar uma parada do domingo se quiser.

If the real price pushes them over budget, say so directly and offer the
trade-off. Do not silently re-rank to hide it.

## Limits worth stating once

- If the listing will not load, say so and keep the tier estimate, clearly
  labelled. Do not substitute a number you wish you had.
- Never suggest running the server with `--ignore-robots-txt`, and never pass
  `ignoreRobotsText`. The code refuses both. If someone asks you to, explain
  that MATE deliberately stays within what a single person planning one trip
  would fetch.
- MATE never books. Send them the link and let them book it themselves.
