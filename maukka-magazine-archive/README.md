# Magazine Archive Umbrel App Draft

This is a draft Umbrel app packaging layout for the Magazine Archive project.

Current structure:

- `umbrel-app.yml`
  - app metadata draft
- `docker-compose.yml`
  - `app_proxy` service for Umbrel routing
  - `init` seeding container
  - `viewer` nginx container
  - `search` Python/Flask container
  - `admin` Python/Flask container for uploads and archive management
- `seed/site/`
  - empty archive shell with static site files, empty metadata, and empty search DB
- `viewer/`
  - nginx config that proxies `/api/` to the `search` service
- `search/`
  - containerized search API using `search_server.py`
- `admin/`
  - containerized admin panel and upload pipeline
- `init/`
  - first-run seeding logic for `${APP_DATA_DIR}/site`

This package is intentionally light. It does not bundle the real archive
content. The expected future flow is:

1. install the app
2. use the built-in admin/upload workflow to add real magazine data
3. keep large JPG/PDF collections outside the Git repo

The current draft also disables Umbrel proxy auth so the viewer can stay open
on the local network without an extra login prompt.

Container images expected by this draft:

- `ghcr.io/maukkae/magazine-archive-init:latest`
- `ghcr.io/maukkae/magazine-archive-viewer:latest`
- `ghcr.io/maukkae/magazine-archive-search:latest`
- `ghcr.io/maukkae/magazine-archive-admin:latest`

Those images are meant to be published automatically from GitHub Actions via:

- `.github/workflows/publish-ghcr-images.yml`

After the first workflow run, make the GHCR packages public so Umbrel can pull
them anonymously.

This package is not yet the final production Umbrel app. It is a staging point
for converting the current working local/Umbrel deployment into a clean
community app-store package.
