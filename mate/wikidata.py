"""Descriptions for the stops that carry a wikidata tag.

This is enrichment, not data the plan depends on, so every failure path here
ends in "return the stops unchanged plus a note the caller logs as degraded".
The one thing it must not do is take the trip down with it.
"""

from __future__ import annotations

import re
from dataclasses import replace

from .cache import Cache, cache_key
from .http import SourceError, get_json
from .schema import Stop
from .settings import Settings

BATCH = 50                       # wbgetentities hard limit on ids per call
LANGS = ("pt-br", "pt", "en")
WIKIS = ("ptwiki", "enwiki")
QID_IN_ERROR = re.compile(r"\bQ\d+\b")


def _fetch_batch(qids: list[str], settings: Settings) -> dict[str, dict[str, str]]:
    data = get_json(
        settings.wikidata_url,
        {
            "action": "wbgetentities",
            "ids": "|".join(qids),
            "props": "descriptions|sitelinks/urls",
            "languages": "|".join(LANGS),
            "sitefilter": "|".join(WIKIS),
            "format": "json",
            "formatversion": "2",
        },
        settings,
        rate_limit_key="wikidata",
    )
    if "error" in data:
        raise SourceError("wikidata", data["error"].get("info", "api error"))
    out = {}
    for qid, ent in (data.get("entities") or {}).items():
        descriptions = ent.get("descriptions") or {}
        sitelinks = ent.get("sitelinks") or {}
        out[qid] = {
            "desc": next(
                (descriptions[lang]["value"] for lang in LANGS if lang in descriptions), ""
            ),
            "url": next((sitelinks[w]["url"] for w in WIKIS if w in sitelinks), ""),
        }
    return out


def _fetch_batch_dropping_dead_ids(
    qids: list[str], settings: Settings
) -> dict[str, dict[str, str]]:
    """OSM wikidata tags go stale, and wbgetentities rejects the whole call over
    one deleted QID. Drop the id the API names and ask again, so a single bad tag
    cannot cost 49 good stops their description."""
    ids = list(qids)
    for _ in range(3):
        try:
            return _fetch_batch(ids, settings)
        except SourceError as e:
            dead = {q for q in QID_IN_ERROR.findall(e.message) if q in ids}
            if not dead:
                raise
            ids = [q for q in ids if q not in dead]
            if not ids:
                return {}
    raise SourceError("wikidata", f"gave up after repeated bad ids in {qids[0]}...")


def enrich(stops: list[Stop], settings: Settings) -> tuple[list[Stop], str]:
    """Returns (stops, note); the note is empty only when nothing was degraded."""
    cache = Cache(settings)
    wanted = sorted({s.wikidata_id for s in stops if s.wikidata_id})
    if not wanted:
        return stops, ""

    known: dict[str, dict[str, str]] = {}
    pending: list[str] = []
    for qid in wanted:
        hit = cache.get(cache_key("wikidata", qid))
        if hit is None:
            pending.append(qid)
        else:
            known[qid] = hit

    batches = [pending[i:i + BATCH] for i in range(0, len(pending), BATCH)]
    failures = []
    for batch in batches:
        try:
            fetched = _fetch_batch_dropping_dead_ids(batch, settings)
        except SourceError as e:
            failures.append(str(e))
            continue
        for qid in batch:
            entry = fetched.get(qid, {"desc": "", "url": ""})
            cache.put(cache_key("wikidata", qid), entry)
            known[qid] = entry

    for s in stops:
        entry = known.get(s.wikidata_id)
        if not entry:
            continue
        if entry["desc"] and not s.description:
            s.description = entry["desc"]
            s.provenance = replace(
                s.provenance, detail=f"{s.provenance.detail}; description from Wikidata"
            )
        if entry["url"] and not s.url:
            s.url = entry["url"]

    if failures:
        unresolved = sum(1 for q in wanted if q not in known)
        return stops, (
            f"wikidata enrichment degraded: {len(failures)} of {len(batches)} batches "
            f"failed ({failures[0]}); {unresolved} stops kept their OSM description"
        )
    return stops, ""
