---
name: mate-doctor
description: Use when MATE's planning commands fail, when the user reports that a plan link is broken or empty, when distances or times look obviously wrong, or when someone installing MATE asks why it is not working. Checks every data source and reports which one is down.
version: 1.0.0
allowed-tools: Bash(/opt/plow/mate/bin/mate:*)
---

# MATE: check the plumbing

## When to Use

A plan command failed, the numbers look wrong, or someone is setting MATE up
and it is not behaving.

## Run

```
/opt/plow/mate/bin/mate doctor
```

It reports one line per source. Exit 0 means MATE can plan; exit 1 means it
cannot.

## Reading the result

| Check | If it is down |
|---|---|
| `osrm:car`, `osrm:foot` | Plans still work, but every distance and time becomes a straight-line estimate and the page says so. Fix by starting the OSRM containers — see the README. Not fatal. |
| `photon` | Fatal. Without geocoding MATE cannot place the origin or the region. Check egress from the container. |
| `mcp:airbnb` | Only affects pinning a real listing. `airbnb_search` being refused is **not** a fault — Airbnb's robots.txt disallows the search path and MATE respects it. As long as `airbnb_listing_details` is listed, this check is healthy. |

## What to say

Name the broken thing and its consequence in one sentence, then the fix. Do not
paste the raw JSON at someone over SMS.

> O roteador de rotas tá fora do ar, então as distâncias saíram estimadas.
> O plano funciona, só não está medido. Subindo o OSRM resolve.

If everything passes but the plan still looks wrong, the likely causes, in
order: the region geocoded to the wrong place, OSM has thin coverage for that
town (real in small serra towns — the main sights are mapped, the long tail is
not), or a constraint from earlier in the conversation is still binding. Check
the constraint list on the page before assuming a bug.
