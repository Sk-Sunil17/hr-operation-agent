#!/usr/bin/env sh
#
# Boot the API, seeding the demo the first time only.
#
# Re-seeding on every boot would be wrong: if a disk is mounted, somebody's
# demo state survives a restart and should not be wiped by a platform deciding
# to recycle the instance. So the seed is guarded by a marker file next to the
# database, and `hrctl init` is run every time because it is idempotent and
# picks up any pack changes shipped in a new image.

set -eu

VAR_DIR="${HR_VAR_DIR:-/data}"
MARKER="$VAR_DIR/.seeded"
PORT="${PORT:-4000}"

mkdir -p "$VAR_DIR"

echo "loading company packs..."
python -m hr_agent.cli init

if [ ! -f "$MARKER" ]; then
  echo "first boot: seeding a morning of traffic for each company..."
  for tenant in acme_labs meridian_group harbour_clinic; do
    python -m hr_agent.cli demo "$tenant" --reset
  done
  date -u +"%Y-%m-%dT%H:%M:%SZ" > "$MARKER"
else
  echo "database already seeded on $(cat "$MARKER"); leaving it alone."
  echo "to re-seed, delete $MARKER and restart."
fi

# Bind every interface: inside a container there is nothing else to bind. The
# server still refuses this unless HR_PUBLIC_DEMO carries the exact
# acknowledgement, or authentication is on -- see hr_agent/config.py.
echo "serving on 0.0.0.0:$PORT"
exec python -c "
from hr_agent import web
web.serve(host='0.0.0.0', port=$PORT)
"
