#!/usr/bin/env bash
# Make the image's payload reachable from where its consumers look, on EVERY
# boot.
#
# The home is a named volume, and a volume seeds from the image only while it
# is EMPTY (plow-hermes-agent#58). /etc/cont-init.d/00-plow-sanitize seeds
# /var/lib/hermes/config.yaml from the base's seed only if it is absent. So
# after the very first `docker compose up`, nothing in the image reaches the
# home again — a rebuilt image with a corrected config.yaml would boot the old
# one, silently, forever. Reinstalling here is what makes an image update
# reach a home that already exists.
#
# HERMES_HOME is defaulted, not required: a `#!/usr/bin/env bash` cont-init
# script gets s6's own environment rather than the container's, so the variable
# the base image sets with ENV is not necessarily in scope here. Demanding it
# is a known way to make a cont-init fail on every boot.
#
# Ownership is the other half. config.yaml is the AGENT's file: plow-init
# rewrites its own keys in it after this script runs, as hermes rather than as
# root, and $HERMES_HOME carries the sticky bit — so a root-owned config.yaml
# is one the agent cannot replace, and the boot parks on
# `PermissionError: os.replace('config.yaml.tmp' -> 'config.yaml')` AFTER
# cont-init has already reported success. The base ships it 0640 agent-owned;
# match that. This is str-hermes-agent's install idiom, kept verbatim for that
# reason.
#
# SOUL.md is deliberately absent from this script: plow-init writes the home's
# copy on every boot as the base persona followed by
# /opt/hermes/plow-seed/persona.md, so reinstalling one here would make this a
# second owner of a file it holds only half of.
set -euo pipefail

home="${HERMES_HOME:-/var/lib/hermes}"
payload=/opt/plow/mate
src="$payload/home/config.yaml"

die() {
  echo "05-install-mate-payload: $*" >&2
  echo "05-install-mate-payload: refusing to boot a half-configured agent." >&2
  echo "05-install-mate-payload: rebuild the image (\`docker compose build\`);" >&2
  echo "05-install-mate-payload: if that does not fix it, the payload did not" >&2
  echo "05-install-mate-payload: survive the build and the Dockerfile is where" >&2
  echo "05-install-mate-payload: to look." >&2
  exit 1
}

# Everything this script is about to rely on, checked before it changes
# anything. A partial payload is the case worth catching: the agent would come
# up, answer, and be wrong about tolls or have no lodging search, with nothing
# in the log saying why. S6_BEHAVIOUR_IF_STAGE2_FAILS=2 in the Dockerfile is
# what turns each `die` below into a stopped container rather than one warning
# nobody reads.
[ -d "$home" ] || die "no agent home at $home"
[ -r "$src" ] || die "the image's config.yaml is missing at $src"
[ -s "$src" ] || die "the image's config.yaml at $src is empty"
# The flat layout bin/mate and the three parent.parent lookups both depend on:
# mate/, data/ and templates/ as siblings under $payload, with bin/mate at the
# absolute path every SKILL.md hard-codes.
[ -r "$payload/mate/cli.py" ] \
  || die "the mate package is missing at $payload/mate"
[ -r "$payload/data/tolls_rj.json" ] \
  || die "the curated toll table is missing at $payload/data/tolls_rj.json"
[ -r "$payload/templates/plan_template.html" ] \
  || die "the plan template is missing at $payload/templates"
[ -x "$payload/bin/mate" ] \
  || die "$payload/bin/mate is missing or not executable; every skill invokes it by that path"
[ -r "$payload/airbnb-mcp/node_modules/@openbnb/mcp-server-airbnb/dist/index.js" ] \
  || die "the airbnb MCP server config.yaml names is not in this image"

id -u hermes >/dev/null 2>&1 || die "no hermes user in this image"
uid="$(id -u hermes)"
gid="$(id -g hermes)"

# Unconditional: overwriting the stale copy a volume kept is the entire point.
install -o "$uid" -g "$gid" -m 0640 -t "$home" "$src" \
  || die "could not install config.yaml into $home"

# What was actually installed, not what install was asked to do. `install`
# copies in place rather than renaming, so a full disk or a truncated write
# leaves a short file and a zero exit is not proof on its own.
dst="$home/config.yaml"
[ -s "$dst" ] || die "config.yaml landed empty at $dst"
cmp -s "$src" "$dst" || die "config.yaml at $dst does not match the image's copy"
grep -q '^mcp_servers:' "$dst" \
  || die "config.yaml at $dst has no mcp_servers block; it is not MATE's config"

# The plan cache. Settings.cache_dir is overridden to this path by the image
# (MATE_CACHE_DIR) so the cache lands somewhere the agent owns and the volume
# keeps, rather than in a `.cache` relative to whatever directory a skill was
# invoked from. Created here because the volume may be empty on first boot, and
# agent-owned because the agent is what writes it.
install -d -o "$uid" -g "$gid" -m 0700 "$home/.cache" "$home/.cache/mate" \
  || die "could not create the plan cache under $home/.cache"

# Where the plans themselves land. The travel skills read and write
# /var/lib/hermes/plans/<id>.json by that absolute path, and on a first boot the
# volume is empty, so something has to create it — as the agent, because the
# agent is what writes into it. 0755 rather than the cache's 0700: a plan is a
# document the owner may want to copy out of the container.
install -d -o "$uid" -g "$gid" -m 0755 "$home/plans" \
  || die "could not create the plan directory at $home/plans"

# Not fatal, and deliberately so: lodging.py reads this table but the rest of a
# plan does not, and a hackathon image that refuses to boot over one optional
# data file is worse than one that says what is missing and comes up.
[ -r "$payload/data/lodging_tiers_rj.json" ] || {
  echo "05-install-mate-payload: note — data/lodging_tiers_rj.json is not in" >&2
  echo "05-install-mate-payload: this image, so mate/lodging.py's tier lookup" >&2
  echo "05-install-mate-payload: will fail when it is reached. Everything else" >&2
  echo "05-install-mate-payload: works. Add the file and rebuild." >&2
}

echo "05-install-mate-payload: config.yaml installed to $home; cache and plans ready."
