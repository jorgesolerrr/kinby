# The instance image is the first stage, and the hub builds instances from that stage alone.
# The stages after it build the hub image, which is the instance image plus the web app.
FROM python:3.14-slim AS instance

# Workspace snapshots run git against a shadow repository under the instance. uv installs
# a package on top of this image; a package's own recipe adds whatever else it needs. Node
# needs libatomic.
RUN apt-get update \
    && apt-get install --no-install-recommends --yes ca-certificates curl git libatomic1 \
    && rm -rf /var/lib/apt/lists/*
# uvx and npx start the local MCP servers an instance picks. Node is the current LTS release,
# from nodejs.org because Debian's package lags it.
COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /uvx /usr/local/bin/
ARG NODE_VERSION=26.13.1
RUN case "$(dpkg --print-architecture)" in \
        amd64) node_arch=x64 ;; \
        arm64) node_arch=arm64 ;; \
        *) exit 1 ;; \
    esac \
    && node="node-v${NODE_VERSION}-linux-${node_arch}" \
    && curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/${node}.tar.gz" \
        | tar -xz -C /usr/local --strip-components=1 "${node}/bin" "${node}/lib"
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

# `kinby hub` serves the app from this path unless --web-app names another. The hub drives the
# browser and runs its browser service from this image, so only the hub image carries Playwright.
FROM instance AS hub
RUN pip install --no-cache-dir ".[hub]" && playwright install --with-deps --only-shell chromium
COPY --from=web /web/apps/web/dist /usr/local/share/kinby/web

# The default target is the instance image, so a plain build never runs the web stage.
FROM instance
