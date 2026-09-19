# MATE — "Make A Trip Easy", a trip-planner variant of the Plow cloud agent.
#
# No runtime of its own: the persona, skills, Python payload and config.yaml
# copied below are the tracked files this repo owns. Context is the repo root,
# so those copies are the product content: `docker build .`
#
# The tag is an immutable `base-<sha>` naming one commit of the base's source
# repo, plow-pbc/plow-hermes-agent, AND its index digest. The registry's tags
# are mutable, so the tag names the commit for a reader and the digest is what
# docker actually resolves. It is never moved: every tenant inherits this exact
# filesystem while holding that owner's Plow credential, so a moving tag would
# substitute code underneath them.
#
# Taken verbatim from plow-pbc/life-assistant-hermes-agent's Dockerfile at
# commit 95c55cff (2026-09-16T14:09Z, "Take base-c96adf1"), which is the most
# recently bumped of the variant repos — str-hermes-agent is still on
# base-80ef502, the pin life-assistant carried earlier the same day.
# To bump: `docker buildx imagetools inspect <tag>`, take the top `Digest:`
# line (the INDEX digest; a per-platform manifest digest does not resolve as a
# FROM).
FROM public.ecr.aws/e1h7x4a2/plow-cloud-agents:base-ef0019372ff8bca593611b31ebd2e08f9f1458ff@sha256:a8a2f97ad78b8192d80a984dce81d3bf5a9a883d18cb7b677704913a09b56aee

# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------
# Only what is specific to this agent. plow-init writes the home's SOUL.md on
# every boot as the base persona followed by this file, so nothing here targets
# /var/lib/hermes/SOUL.md — that path is overwritten at boot, and a copy of it
# in this image would be a second source for a file this image owns only half
# of. `docker run <tag> cat /opt/hermes/plow-seed/persona.md` is what shows an
# operator the persona a published image carries.
#
# Plain COPY then chmod, not `COPY --chmod`: that option requires BuildKit, and
# on a stock builder the build dies here.
#
# At the repo root, not runtime/persona.md: the sibling variants keep theirs
# under runtime/, but this repo's is tracked at the top level and a COPY has to
# name where the file actually is.
COPY persona.md /opt/hermes/plow-seed/persona.md
RUN chown 0:0 /opt/hermes/plow-seed/persona.md \
 && chmod 0644 /opt/hermes/plow-seed/persona.md
# No `COPY LICENSE NOTICE /usr/share/doc/mate/` yet, the way the sibling
# variants carry one: neither file is tracked here, and a COPY naming a missing
# path fails the build. Add both lines together if the repo grows them.

# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
# Shipped under /opt/hermes/skills, outside every home, so a bind-mounted or
# pre-populated home still receives them: tools/skills_sync.py rglobs that root
# for SKILL.md and reconciles what it finds into $HERMES_HOME/skills on every
# boot, preserving the path it found them at. The copy under /var/lib/hermes is
# what an EMPTY named volume initialises from and what
# `docker run <tag> ls /var/lib/hermes/skills/travel` shows an operator checking
# a published image; it is derived from the same source, not a second one.
#
# The category path is preserved, not invented: this repo ships skills/travel/,
# and skills_sync keys a skill's destination on where it found it, so the agent
# knows them as travel/mate-plan-trip and a future category arrives at its own
# name with no edit here.
#
# Staged first rather than COPY'd straight onto the two roots, because after the
# copy the two trees are indistinguishable from the base's own bundled skills
# and there is nothing left to enumerate. The staging copy is what says which
# categories THIS repo owns, so the chown below reaches all of them and only
# them — a `chown -R` on either root would take the base's skills with it, and
# on /opt/hermes/skills would also reset the root's mode and leave it
# unwritable for the gateway's own bundled-skill install, which then scans
# nothing (life-assistant-hermes-agent's Dockerfile documents that failure).
COPY skills/ /opt/plow/mate/skills-staging/

