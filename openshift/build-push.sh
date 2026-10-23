#!/usr/bin/env bash
# Builds the images and pushes them to Quay. Run on a machine with docker that can reach the registry.
#
#   docker login quay-quay-registry.apps.dda-desc-az1-01.desccii.local
#   ./openshift/build-push.sh v1               # all of them
#   ./openshift/build-push.sh v1 main-service  # one: main-service | indexing | frontend | tika
#
# The frontend has the API address baked in at build time: API_URL below must be the main-service route in 08-routes.yaml.
set -euo pipefail

TAG="${1:?usage: build-push.sh <image-tag> [main-service|indexing|frontend|tika]}"
ONLY="${2:-all}"

REGISTRY="${REGISTRY:-quay-quay-registry.apps.dda-desc-az1-01.desccii.local/dda-desc}"
API_URL="${API_URL:-https://chatbot-api.apps.desc-bot.desc.local}"

# On a network with no internet, point these at internal mirrors (leave empty otherwise).
BASE_REGISTRY="${BASE_REGISTRY:-registry.access.redhat.com}"
PIP_INDEX_URL="${PIP_INDEX_URL:-}"
PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST:-}"
NPM_REGISTRY="${NPM_REGISTRY:-}"
EPEL_RPM_URL="${EPEL_RPM_URL:-https://dl.fedoraproject.org/pub/epel/epel-release-latest-9.noarch.rpm}"
NODE_IMAGE="${NODE_IMAGE:-}"     # empty = the Dockerfile default (Red Hat UBI9 Node 20)
NGINX_IMAGE="${NGINX_IMAGE:-}"   # empty = the Dockerfile default (Red Hat UBI9 nginx)
TIKA_SOURCE="${TIKA_SOURCE:-apache/tika:4.0.0}"

cd "$(dirname "$0")/.."
want() { [ "$ONLY" = "all" ] || [ "$ONLY" = "$1" ]; }
build() {  # <name> <context> [extra docker build args...]
  local name="$1" ctx="$2"; shift 2
  echo "==> $REGISTRY/$name:$TAG"
  # OpenShift nodes are x86_64: a silently arm64 image dies with "exec format error".
  docker build --platform linux/amd64 -t "$REGISTRY/$name:$TAG" "$@" "$ctx"
  docker push "$REGISTRY/$name:$TAG"
}

want main-service && build chatbot-main-service main-service-chatbot \
  --build-arg BASE_REGISTRY="$BASE_REGISTRY" --build-arg PIP_INDEX_URL="$PIP_INDEX_URL" --build-arg PIP_TRUSTED_HOST="$PIP_TRUSTED_HOST"

# EMBEDDING_MODE=remote: the embedding model is called over the LiteLLM proxy, so no torch / model is baked in.
want indexing && build chatbot-parsing-indexing-service parsing-indexing-service \
  --build-arg BASE_REGISTRY="$BASE_REGISTRY" --build-arg PIP_INDEX_URL="$PIP_INDEX_URL" --build-arg PIP_TRUSTED_HOST="$PIP_TRUSTED_HOST" \
  --build-arg EPEL_RPM_URL="$EPEL_RPM_URL" --build-arg EMBEDDING_MODE=remote --build-arg OCR_MODE=optional

want frontend && build chatbot-frontend FE \
  --build-arg BASE_REGISTRY="$BASE_REGISTRY" --build-arg NODE_IMAGE="$NODE_IMAGE" --build-arg NGINX_IMAGE="$NGINX_IMAGE" \
  --build-arg NPM_REGISTRY="$NPM_REGISTRY" --build-arg BUILD_SCRIPT=build:local --build-arg VITE_API_BASE_URL="$API_URL"

# Tika is someone else's image: copy it into Quay so the cluster does not need internet. The tag is fixed (4.0.0),
# not $TAG: 09-deployment-tika.yaml refers to it by that.
if want tika; then
  echo "==> $REGISTRY/tika:4.0.0"
  docker pull --platform linux/amd64 "$TIKA_SOURCE"
  docker tag "$TIKA_SOURCE" "$REGISTRY/tika:4.0.0"
  docker push "$REGISTRY/tika:4.0.0"
fi

echo "Pushed tag $TAG. Next:  ./openshift/deploy.sh $TAG"
