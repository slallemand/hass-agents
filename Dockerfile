# hass-agents — Red Hat Hardened Images (catalog: https://images.redhat.com)
# Pull registry: registry.access.redhat.com/hi/...
#
# Multi-stage: builder installs deps + app into a venv; minimal runtime copies it.
#
# NOTE: hi/python sets ENV PYTHON_VERSION to the full patch (e.g. 3.12.14).
# Use PYTHON_SERIES for image tags and RPM names (python3.12-devel), never PYTHON_VERSION.
#
# NOTE: do NOT use `pip install --prefix` on RHEL — native wheels (pydantic_core, etc.)
# land in lib64/ while pure-Python packages land in lib/, breaking imports. Use a venv.

ARG RH_REGISTRY=registry.access.redhat.com
ARG PYTHON_SERIES=3.12
ARG APP_UID=65532
ARG APP_GID=65532

# -----------------------------------------------------------------------------
# Builder
# -----------------------------------------------------------------------------
FROM ${RH_REGISTRY}/hi/python:${PYTHON_SERIES}-builder AS builder

ARG PYTHON_SERIES
ARG APP_UID
ARG APP_GID

USER root
RUN dnf install -y \
      gcc \
      gcc-c++ \
      make \
      "python${PYTHON_SERIES}-devel" \
      openssl-devel \
      libffi-devel \
    && dnf clean all

USER ${APP_UID}:${APP_GID}

WORKDIR /opt/app-root/src

COPY --chown=${APP_UID}:${APP_GID} pyproject.toml README.md ./
COPY --chown=${APP_UID}:${APP_GID} src ./src
COPY --chown=${APP_UID}:${APP_GID} config ./config

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH=/opt/app-root/venv/bin:${PATH}

RUN python3 -m venv /opt/app-root/venv \
 && /opt/app-root/venv/bin/pip install --upgrade pip \
 && /opt/app-root/venv/bin/pip install ".[llm]"

# -----------------------------------------------------------------------------
# Runtime (minimal Hardened Image — no package manager)
# -----------------------------------------------------------------------------
FROM ${RH_REGISTRY}/hi/python:${PYTHON_SERIES}

ARG APP_UID
ARG APP_GID

USER ${APP_UID}:${APP_GID}

WORKDIR /opt/app-root/src

COPY --from=builder --chown=${APP_UID}:${APP_GID} \
     /opt/app-root /opt/app-root

ENV PYTHONUNBUFFERED=1 \
    PATH=/opt/app-root/venv/bin:${PATH} \
    VIRTUAL_ENV=/opt/app-root/venv \
    ENTITIES_CONFIG=/opt/app-root/src/config/entities.yaml

CMD ["/opt/app-root/venv/bin/hass-agents", "serve"]