# Modes are normalized with the executable bit preserved: a SKILL.md that
# invokes a script by bare path fails with Permission denied under a blanket
# 0644, and a checkout on a filesystem without exec bits loses the other half.
# Ownership goes to uid/gid 10000 (hermes), which is what the base does to
# everything under /var/lib/hermes.
RUN set -eu; \
    cats="$(cd /opt/plow/mate/skills-staging && find . -mindepth 1 -maxdepth 1 -type d -printf '%f ')"; \
    [ -n "$cats" ] || { echo "skills/ shipped no category directories" >&2; exit 1; }; \
    for root in /opt/hermes/skills /var/lib/hermes/skills; do \
      cp -R /opt/plow/mate/skills-staging/. "$root"/; \
      for cat in $cats; do \
        find "$root/$cat" -type d -exec chmod 0755 {} + ; \
        find "$root/$cat" -type f ! -perm -u+x -exec chmod 0644 {} + ; \
        find "$root/$cat" -type f -perm -u+x -exec chmod 0755 {} + ; \
        chown -R 10000:10000 "$root/$cat"; \
      done; \
    done; \
    find /opt/hermes/skills -name SKILL.md -path '*mate-*' -print | grep -q . \
      || { echo "no mate-* SKILL.md reached /opt/hermes/skills" >&2; exit 1; }; \
    rm -rf /opt/plow/mate/skills-staging

# ---------------------------------------------------------------------------
# The Python payload
# ---------------------------------------------------------------------------
# mate/ is stdlib-only: no venv, no pip, no requirements step. What matters is
# the relative layout, and TWO separate things fix it — this is not a free
# choice of directory:
#
#   1. Three modules resolve a sibling of the package by path —
#      tolls.py    parent.parent / "data" / "tolls_rj.json"
#      lodging.py  parent.parent / "data" / "lodging_tiers_rj.json"
#      plan_state.py  parent.parent / "templates" / "plan_template.html"
#      so `data/` and `templates/` have to sit BESIDE `mate/`, under one shared
#      parent.
#
#   2. bin/mate, which every SKILL.md invokes by the absolute path
#      /opt/plow/mate/bin/mate, defaults MATE_HOME to /opt/plow/mate and then
#      tests for "$MATE_HOME/mate". That names the shared parent from (1), and
#      it names it as exactly /opt/plow/mate.
#
# So the tree is flat under /opt/plow/mate and an extra level would break both:
# the package would not be importable and every skill's Bash line would fail.
#
# Root-owned under /opt/plow, the pattern life-assistant-hermes-agent and
# str-hermes-agent both use and for the same reason: everything under
# $HERMES_HOME belongs to uid 10000 in a running container, so code the agent
# imports from there is code one prompt-injected turn can rewrite. This the
# agent can read and cannot change.
COPY mate/ /opt/plow/mate/mate/
COPY data/ /opt/plow/mate/data/
COPY templates/ /opt/plow/mate/templates/
COPY bin/ /opt/plow/mate/bin/

# The declarative half of this deployment's home. COPY'd rather than mounted: a
# published image has no deploy clone to mount from, and it is what a
# named-volume home initialises from.
#
# The authoritative copy lives OUTSIDE the home, because the home is a volume
# and a volume seeds from the image only while it is EMPTY — so a home that
# already exists shadows every later revision of it (plow-hermes-agent#58).
# 05-install-mate-payload.sh reinstalls it from here on every boot, which is
# what makes an image update reach a pre-populated home at all.
COPY runtime/config.yaml /opt/plow/mate/home/config.yaml

# bin/mate is tracked 0644, so the executable bit cannot travel on the COPY and
# has to be set here — the skills invoke it as a command, and 0644 is
# Permission denied. The blanket 0644 over /opt/plow runs FIRST for that
# reason; the bin chmod after it is what survives.
#
# The `test`s are the build-time proof that the two path contracts above hold.
# A layout mistake fails the build here rather than at the first tool call, on
# a judge's machine, as a Python traceback in a chat window.
RUN set -eu; \
    chown -R root:root /opt/plow; \
    find /opt/plow -type d -exec chmod 0755 {} + ; \
    find /opt/plow -type f -exec chmod 0644 {} + ; \
    find /opt/plow/mate/bin -type f -exec chmod 0755 {} + ; \
    test -f /opt/plow/mate/mate/tolls.py; \
    test -f /opt/plow/mate/mate/cli.py; \
    test -f /opt/plow/mate/data/tolls_rj.json; \
    test -f /opt/plow/mate/templates/plan_template.html; \
    test -x /opt/plow/mate/bin/mate; \
    python3 -c "import sys; sys.path.insert(0, '/opt/plow/mate'); import mate.cli"; \
    install -m 0644 -t /var/lib/hermes/ /opt/plow/mate/home/config.yaml

