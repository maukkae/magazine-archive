# Magazine Archive for Umbrel

This repository contains a small Umbrel community app store and one packaged
app: `Magazine Archive`.

`Magazine Archive` is a self-hosted magazine viewer and search tool with:

- cover browsing
- issue and page viewing
- SQLite-backed full-text search
- OCR review and patch import
- PDF and JPG upload through the built-in admin interface

## What This Repository Contains

- `umbrel-app-store.yml`
  - community app-store metadata
- `maukka-magazine-archive/`
  - the Umbrel app package
- `.github/workflows/`
  - container image publishing workflow

## What It Does Not Contain

- large PDF collections
- full JPG archives
- local-machine deployment notes
- private self-hosted content

The app is designed to install as a lightweight shell and then receive archive
content later through the admin workflow or by reseeding the app data
directory.

## App Package Layout

- `maukka-magazine-archive/umbrel-app.yml`
  - Umbrel metadata
- `maukka-magazine-archive/docker-compose.yml`
  - viewer, search, admin, and init services
- `maukka-magazine-archive/viewer/`
  - nginx-based frontend container
- `maukka-magazine-archive/search/`
  - search API container
- `maukka-magazine-archive/admin/`
  - upload and archive-management container
- `maukka-magazine-archive/init/`
  - first-run seeding and static-shell refresh

## Current State

The package is intended for self-hosted Umbrel use and keeps search, viewing,
and administration inside the app itself.
