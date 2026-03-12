# Magazine Archive Umbrel Packaging Repo

This directory is an exported packaging scaffold from:

- E:\claude_projects\pelit_cover_sheets

Treat this folder as the Umbrel app-store packaging side, not the main source repo.

Workflow:

1. Do normal development in the source repo.
2. Run `export_umbrel_app_store.ps1` from the source repo.
3. Review exported files here.
4. Commit packaging-specific changes in this repo.

Current exported content:

- `umbrel-app-store.yml`
- `maukka-magazine-archive/umbrel-app.yml`
- `maukka-magazine-archive/docker-compose.yml`
- `maukka-magazine-archive/ASSETS.md`
- `umbrel_app_store_prep.md`

The final app-store repo should stay generic and avoid the old `pelit` branding in public-facing app/package naming.
