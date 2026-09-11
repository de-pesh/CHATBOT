# Base images, declared before the first FROM so both stages can use them -
# an ARG declared inside a stage is not visible to a later FROM.
# Point these at an internal mirror on a closed network.
ARG NODE_IMAGE=node:20-alpine
ARG NGINX_IMAGE=nginxinc/nginx-unprivileged:1.25-alpine

# ---------- Build stage ----------
FROM ${NODE_IMAGE} AS builder

WORKDIR /app

# Copy only dependency files first (better caching)
COPY package.json package-lock.json ./
# Internal npm registry, when one is configured.
ARG NPM_REGISTRY=""
RUN if [ -n "$NPM_REGISTRY" ]; then npm config set registry "$NPM_REGISTRY"; fi; npm install --force

# Copy rest of the source (.dockerignore keeps the host's node_modules out,
# otherwise it would clobber the Linux binaries installed above)
COPY . .

# Build Vite app. build:qa is the OpenShift build; the local docker-compose
# stack overrides this with build:local, which drops the external Module
# Federation remote.
ARG BUILD_SCRIPT=build:qa
RUN npm run "$BUILD_SCRIPT"

# ---------- Runtime stage ----------
# nginx-unprivileged, not plain nginx: OpenShift's restricted-v2 SCC runs the
# container as an arbitrary non-root UID, which cannot bind port 80, write
# /var/run/nginx.pid, or own /var/cache/nginx. This variant listens on 8080 and
# keeps its pid + temp paths under /tmp.
FROM ${NGINX_IMAGE}

# The default config and the file copies need write access to root-owned dirs.
USER 0

RUN rm -f /etc/nginx/conf.d/default.conf

COPY nginx.conf /etc/nginx/conf.d/default.conf

# The Vite build sets base "/chatbot/", so the app is served under that prefix
# (see nginx.conf) - on AKS the ingress rewrite stripped it, the HAProxy router
# cannot.
COPY --from=builder /app/dist /usr/share/nginx/html/chatbot

# Arbitrary UID always lands in group 0, so everything nginx reads or writes
# must be group-accessible.
RUN chmod -R g=u /etc/nginx /usr/share/nginx/html /var/cache/nginx /tmp

USER 101

EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
