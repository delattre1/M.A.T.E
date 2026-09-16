---
name: mate-show-plan
description: Use whenever someone asks to see, resend or confirm a trip plan that already exists — "me dá o roteiro completo", "manda de novo", "qual ficou o plano", "me manda atualizado", "quanto vai custar mesmo?", "quais são as opções?", "o que a gente combinou até agora?", or any request to repeat part of the itinerary. Reads the current plan from disk instead of retyping it from earlier in the conversation.
version: 1.0.0
allowed-tools: Bash(/opt/plow/mate/bin/mate:*)
---

# MATE: show the plan as it stands now

## When to Use

They want to look at the plan again. Nothing is changing — if something is
changing, that is `mate-edit-plan`.

## Always read it back, never recite it

The plan is a file, and it moves. It changed the last time anyone edited it,
and a conversation can be resumed days later or from a second device.

So **run the command**. Do not rebuild the itinerary from a tool result earlier
in this conversation, however recent it looks. A plan that changed since then
reads exactly like one that did not, and from inside the conversation you
cannot tell the difference — which is how a confident, out-of-date itinerary
reaches somebody who is about to drive somewhere.

This matters most when they say "atualizado", "agora" or "como ficou": those
words are a request for current state, not a request for a nicer summary of
what you already said.

```
/opt/plow/mate/bin/mate say --plan /var/lib/hermes/plans/<id>.json --what itinerary
```

| They asked for | `--what` |
|---|---|
| the itinerary, the days, the full plan, the cost | `itinerary` |
| the choices, "quais as opções", a comparison | `options` |
| "o que a gente já combinou", the adjustments so far | `constraints` |

Send the strings in `messages[]`, in order. They are already written and
formatted with the figures in place.

If the command exits non-zero, say that you could not read the plan and why.
Do not fall back to describing it from memory — that is the failure this skill
exists to prevent.

## If there is no plan yet

`say` fails when the file does not exist. That means nobody has planned a trip
in this conversation yet, so switch to `mate-plan-trip` and ask the four
questions.
