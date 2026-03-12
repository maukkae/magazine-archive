#!/bin/sh
set -eu

mkdir -p /data

if [ ! -f /data/manifest.json ]; then
  echo "Seeding demo archive into /data"
  cp -R /seed/. /data/
else
  echo "Existing archive data found in /data, leaving it untouched"
fi
