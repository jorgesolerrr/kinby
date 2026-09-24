FROM python:3.14-slim

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
