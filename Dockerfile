# The instance image is the first stage, and the hub builds instances from that stage alone.
# The stages after it build the hub image, which is the instance image plus the web app.
FROM python:3.14-slim AS instance

# Workspace snapshots run git against a shadow repository under the instance. uv installs
# a package on top of this image; a package's own recipe adds whatever else it needs.
RUN apt-get update \
    && apt-get install --no-install-recommends --yes ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /usr/local/bin/uv
# uv uses the image's Python instead of downloading one.
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY docker/entrypoint.sh /usr/local/bin/kinby-entrypoint
RUN chmod +x /usr/local/bin/kinby-entrypoint

ENV KINBY_INSTANCE=/instance
VOLUME ["/instance"]

ENTRYPOINT ["kinby-entrypoint"]
CMD ["repl"]

FROM oven/bun:1.4.2 AS web
WORKDIR /web
COPY package.json bun.lock ./
COPY apps/web/package.json apps/web/
COPY packages/contract/package.json packages/contract/
RUN bun install --frozen-lockfile
COPY apps apps
COPY packages packages
RUN bun run --filter @kinby/web build

# `kinby hub` serves the app from this path unless --web-app names another.
FROM instance AS hub
COPY --from=web /web/apps/web/dist /usr/local/share/kinby/web

# The default target is the instance image, so a plain build never runs the web stage.
FROM instance
