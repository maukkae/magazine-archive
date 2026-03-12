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
  - empty archive shell with static site files, empty metadata, and empty search DB
- `viewer/`
  - nginx config that proxies `/api/` to the `search` service
- `search/`
  - containerized search API using `search_server.py`
- `init/`
  - first-run seeding logic for `${APP_DATA_DIR}/site`

This package is intentionally light. It does not bundle the real archive
content. The expected future flow is:

1. install the app
2. use the built-in admin/upload workflow to add real magazine data
3. keep large JPG/PDF collections outside the Git repo

This package is not yet the final production Umbrel app. It is a staging point
for converting the current working local/Umbrel deployment into a clean
community app-store package.
