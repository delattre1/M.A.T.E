# MATE — Make A Trip Easy

A trip planner you talk to by text message. Answer four questions in one reply
and get back a day-by-day plan: where to stay, what to see, in what order, how
you get around, and what the whole thing actually costs.

MATE is a variant agent for the [Plow](https://github.com/plow-pbc) Hermes
fleet. It runs in a container, holds a phone line, and plans short trips
reachable by car or on foot.

## The problem

The hard part of planning a weekend away is not finding a place to stay. It is
holding several variables in your head at once — budget, dates, where the
lodging sits, how far the sights are from it, how you would move between them,
whether the total still fits — while every new browser tab resets the
arithmetic.

People choose the lodging first and discover the travel afterwards. A cheaper
pousada an hour further out is often the more expensive trip once fuel and
tolls are counted, and nobody does that sum by hand across six tabs.

MATE does that sum. It ranks candidate towns on **lodging + travel + time**,
never on nightly rate.

## Install

Five minutes, most of it waiting for a build. You need Docker with Compose,
git, and Python 3.

**1. Get the Plow CLI and your own line.** Usage bills your account, so you
mint your own credential — MATE never ships one.

```bash
git clone https://github.com/plow-pbc/plow-agents.git
export PATH="$PWD/plow-agents/bin:$PATH"
plow-agents login --new-line
```

`login` texts an activation phrase to your phone. Then list your lines and mint
a credential for the one you want MATE to answer on:

```bash
plow-agents lines
plow-agents mint ln_xxxxxxxx
```

That writes `./plow-credentials`. Move it next to MATE's `compose.yml`.

**2. Build and start MATE.**

```bash
git clone https://github.com/thiagocamerato757/M.A.T.E.git && cd M.A.T.E
AGENT_ID=mate docker compose up --build -d
```

Watch for `plow-init: configured ... as cht_` in `docker compose logs -f`. Then
text the line and say you want to plan a trip.

`AGENT_ID` is what credits usage to MATE on the
[Agent Index](https://aiworthusing.com/agent-index). Leave it empty to turn
reporting off.

## Measured distances (optional, recommended)

Without a router, MATE still plans — but every distance and time is a
straight-line estimate, and it says so on every affected line. With one, they
are real road distances and the toll matching follows the actual route.

```bash
./infra/osrm/setup.sh
docker compose -f compose.yml -f compose.osrm.yml up -d
```

`setup.sh` downloads the Rio de Janeiro state extract from Geofabrik and
preprocesses it once per profile. You do not need all of Brazil: the state
extract covers Petrópolis, Teresópolis, Friburgo and Miguel Pereira. Visconde
de Mauá sits on the border — set `REGION=minas-gerais` and run it again if you
want that corner too.

## Using it without the agent

The solver is a plain CLI. Nothing about it needs a model.

```bash
python3 -m mate.cli plan \
  --origin "Rio de Janeiro, RJ" --region "Serra Fluminense" \
  --town "Petrópolis, RJ" --town "Teresópolis, RJ" --town "Miguel Pereira, RJ" \
  --start 2026-10-31 --end 2026-11-01 --people 2 --budget 2000 \
  --out plan.json
```

| Command | What it does |
|---|---|
| `plan` | Build a plan from scratch |
| `edit --kind ... --value ...` | Record a correction and re-solve |
| `lock --option <id>` | Fix the chosen option; only it renders afterwards |
| `say --what options\|itinerary\|constraints` | The plan, formatted as messages |
| `show` | The current plan as JSON |
| `render --out plan.html` | A local HTML view, for looking at rather than sending |
| `doctor` | Check every data source and say which is down |

Every command prints one JSON object and exits non-zero when it did not work.
The first cold run over five towns takes a couple of minutes because it queries
OpenStreetMap; it is cached afterwards, so every later change is instant.

```bash
python3 -m unittest discover -s tests
```

## How it works

The model does perception and language. Code does anything with a right answer.
The model reads what you wrote, decides what you meant, and writes the reply —
it never computes a distance, a cost or an ordering. Every number in a plan came
from a function, and the agent checks the exit status before repeating it.

- **State** lives in `plan.json` — the trip, the options, and every constraint
  gathered across the conversation. The solver reads it, re-solves, rewrites it.
  Re-planning on disruption is not a separate feature: an attraction closed, the
  party got smaller, the booking fell through, all of it is the same solver with
  one more constraint.
- **Days are built geographically.** Stops are clustered by proximity to each
  other, not to the base — that is what stops a day zigzagging across a valley.
- **Order is brute-forced.** Six stops is 720 orderings. No solver library, no
  heuristic; at that scale exhaustive search is cheaper than the code you would
  write to avoid it. Meals are fixed stops inside a time window, and waiting
  counts against an ordering as much as driving does.
- **Mode is an output, not a question.** MATE never asks how you will get
  around. It measures how far apart the day's stops are and tells you: stops
  within about 2 km of each other means a day on foot, spread across 20 km means
  wheels.
- **Assumptions are stated, not asked.** MATE cannot know whether you have a
  car, so the plan says "assuming your own car" at the top and one message
  corrects it. A visible, correctable assumption beats a question up front.

## Data sources

Free and open. No API keys.

| Need | Source |
|---|---|
| Points of interest, coordinates | Overpass API (OpenStreetMap) |
| Descriptions, notability | Wikidata / Wikipedia |
| Geocoding | Photon, falling back to Nominatim |
| Routing, travel-time matrices, walking times | OSRM, self-hosted |
| Lodging listings | OpenStreetMap |
| Lodging price for a specific listing | `@openbnb/mcp-server-airbnb` |

MATE performs the search volume one person planning one trip would perform, and
caches aggressively so a re-plan costs nothing.

### On Airbnb and robots.txt

Airbnb's `robots.txt` disallows `/s/*/*` for the wildcard user-agent — the
search path. So `airbnb_search` returns a refusal, and **that is the correct
outcome**. The MCP server offers an `--ignore-robots-txt` override; it is
deliberately absent from this repo's config, the Python client refuses to pass
it, and it should stay that way. Judges install and run this on their own
machines under their own IP, and quietly disabling a site's crawl policy on
someone else's connection is not ours to decide.

`/rooms/<id>` is *not* disallowed, so when you send MATE a listing you found
yourself, it reads that listing's real price and folds it into the plan.

Everything else is genuinely open data.

## What the numbers mean

Every figure carries its provenance, and the plan marks anything soft with `~`:

- **Measured** — a real source answered: OSRM distances, OSM coordinates, a
  listing's actual price.
- **Curated** — a human-maintained table in `data/`, with an `as_of` date.
  Toll prices and nightly rate tiers. Approximate by construction.
- **Estimated** — our formula stood in for a source we could not reach.
  Ride-hail fares, food, and every distance when OSRM is down.

An invented number presented as certainty is what breaks trust at the moment
someone orders the car, so MATE would rather say "about" than be confidently
wrong. **Verify `data/tolls_rj.json` against
[ANTT](https://www.gov.br/antt/pt-br) before relying on it** — no free API
publishes Brazilian toll tariffs, so those prices are maintained by hand.

## Known limits

- **No flights, no multi-city, no international trips.** Car and foot, one
  region, one or two nights. The day model already carries a per-day mode, so
  the shape does not need rewriting to grow.
- **MATE never books or takes payment.** It plans and links out; you book.
- **OSM coverage in small serra towns is uneven.** The main restaurants and
  waterfalls are mapped; the long tail is not. Petrópolis has around 200 named
  stops with a dozen carrying Wikidata entries — Visconde de Mauá has none. In
  the thinner towns the ranking falls back to distance, because OSM offers no
  signal to rank on.
- **Opening hours are nearly absent from OSM**, so MATE cannot schedule around
  them. It schedules meals into meal windows and otherwise assumes places are
  open.
- **Lodging prices are tiers, not quotes**, for the robots.txt reason above.
  Send a listing to replace one with a real price.
