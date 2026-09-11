# RedHat UBI9 Python 3.12 - runs as UID 1001, app root is /opt/app-root/src.
# The venv at /opt/app-root is already on PATH, so pip/poetry install into it.
# Base image registry - point at an internal mirror on a closed network.
ARG BASE_REGISTRY=registry.access.redhat.com
FROM ${BASE_REGISTRY}/ubi9/python-312:latest

# curl is already present as curl-minimal; installing "curl" here would
# conflict with it. Add extra RPMs as USER 0, then drop back to 1001.
#
# The document/OCR toolchain came with the parsing code folded in from
# parsing-rpc-server. As there, tesseract* / qpdf / unpaper are in neither UBI9
# nor EPEL9 (they need an entitled RHEL AppStream build), so OCR_MODE=optional
# installs what is available and skips the rest rather than failing the build.
# Scanned-PDF OCR degrades; everything else parses.
USER 0
ARG EPEL_RPM_URL=https://dl.fedoraproject.org/pub/epel/epel-release-latest-9.noarch.rpm
ARG OCR_MODE=required
ARG OCR_PACKAGES="tesseract tesseract-langpack-eng tesseract-langpack-ara ghostscript qpdf pngquant unpaper"
RUN if [ -n "$EPEL_RPM_URL" ]; then dnf install -y "$EPEL_RPM_URL"; fi && \
    if [ "$OCR_MODE" = "optional" ]; then \
        for pkg in $OCR_PACKAGES; do \
            dnf install -y "$pkg" || echo "OCR package unavailable, skipping: $pkg"; \
        done; \
    else \
        dnf install -y $OCR_PACKAGES; \
    fi \
    && dnf clean all && rm -rf /var/cache/dnf
USER 1001

WORKDIR /opt/app-root/src

# Dependency manifests first so code edits do not bust the install layer.
COPY --chown=1001:0 pyproject.toml ./

# UBI9 ships a venv at /opt/app-root but leaves VIRTUAL_ENV unset. Poetry then
# has no active env to detect and, with virtualenvs.create=false, falls back to
# the base interpreter under /usr - whose site-packages is root-owned, so the
# install dies with PermissionError as UID 1001. Pointing VIRTUAL_ENV at the
# venv that is already on PATH keeps poetry inside it.
ENV VIRTUAL_ENV=/opt/app-root

# Internal PyPI mirror, when one is configured. pip and poetry have to be told
# separately - poetry does not read pip's index-url.
ARG PIP_INDEX_URL=""
ARG PIP_TRUSTED_HOST=""
# Poetry goes in its own throwaway venv, not the app venv. Installed alongside
# the app it leaves ~43 MB of its own dependencies (dulwich, rapidfuzz,
# zstandard) behind in site-packages, and "pip uninstall poetry" does not take
# those with it. It also keeps an artifact cache under .cache/pypoetry - 245 MB
# for this dependency set - that pip's --no-cache-dir does not cover.
RUN if [ -n "$PIP_INDEX_URL" ]; then \
        pip config set global.index-url "$PIP_INDEX_URL"; \
        if [ -n "$PIP_TRUSTED_HOST" ]; then pip config set global.trusted-host "$PIP_TRUSTED_HOST"; fi; \
        export POETRY_PYPI_MIRROR_URL="$PIP_INDEX_URL"; \
    fi; \
    python -m venv /tmp/poetry-venv && \
    /tmp/poetry-venv/bin/pip install --no-cache-dir poetry && \
    /tmp/poetry-venv/bin/poetry config virtualenvs.create false && \
    /tmp/poetry-venv/bin/poetry install --no-root --no-interaction --no-ansi && \
    rm -rf /tmp/poetry-venv /opt/app-root/src/.cache/pypoetry && \
    python -c "import fastapi, uvicorn, aiohttp, redis, motor, grpc, fitz, openpyxl, ocrmypdf, langchain_openai, langchain_ollama; print('dependency smoke test OK')"

COPY --chown=1001:0 . .

# OpenShift runs the container with an arbitrary UID in group 0, so every
# path the app touches must be group-writable.
RUN chmod -R g=u /opt/app-root/src

ENV PYTHONUNBUFFERED=1 \
    HOME=/opt/app-root/src

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
