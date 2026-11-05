# RedHat UBI9 Python 3.12 - runs as UID 1001, app root is /opt/app-root/src.
# Base image registry - point at an internal mirror on a closed network.
ARG BASE_REGISTRY=registry.access.redhat.com

# ==============================================================================
# Build stage: toolchain, dependencies and the model. None of this ships.
# ==============================================================================
FROM ${BASE_REGISTRY}/ubi9/python-312:latest AS builder

USER 1001

WORKDIR /opt/app-root/src

# Dependency manifests first so code edits do not bust the install layer.
COPY --chown=1001:0 requirements.txt ./

# UBI9 ships a venv at /opt/app-root but leaves VIRTUAL_ENV unset. pip then
# installs into the base interpreter under /usr - whose site-packages is
# root-owned - and dies with PermissionError as UID 1001. Pointing VIRTUAL_ENV
# at the venv already on PATH keeps pip inside it.
ENV VIRTUAL_ENV=/opt/app-root

# Internal PyPI mirror, when one is configured.
ARG PIP_INDEX_URL=""
ARG PIP_TRUSTED_HOST=""
RUN if [ -n "$PIP_INDEX_URL" ]; then \
        pip config set global.index-url "$PIP_INDEX_URL"; \
        if [ -n "$PIP_TRUSTED_HOST" ]; then pip config set global.trusted-host "$PIP_TRUSTED_HOST"; fi; \
    fi; \
    pip install --no-cache-dir -r requirements.txt && \
    rm -rf /opt/app-root/src/.cache/pip && \
    python -c "import fastapi, uvicorn, numpy, pptx, docx, extract_msg, pymongo, openpyxl, PIL, pdfplumber, bs4, lxml, langchain_azure_ai, langchain_openai, langchain_ollama, langchain_core, langchain_text_splitters, pydantic_settings, aiohttp, azure.storage.blob; print('dependency smoke test OK')"

# Code last.
COPY --chown=1001:0 . .

# OpenShift runs the container with an arbitrary UID in group 0, so every
# path the app touches must be group-writable. Done here, in the stage that is
# thrown away: previously this ran as the last layer of the shipped image and
# rewrote the mode of all ~1.3 GB of model and venv files, which forced Docker
# to copy every one of them into a new layer.
RUN chmod -R g=u /opt/app-root

# ==============================================================================
# Runtime stage: one COPY of the finished tree, no toolchain, no churn.
# ==============================================================================
FROM ${BASE_REGISTRY}/ubi9/python-312:latest

# ocrmypdf and office_helper (extractors.py) both shell out to binaries at run
# time, so unlike git/git-lfs these belong in the runtime stage, not the
# builder. Carried over from the chat service with ParseFile.
#
# tesseract* / qpdf / unpaper are in neither UBI9 nor EPEL9 (they need an
# entitled RHEL AppStream build), so OCR_MODE=optional installs what is
# available and skips the rest rather than failing the build. Scanned-PDF OCR
# degrades without them; every other format still parses - INCLUDING .docx/
# .doc, which need libreoffice-headless (below), not tesseract.
#
# Confirmed against a real UBI9+EPEL9 container in this repo's dev environment:
# EPEL9 has no "tesseract*" package at all, so eng/ara here (and any language
# you add) can only come from an entitled RHEL AppStream subscription or an
# internal mirror that carries them - never from EPEL. This can't be changed
# via .env/secrets at deploy time; it's baked in at image build time.
#
# To OCR scanned documents in more languages, add more
# "tesseract-langpack-<iso-code>" entries below (check exact package names
# against your own mirror/subscription - they were not guessed here since this
# sandbox has no access to an entitled RHEL repo to verify them against), and
# consider OCR_MODE=optional while you do, so a langpack that turns out not to
# exist on your mirror degrades OCR for that language instead of failing the
# whole build.
#
# libreoffice-headless provides `soffice`, which office_helper shells out to for
# .docx/.doc -> PDF conversion (see extractors.py's _resolve_soffice_path).
# Without it every .docx/.doc upload fails at parse time with a clear
# "LibreOffice executable 'soffice' was not found in PATH" error - .docx/.doc
# were accepted at upload (main-service-chatbot's ALLOWED_EXTENSIONS) with no
# way to know this package was never installed. Unlike tesseract this is
# generally available in EPEL9 without an entitled subscription, but was not
# verified against an entitled mirror from this sandbox - confirm against
# your own before relying on it, same as the tesseract packages above.
USER 0
ARG EPEL_RPM_URL=https://dl.fedoraproject.org/pub/epel/epel-release-latest-9.noarch.rpm
# "optional" is the default on purpose: tesseract/qpdf/unpaper are confirmed
# absent from plain UBI9+EPEL9 (see above), so a "required" default would
# fail a plain `docker build` with no --build-arg flags out of the box, on
# every machine without an entitled RHEL AppStream subscription. Pass
# --build-arg OCR_MODE=required yourself if your build machine has that
# entitlement and you want the build to fail loudly on a missing package
# instead of degrading.
ARG OCR_MODE=optional
ARG OCR_PACKAGES="tesseract tesseract-langpack-eng tesseract-langpack-ara ghostscript qpdf pngquant unpaper libreoffice-headless"
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

# Brings both the venv (/opt/app-root/lib) and the app plus model
# (/opt/app-root/src) across in a single layer.
COPY --from=builder --chown=1001:0 /opt/app-root /opt/app-root

# Default mount point for STORAGE_TYPE=local. Created here, owned by 1001:0 and
# group-writable, so that:
#   * a fresh Docker named volume inherits these permissions when it is first
#     mounted (Docker seeds an empty volume from the image), and
#   * on OpenShift the arbitrary UID - always in group 0 - can write to the PVC
#     mounted over it, given fsGroup on the pod.
# Without this the store is root-owned 0755 and startup fails the writability
# check in services/storage/local_file_storage.py.
USER 0
RUN mkdir -p /data/files && chown -R 1001:0 /data && chmod -R g=u /data
USER 1001

ENV PYTHONUNBUFFERED=1 \
    HOME=/opt/app-root/src \
    VIRTUAL_ENV=/opt/app-root

EXPOSE 8001

CMD ["python", "-m", "app"]
