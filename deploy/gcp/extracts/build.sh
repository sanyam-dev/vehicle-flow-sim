#!/usr/bin/env bash
# Runs inside Cloud Build (see cloudbuild.yaml): for each Geofabrik region,
# keep only ways tagged `highway` (plus their nodes, which keeps traffic-signal
# tags) and upload `<id>.osm.pbf`, `<id>.poly` and `<id>.date` to gs://$BUCKET.
#
# Env:
#   BUCKET   target bucket name (no gs://)
#   REGIONS  space-separated Geofabrik paths, e.g. "asia/india asia/india/southern-zone"
#            ids drop the continent: asia/india/southern-zone -> india-southern-zone
set -euo pipefail
: "${BUCKET:?set BUCKET}" "${REGIONS:?set REGIONS}"

GEOFABRIK=https://download.geofabrik.de
apt-get update -qq
apt-get install -y -qq --no-install-recommends osmium-tool curl >/dev/null
osmium --version | head -1

mkdir -p /workspace/extracts && cd /workspace/extracts
for region in $REGIONS; do
  id="$(echo "$region" | cut -d/ -f2- | tr / -)"
  echo "== $region -> $id"
  curl -fsSL --retry 3 -o raw.osm.pbf "$GEOFABRIK/${region}-latest.osm.pbf"
  curl -fsSL --retry 3 -o "$id.poly" "$GEOFABRIK/${region}.poly"
  osmium tags-filter raw.osm.pbf w/highway -o "$id.osm.pbf" --overwrite
  osmium fileinfo -g header.option.osmosis_replication_timestamp raw.osm.pbf > "$id.date"
  ls -l raw.osm.pbf "$id.osm.pbf"
  gcloud storage cp "$id.osm.pbf" "$id.poly" "$id.date" "gs://$BUCKET/"
  rm -f raw.osm.pbf "$id.osm.pbf"
done
gcloud storage ls -l "gs://$BUCKET/"
