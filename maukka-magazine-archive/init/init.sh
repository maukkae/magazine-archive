#!/bin/sh
set -eu

mkdir -p /data /data/site

STATIC_FILES="
index.html
carousel.html
mobile.html
mobile_viewer.html
viewer.html
search.html
lang.js
app.webmanifest
"

echo "Refreshing static shell files in /data/site"
for name in $STATIC_FILES; do
  if [ -f "/seed/site/$name" ]; then
    cp "/seed/site/$name" "/data/site/$name"
  fi
done

if [ ! -f /data/site/manifest.json ]; then
  echo "Seeding initial archive metadata into /data/site"
  cp -n /seed/site/manifest.json /data/site/manifest.json
  cp -n /seed/site/search_index.json /data/site/search_index.json
  cp -n /seed/site/search.db /data/site/search.db
  mkdir -p /data/site/jpg
else
  echo "Existing archive data found in /data/site, preserving it"
fi
