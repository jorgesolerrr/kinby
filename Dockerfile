FROM python:3.14-slim

ARG GH_VERSION=2.82.1

# Workspace snapshots run git against a shadow repository under the instance.
# A coding workspace also needs gh (issues, pull requests) and uv (its own checks).
RUN apt-get update \
    && apt-get install --no-install-recommends --yes ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/* \
    && arch="$(dpkg --print-architecture)" \
    && curl -fsSL "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_${arch}.tar.gz" \
        | tar -xz -C /usr/local --strip-components=1 "gh_${GH_VERSION}_linux_${arch}/bin/gh"
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
CMD ["run"]
