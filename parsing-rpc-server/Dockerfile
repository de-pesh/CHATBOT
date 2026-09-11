# RedHat UBI9 Python 3.12 - runs as UID 1001, app root is /opt/app-root/src.
# Base image registry - point at an internal mirror on a closed network.
ARG BASE_REGISTRY=registry.access.redhat.com
FROM ${BASE_REGISTRY}/ubi9/python-312:latest

# The document/OCR toolchain is not in the UBI9 repos, so EPEL9 is enabled here.
#
# WARNING - libreoffice is NOT in UBI9 or EPEL9. It ships in the full RHEL9
# AppStream, which needs an entitled build (subscription-manager attached, or
# a build on an entitled RHEL/OpenShift node). Until then, any code path that
# shells out to `soffice` for doc conversion will fail at runtime.
USER 0
# EPEL supplies packages UBI9 does not carry. On a closed network set
# EPEL_RPM_URL="" and make sure the internal repos already provide them.
ARG EPEL_RPM_URL=https://dl.fedoraproject.org/pub/epel/epel-release-latest-9.noarch.rpm

# OCR_MODE=required (the default, and what deploy/build.sh gets) fails the build
# if any OCR package is missing. OCR_MODE=optional installs them one at a time
# and keeps going past the ones the enabled repos do not carry - on a plain
# UBI9 + EPEL9 host that is tesseract* and qpdf, which live in the full RHEL9
# AppStream and need an entitled build, the same gap `soffice` has above. OCR
# then degrades at runtime instead of blocking the build; the local
# docker-compose stack uses this, since only scanned PDFs need it.
ARG OCR_MODE=required
ARG OCR_PACKAGES="tesseract tesseract-langpack-eng tesseract-langpack-ara ghostscript qpdf pngquant unpaper"
RUN if [ -n "$EPEL_RPM_URL" ]; then dnf install -y "$EPEL_RPM_URL"; fi && \
    dnf install -y git && \
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
RUN if [ -n "$PIP_INDEX_URL" ]; then \
        pip config set global.index-url "$PIP_INDEX_URL"; \
        if [ -n "$PIP_TRUSTED_HOST" ]; then pip config set global.trusted-host "$PIP_TRUSTED_HOST"; fi; \
        export POETRY_PYPI_MIRROR_URL="$PIP_INDEX_URL"; \
    fi; \
    pip install --no-cache-dir poetry && \
    poetry config virtualenvs.create false && \
    poetry install --no-root --no-interaction --no-ansi

COPY --chown=1001:0 . .

# OpenShift runs the container with an arbitrary UID in group 0, so every
# path the app touches must be group-writable.
RUN chmod -R g=u /opt/app-root/src

# HOME is not writable under an arbitrary UID; point every cache at the app dir.
# LibreOffice also needs a writable profile dir if/when it becomes available.
#
# No HF/torch here: this service only parses documents. It used to bake in the
# gte-multilingual-base model and the full torch stack for an EmbeddingService
# that was never imported - ~15 GB of image for dead code. Embedding lives in
# indexing-rpc-server, which is the only thing that actually embeds.
ENV PYTHONUNBUFFERED=1 \
    HOME=/opt/app-root/src \
    TESSDATA_PREFIX=/usr/share/tesseract/tessdata

EXPOSE 8000

CMD ["python", "-m", "app"]
