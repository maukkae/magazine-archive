#!/bin/sh
set -eu

mkdir -p /data

if [ ! -f /data/manifest.json ]; then
  echo "Seeding empty archive shell into /data"
  cp -R /seed/. /data/
else
  echo "Existing archive data found in /data, leaving it untouched"
fi
