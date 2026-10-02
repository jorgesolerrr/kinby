# Runs `bun run check` on Linux for a Windows host. `bun run check:linux` builds this image and
# mounts the checkout at /kinby. The venv and node_modules stay inside the container.
FROM oven/bun:1.4.2 AS bun

FROM python:3.14-slim

# The tests run git, and zoneinfo reads the system tzdata.
RUN apt-get update \
    && apt-get install --no-install-recommends --yes ca-certificates git tzdata \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /usr/local/bin/uv
COPY --from=bun /usr/local/bin/bun /usr/local/bin/bun
# The venv sits outside the mount, so the host's .venv is never touched.
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /kinby

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project

COPY package.json bun.lock ./
COPY apps/web/package.json apps/web/
COPY packages/contract/package.json packages/contract/
RUN bun install --frozen-lockfile

CMD ["bun", "run", "check"]
