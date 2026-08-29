# syntax=docker/dockerfile:1

# sh-common: a local sibling checkout (dev) OR its GitHub repo at a version tag
# (CI / published images). Select with --build-arg SH_COMMON_SOURCE=local|git.
#   git   (default) : pip install from github.com/${GH_OWNER}/sh-common@${SH_COMMON_REF}
#                     public repo -> no token; private -> --build-arg GH_TOKEN=<PAT>
#   local           : pip install from the `sh_common` build context
#                     (docker build --build-context sh_common=../sh-common ...)
ARG SH_COMMON_SOURCE=git

# `git` mode installs sh-common with `pip install git+https://…`, which needs git.
FROM python:3.11-slim AS shcommon-git
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

FROM python:3.11-slim AS shcommon-local
COPY --from=sh_common . /sh-common

FROM shcommon-${SH_COMMON_SOURCE} AS base
WORKDIR /app
ARG SH_COMMON_SOURCE=git
ARG GH_OWNER=smart-home-ia-sample
ARG SH_COMMON_REF=v0.2.0
ARG GH_TOKEN=
RUN if [ "$SH_COMMON_SOURCE" = "local" ]; then \
      pip install --no-cache-dir /sh-common; \
    else \
      pip install --no-cache-dir \
        "sh-common @ git+https://${GH_TOKEN:+${GH_TOKEN}@}github.com/${GH_OWNER}/sh-common.git@${SH_COMMON_REF}"; \
    fi

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
ENV PYTHONPATH=/app
EXPOSE 8500
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8500"]
