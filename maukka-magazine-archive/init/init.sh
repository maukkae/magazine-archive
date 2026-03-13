#!/bin/sh
set -eu

mkdir -p /data /data/site

if [ ! -f /data/site/manifest.json ]; then
  echo "Seeding empty archive shell into /data/site"
  cp -Rn /seed/site/. /data/site/
else
  echo "Existing archive data found in /data/site, leaving it untouched"
fi
