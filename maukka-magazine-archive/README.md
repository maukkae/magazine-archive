# Magazine Archive Umbrel App Draft

This is a draft Umbrel app packaging layout for the Magazine Archive project.

Current structure:

- `umbrel-app.yml`
  - app metadata draft
- `docker-compose.yml`
  - `init` seeding container
  - `viewer` nginx container
  - `search` Python/Flask container
- `seed/site/`
  - minimal demo archive payload (`Pelit 1992/01`)
- `viewer/`
  - nginx config that proxies `/api/` to the `search` service
- `search/`
  - containerized search API using `search_server.py`
- `init/`
  - first-run seeding logic for `${APP_DATA_DIR}/site`

This package is not yet the final production Umbrel app. It is a staging point
for converting the current working local/Umbrel deployment into a clean
community app-store package.
