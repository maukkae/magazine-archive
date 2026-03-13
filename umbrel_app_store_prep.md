# Umbrel App Store Prep

This file is a local preparation note for eventually packaging the magazine archive as an Umbrel community app and publishing it from a custom GitHub app-store repository.

## Recommended naming

### Community app store

- Store ID: `maukka`
- Store name: `Maukka Apps`

Reason:
- short
- stable
- safe prefix for future apps
- fits Umbrel's requirement that app IDs begin with the store ID

### First app

- App ID: `maukka-magazine-archive`
- App name: `Magazine Archive`

Alternative app names:
- `Pelit Archive`
- `Magazine Viewer`
- `Retro Magazine Archive`

`Magazine Archive` is the safest umbrella name because the project already contains more than just `Pelit`.

## Files needed in the future app-store repo

### Store-level

- `umbrel-app-store.yml`

### App-level

Inside a folder named after the app ID:

- `maukka-magazine-archive/umbrel-app.yml`
- `maukka-magazine-archive/docker-compose.yml`
- `maukka-magazine-archive/exports.sh` (optional)
- `maukka-magazine-archive/icon.svg` or `icon.png`
- gallery images like:
  - `1.png`
  - `2.png`
  - `3.png`

## Recommended architecture for the Umbrel app version

Do not depend on:
- host Python venv
- host `systemd`
- host-level search service

Instead package everything as containers:

### Required containers

- `viewer`
  - nginx or another static web server
  - serves the frontend and static assets
- `search`
  - Python container running `search_server.py`
  - serves `/api/search`

### Optional container

- `extractor`
  - only if you want extraction/import tools inside the Umbrel app
  - can be omitted if Umbrel is only used as a viewing host

## Recommended app behavior

### Public vs protected paths

Keep public:
- `/`
- `/index.html`
- `/carousel.html`
- `/mobile.html`
- `/search.html`
- `/manifest.json`
- `/api/*`
- cover images
- yearly collages

Protected:
- full viewer pages if you still want auth
- admin endpoints if the admin panel is ever included

If you later use Umbrel app proxy auth, prefer that over custom nginx basic auth.

## Persistent data to mount in the app version

At minimum:

- `jpg/`
- `manifest.json`
- `search_index.json`
- `search.db`

If extractor/import is included:

- `pdf/`
- `scans/`

## Asset checklist

### Icon

Recommended:
- simple stylized magazine spread
- flat SVG first
- no rounded corners baked in
- strong silhouette that still reads at small size

Possible visual direction:
- open spread shape
- one spine line in the middle
- one or two horizontal text lines on each page
- accent color borrowed from the site

### Gallery images

Umbrel official guidance expects 3 to 5 images.

Good candidates:
- carousel home view
- spread viewer with a clean magazine page open
- search page with fast results visible
- mobile view

If needed later:
- make polished gallery composites from screenshots instead of raw screenshots

## Open decisions before publishing

- whether the app should be named broadly (`Magazine Archive`) or brand-first (`Pelit Archive`)
- whether extractor tooling belongs inside the app or remains a separate maintenance path
- whether admin tools should be part of the app at all
- whether to rely on Umbrel app proxy auth or keep custom auth logic

## Next practical step

When you are ready, create a GitHub repo from:

- `getumbrel/umbrel-community-app-store`

Then copy the draft files from:

- [umbrel_app_store_draft](E:\claude_projects\pelit_cover_sheets\umbrel_app_store_draft)

and replace the template app with the real app package.

## Current tested status

This is no longer just a draft.

Confirmed working pieces:

- the community app store repo can be added to Umbrel
- the app installs from the custom store
- GHCR-backed images work
- the app icon renders in the Umbrel launcher
- the app runs after reseeding content into its app-data directory

Confirmed caveat:

- uninstalling the app wipes its app data

So the practical deployment model is:

- install the app shell
- reseed content afterward

Current live app-data path on Umbrel:

```text
/home/umbrel/umbrel/app-data/maukka-magazine-archive/site
```

Current reseed helper:

- [umbrel/reseed_app_data.sh](E:\claude_projects\pelit_cover_sheets\umbrel\reseed_app_data.sh)

## Next major app-store task

Add the admin/upload workflow to the app package so the installed app can be populated and maintained without manual host-side copying.
