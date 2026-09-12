# RedHat UBI9 Python 3.12 - runs as UID 1001, app root is /opt/app-root/src.
# Base image registry - point at an internal mirror on a closed network.
ARG BASE_REGISTRY=registry.access.redhat.com

# ==============================================================================
# Build stage: toolchain, dependencies and the model. None of this ships.
# ==============================================================================
FROM ${BASE_REGISTRY}/ubi9/python-312:latest AS builder

# git-lfs is not in the UBI9 repos, so pull EPEL9 in for it. Build-stage only,
# so there is no need to uninstall it afterwards.
USER 0
# EPEL supplies packages UBI9 does not carry. On a closed network set
# EPEL_RPM_URL="" and make sure the internal repos already provide them.
ARG EPEL_RPM_URL=https://dl.fedoraproject.org/pub/epel/epel-release-latest-9.noarch.rpm
RUN if [ -n "$EPEL_RPM_URL" ]; then dnf install -y "$EPEL_RPM_URL"; fi && \
    dnf install -y git git-lfs && \
    git lfs install --system
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
# Poetry goes in its own throwaway venv, not the app venv. "pip uninstall
# poetry" leaves its dependencies (dulwich, rapidfuzz, zstandard - ~43 MB)
# behind in site-packages, and poetry also keeps an artifact cache under
# .cache/pypoetry - ~650 MB for this dependency set - that pip's
# --no-cache-dir does not cover. Both would otherwise ride into the runtime
# stage inside /opt/app-root.
RUN if [ -n "$PIP_INDEX_URL" ]; then \
        pip config set global.index-url "$PIP_INDEX_URL"; \
        if [ -n "$PIP_TRUSTED_HOST" ]; then pip config set global.trusted-host "$PIP_TRUSTED_HOST"; fi; \
        export POETRY_PYPI_MIRROR_URL="$PIP_INDEX_URL"; \
    fi; \
    python -m venv /tmp/poetry-venv && \
    /tmp/poetry-venv/bin/pip install --no-cache-dir poetry && \
    /tmp/poetry-venv/bin/poetry config virtualenvs.create false && \
    /tmp/poetry-venv/bin/poetry install --no-root --only main --no-interaction --no-ansi && \
    pip install --no-cache-dir grpcio-reflection && \
    rm -rf /tmp/poetry-venv /opt/app-root/src/.cache/pypoetry /opt/app-root/src/.cache/pip && \
    python -c "import grpc, grpc_reflection, google.protobuf, sentence_transformers, torch, pymongo, fitz, ocrmypdf, openpyxl, PIL, langchain_azure_ai; print('dependency smoke test OK')"

# Model must land next to app/ - embedding_service.py resolves it as
# Path(__file__).parents[2] / "hf_models" / "gte-multilingual-base".
# HF_MODEL_MODE=download clones from huggingface.co; =vendored uses the copy
# already placed in <service>/vendor/, for machines with no internet access.
ARG HF_MODEL_MODE=download
COPY --chown=1001:0 vendor/ ./vendor/
RUN if [ "$HF_MODEL_MODE" = "vendored" ]; then \
        test -d vendor/gte-multilingual-base || { \
            echo "HF_MODEL_MODE=vendored but vendor/gte-multilingual-base is missing"; exit 1; }; \
        mkdir -p hf_models && cp -r vendor/gte-multilingual-base hf_models/; \
    else \
        echo "Downloading Hugging Face model..." && \
        git clone --depth 1 https://huggingface.co/Alibaba-NLP/gte-multilingual-base \
            hf_models/gte-multilingual-base && \
        rm -rf hf_models/gte-multilingual-base/.git; \
    fi && rm -rf vendor && echo "Model ready."

# Warm the model once, while the network is still reachable.
#
# gte-multilingual-base's config.json maps its classes to code in a DIFFERENT
# repo - "auto_map": {"AutoModel": "Alibaba-NLP/new-impl--modeling.NewModel"} -
# which transformers downloads at load time because trust_remote_code=True.
# Cloning only the model repo and then setting HF_HUB_OFFLINE=1 below meant that
# lookup could never resolve, so every embed call failed at runtime with
# "Cannot generate embeddings because the model failed to load" and nothing
# could ever be indexed.
#
# Loading it here caches that remote code into the image, and turns a silent
# runtime failure into a build failure if the model cannot be loaded at all.
RUN HF_HOME=/opt/app-root/src/.cache/huggingface \
    python -c "\
from sentence_transformers import SentenceTransformer; \
m = SentenceTransformer('/opt/app-root/src/hf_models/gte-multilingual-base', trust_remote_code=True); \
print('embedding model warm-up OK, dim =', m.get_sentence_embedding_dimension())"

# Code last, so edits do not trigger a model redownload.
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

# ocrmypdf shells out to these at run time, so unlike git/git-lfs they belong
# in the runtime stage, not the builder. Carried over from the chat service
# with ParseFile.
#
# tesseract* / qpdf / unpaper are in neither UBI9 nor EPEL9 (they need an
# entitled RHEL AppStream build), so OCR_MODE=optional installs what is
# available and skips the rest rather than failing the build. Scanned-PDF OCR
# degrades; every other format parses.
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

# Brings both the venv (/opt/app-root/lib) and the app plus model
# (/opt/app-root/src) across in a single layer.
COPY --from=builder --chown=1001:0 /opt/app-root /opt/app-root

USER 1001

# HOME is not writable under an arbitrary UID; point every cache at the app dir.
# TRANSFORMERS_CACHE is deliberately NOT set. It is deprecated, and it overrides
# the hub cache path wholesale: with both set, HF_HOME put the warmed download
# in .cache/huggingface/hub/ while TRANSFORMERS_CACHE made the lookup search
# .cache/huggingface/ - one level too high. Every load then failed offline with
# "couldn't connect to https://huggingface.co". HF_HOME alone is correct and
# covers both the hub cache and the trust_remote_code module cache.
ENV PYTHONUNBUFFERED=1 \
    HOME=/opt/app-root/src \
    HF_HOME=/opt/app-root/src/.cache/huggingface \
    HF_HUB_OFFLINE=1 \
    VIRTUAL_ENV=/opt/app-root

EXPOSE 8000

CMD ["python", "-m", "app"]