# MATE_HOME is the variable bin/mate already reads; stating it makes the
# container's layout explicit rather than leaving it to that script's fallback,
# which walks up from $0 and would silently pick a different tree if the
# payload ever moved. PYTHONPATH is belt and braces for anything that runs the
# package without going through bin/mate — the base sets no PYTHONPATH (checked
# against plow-hermes-agent's Dockerfile), and this exposes exactly one
# top-level name, `mate`.
#
# Neither is read by Settings.from_env: that reads MATE_<FIELD> only for the
# fields Settings actually declares, and it declares no `home` and no `pythonpath`.
ENV MATE_HOME=/opt/plow/mate \
    PYTHONPATH=/opt/plow/mate

# Settings.cache_dir defaults to the RELATIVE ".cache", so where the cache
# lands depends on whatever directory a skill happened to be invoked from — and
# /opt/plow is root-owned, so a run rooted there fails to write and every plan
# re-fetches from the open-data servers. Pointing it at the home puts it
# somewhere the agent owns and somewhere the named volume keeps across
# `up`/`down`. The cont-init creates it, because the volume may be empty.
ENV MATE_CACHE_DIR=/var/lib/hermes/.cache/mate

# ---------------------------------------------------------------------------
# The Airbnb MCP server
# ---------------------------------------------------------------------------
# Installed at BUILD time rather than fetched by `npx -y` at runtime. Both work;
# this one is the safer of the two for the way this image is actually consumed.
# Hermes launches a stdio MCP server when the gateway starts, not on the first
# tool call, and gates that launch on connect_timeout (60s by default). `npx -y`
# inside that window means resolving and downloading the package plus its four
# dependencies — @modelcontextprotocol/sdk, cheerio, node-fetch, robots-parser —
# before the handshake, on whatever connection the machine has. On a slow one
# that is a server which simply is not there, with the failure surfacing as an
# absent tool rather than an error anyone reads. Here the fetch happens once,
# during a build that already needs egress, at a version anyone can read off
# this line.
#
# --prefix with an explicit directory so npm neither walks up to a package.json
# in the build's working directory nor writes into a global prefix this image
# does not own. The `test` is the build-time proof that the path config.yaml
# names actually exists; `bin` in the package manifest maps the
# `mcp-server-airbnb` command to exactly this file.
ARG AIRBNB_MCP_VERSION=0.3.0
RUN set -eu; \
    npm install --no-fund --no-audit --omit=dev \
      --prefix /opt/plow/mate/airbnb-mcp \
      "@openbnb/mcp-server-airbnb@${AIRBNB_MCP_VERSION}"; \
    test -x /opt/plow/mate/airbnb-mcp/node_modules/@openbnb/mcp-server-airbnb/dist/index.js; \
    npm cache clean --force >/dev/null 2>&1 || true; \
    chown -R root:root /opt/plow/mate/airbnb-mcp

# ---------------------------------------------------------------------------
# Agent Index reporting
# ---------------------------------------------------------------------------
# The reporter is the base image's own `agent-index` service. It stands down
# when AGENT_ID is unset, so the image names this entry: a Plow cloud deploy
# does not run compose.yml. Set-but-empty in the container still wins and
# stays the opt-out.
ENV AGENT_ID=mate

# ---------------------------------------------------------------------------
# The image-to-home seam
# ---------------------------------------------------------------------------
# One script, run on every boot, that reinstalls what a pre-populated home
# would otherwise shadow. See the script for why each piece is in it.
COPY docker/cont-init.d/05-install-mate-payload.sh /etc/cont-init.d/05-install-mate-payload.sh
RUN chmod 0755 /etc/cont-init.d/05-install-mate-payload.sh

# What makes the script above a refusal rather than a note.
#
# The base ships 1, at which a cont-init script exiting non-zero prints one
# warning and the boot carries on and starts the gateway anyway — with whatever
# config.yaml the volume already held, which for this agent means an assistant
# with no Airbnb server and no explanation of where it went. At 2, rc.init stops
# the container and the failure is the first thing in `docker logs`.
#
# The base sets 1 deliberately and says why: on exe.dev, Plow's microVM runner,
# /init exiting is `Attempted to kill init` — a panicked kernel pinning a vCPU
# with no sshd, which is worse than a half-configured boot. That reasoning is
# sound and it is about a runner this repo does not target: MATE is distributed
# as this GitHub repo and built and run locally under `docker compose`, where a
# stopped container is exactly the loud park we want. str-hermes-agent, the
# other compose-deployed variant, makes the same call for the same reason;
# life-assistant-hermes-agent, which Plow does run in the cloud, leaves the
# base's 1 alone.
#
# If MATE is ever handed to Plow to run as a cloud agent, DELETE THIS LINE.
ENV S6_BEHAVIOUR_IF_STAGE2_FAILS=2
