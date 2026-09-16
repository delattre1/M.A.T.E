#!/usr/bin/env bash
# Build OSRM routing data for the Rio serra, once, for two profiles.
#
# You do not need Brazil. The Rio de Janeiro state extract covers Petrópolis,
# Teresópolis, Friburgo and Miguel Pereira, is small, and preprocesses in
# minutes. Visconde de Mauá sits on the border — add minas-gerais below if you
# want it.
#
# Each profile needs its own preprocessed copy: the .osrm files encode the
# profile's weights, so car and foot cannot share a directory.
set -euo pipefail

REGION="${REGION:-rio-de-janeiro}"
URL="${URL:-https://download.geofabrik.de/south-america/brazil/${REGION}-latest.osm.pbf}"
IMAGE="${IMAGE:-ghcr.io/project-osrm/osrm-backend:latest}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PBF="${REGION}-latest.osm.pbf"

mkdir -p "$HERE/data"
if [ ! -f "$HERE/data/$PBF" ]; then
  echo "==> downloading $URL"
  curl -fL --progress-bar -o "$HERE/data/$PBF" "$URL"
else
  echo "==> reusing $HERE/data/$PBF"
fi

for profile in car foot; do
  dir="$HERE/data/$profile"
  if [ -f "$dir/${REGION}-latest.osrm.fileIndex" ]; then
    echo "==> $profile already built, skipping"
    continue
  fi
  echo "==> building $profile"
  mkdir -p "$dir"
  cp "$HERE/data/$PBF" "$dir/$PBF"
  docker run --rm -t -v "$dir:/data" "$IMAGE" \
    osrm-extract -p "/opt/${profile}.lua" "/data/$PBF"
  docker run --rm -t -v "$dir:/data" "$IMAGE" \
    osrm-partition "/data/${REGION}-latest.osrm"
  docker run --rm -t -v "$dir:/data" "$IMAGE" \
    osrm-customize "/data/${REGION}-latest.osrm"
  rm -f "$dir/$PBF"
done

echo
echo "Done. Start the routers with:"
echo "  REGION=$REGION docker compose -f compose.osrm.yml up -d"
