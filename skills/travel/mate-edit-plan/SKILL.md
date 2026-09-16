---
name: mate-edit-plan
description: Use whenever a trip plan already exists and ANY detail of it changes — different dates or one more night ("de 31/10 a 02/11", "mais um dia"), a different number of people ("agora somos 4"), a different budget, "nada de museu", "no máximo uma hora de estrada", an attraction closed, the pousada fell through, arriving late, swapping a stop, or choosing which option they want. Always prefer this over rebuilding a plan from scratch: it keeps every correction made so far. Only a different destination justifies a new plan.
version: 1.0.0
allowed-tools: Bash(/opt/plow/mate/bin/mate:*)
---

# MATE: change the plan

A plan only survives if it re-solves. Party size changes, an attraction closes,
a booking falls through — the plan is recomputed, never rewritten by hand.

## When to Use

There is already a plan file for this conversation and the user says something
that changes it, or picks one of the options.

## Turn the sentence into a constraint

This is your half of the work: read what they meant, choose the right `kind`,
and let the solver do the rest. Constraints accumulate — every past correction
still applies after the next one.

| They said | kind | value |
|---|---|---|
| "agora somos 2" | `party_size` | `2` |
| "o teto subiu pra 2500" | `budget` | `2500` |
| "no máximo 1h de estrada" | `max_drive_min` | `60` |
| "nada de museu" / "sem cachoeira" | `exclude` | `museu` |
| "quero ir no Museu Imperial" | `include` | `Museu Imperial` |
| "mais tranquilo" / "mais puxado" | `pace` | `relaxed` / `packed` |
| "não temos carro" | `has_car` | `false` |
| "algo mais barato" / "pode ser mais caro" | `tier` | `low` / `high` |
| "mudou pra 7 e 8 de novembro" | `dates` | `2026-11-07,2026-11-08` |

```
/opt/plow/mate/bin/mate edit --plan /var/lib/hermes/plans/<id>.json \
  --kind party_size --value 2 --text "agora são 2 pessoas"
```

Put their actual words in `--text`. The page shows the constraint list so a
group can see what has been agreed, and their phrasing reads better than yours.

If one message carries two changes, run the command twice. Each correction is
its own constraint.

## Disruption is the same command

"O museu fechou", "vamos chegar tarde", "a pousada cancelou" — these are not a
different feature. They are `exclude`, a later start, a re-solve. Same solver,
one constraint different.

## Locking in

When they choose an option, lock it. From then on only that plan renders and
the page stops offering alternatives.

```
/opt/plow/mate/bin/mate lock --plan /var/lib/hermes/plans/<id>.json --option opt-node-123456
```

Take the id from `options[].id` in the previous output — never invent one. If
the id is wrong the command exits 2 and lists the valid ones. After locking,
`messages[]` becomes the full itinerary for that option instead of the
three-way comparison.

## Reporting back

Check `ok` first. On failure, say what broke and do not describe a plan.

On success, do not dump the whole itinerary again. Say in one line what moved
and what it cost, taking the figures from the JSON:

> Refeito pra 2 pessoas: total caiu pra R$ 1.240 e o segundo dia agora cabe a pé.

Send the full `messages[]` again only when they locked in an option, changed
dates or destination, or actually asked to see the whole thing. Otherwise the
one-line consequence is the better message.

To re-send the plan at any point without recomputing:

```
/opt/plow/mate/bin/mate say --plan /var/lib/hermes/plans/<id>.json --what itinerary
```

`--what options` re-sends the comparison, `--what constraints` lists what has
been agreed so far — useful when a group has lost track of who asked for what.

If a constraint made things impossible, the command fails with a reason — tell
them which constraint is the binding one and offer to relax it. Do not quietly
drop a constraint to make a plan fit; that is how a plan stops being theirs.

Same rules as always: every number comes from the JSON, and estimates stay
labelled as estimates.
