# Multi-stage image: build the SPA with Node, then serve it and the API from one
# Python process on one port. The frontend calls /api on its own origin, so no
# proxy or CORS configuration is involved.
#
#   docker build -t variant-pdp .
#   docker run --rm -p 8000:8000 -v pdp-data:/data variant-pdp
#   # open http://127.0.0.1:8000      (API docs at /docs)

# ---- stage 1: build the frontend ----
# node:22-slim (glibc), not alpine: esbuild's prebuilt binary targets glibc, and
# on musl the Vite build needs extra compatibility shims.
FROM node:22-slim AS frontend
WORKDIR /build

# The lockfile decides the versions, so install with the pnpm release that wrote it.
RUN npm install --global pnpm@11.7.0

# Dependency manifests first: this layer is cached until they change.
COPY app/frontend/package.json app/frontend/pnpm-lock.yaml app/frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

# Sources, then the production build (tsc --noEmit && vite build).
COPY app/frontend/ ./
RUN pnpm build

# ---- stage 2: the API, with the built SPA served from the same origin ----
FROM python:3.12-slim AS api

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PDP_DB_PATH=/data/pdp.sqlite3 \
    PDP_STATIC_DIR=/srv/static

WORKDIR /srv

COPY app/backend/requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r requirements.txt

COPY app/backend/ ./
COPY --from=frontend /build/dist ./static

# The SQLite file lives on a volume so the catalogue and carts survive a restart;
# it is created and seeded on first start.
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000

# urllib rather than httpx: the runtime image installs only runtime dependencies.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2).status == 200 else 1)"]

# Not --reload, and not a dev server: this is the same ASGI app the tests drive.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
