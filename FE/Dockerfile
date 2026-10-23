# Base images, declared before the first FROM so both stages can use them -
# an ARG declared inside a stage is not visible to a later FROM.
# Red Hat UBI9 S2I images, same registry as main-service-chatbot/parsing-indexing-services-chatbot's
# Python base - both are freely pullable with no subscription, so a network
# restricted to Red Hat's registry (plus an internal mirror/Quay) can still
# build this image. Point BASE_REGISTRY at an internal mirror if even that is
# not reachable directly.
ARG BASE_REGISTRY=registry.access.redhat.com
ARG NODE_IMAGE=${BASE_REGISTRY}/ubi9/nodejs-20:latest
ARG NGINX_IMAGE=${BASE_REGISTRY}/ubi9/nginx-124:latest

# ---------- Build stage ----------
FROM ${NODE_IMAGE} AS builder

# The image's own default user (1001) already owns /opt/app-root/src, but a
# plain COPY (no --chown) still lands root:root regardless of the current
# USER - npm then fails rewriting package-lock.json in place with EACCES.
WORKDIR /opt/app-root/src

# Copy only dependency files first (better caching)
COPY --chown=1001:0 package.json package-lock.json ./
# Internal npm registry, when one is configured.
ARG NPM_REGISTRY=""
RUN if [ -n "$NPM_REGISTRY" ]; then npm config set registry "$NPM_REGISTRY"; fi; npm install --force

# Copy rest of the source (.dockerignore keeps the host's node_modules out,
# otherwise it would clobber the Linux binaries installed above)
COPY --chown=1001:0 . .

# Build Vite app. build:qa is the OpenShift build; the local docker-compose
# stack overrides this with build:local, which drops the external Module
# Federation remote.
ARG BUILD_SCRIPT=build:qa
# Where the browser finds main-service. It is compiled into the bundle, so it is a
# build argument, not a runtime setting: docker-compose.yml feeds it from the
# repo-root .env (VITE_API_BASE_URL). Empty means the app's own default, http://localhost:8000.
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run "$BUILD_SCRIPT"

# ---------- Runtime stage ----------
# ubi9/nginx-124 is an S2I image: its entrypoint generates /etc/nginx/nginx.conf
# at container start with a fixed server{} (listen 8080, root /opt/app-root/src)
# that includes /opt/app-root/etc/nginx.default.d/*.conf from inside that
# server block - so our config is a snippet of location blocks, not a full
# server{}, and it lands there rather than at /etc/nginx/conf.d/default.conf.
# It already runs as UID 1001 in group 0 (same convention as the other two
# images), so none of the chown/USER 0 dance the old nginx-unprivileged image
# needed for that is necessary here beyond the app files we add ourselves.
FROM ${NGINX_IMAGE}

USER 0

COPY --chown=1001:0 nginx-chatbot.conf /opt/app-root/etc/nginx.default.d/chatbot.conf

# The Vite build sets base "/chatbot/", so the app is served under that prefix
# (see nginx-chatbot.conf) - on AKS the ingress rewrite stripped it, the
# HAProxy router cannot. Root is fixed at /opt/app-root/src by the base image.
COPY --from=builder --chown=1001:0 /opt/app-root/src/dist /opt/app-root/src/chatbot

# Arbitrary UID always lands in group 0, so everything nginx reads or writes
# must be group-accessible.
RUN chmod -R g=u /opt/app-root

USER 1001

EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
