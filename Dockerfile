FROM python:3.14-slim

# Workspace snapshots run git against a shadow repository under the instance.
RUN apt-get update \
    && apt-get install --no-install-recommends --yes git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .

ENV KINBY_INSTANCE=/instance
VOLUME ["/instance"]

ENTRYPOINT ["kinby"]
CMD ["run"]
