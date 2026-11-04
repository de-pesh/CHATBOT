#!/usr/bin/env bash
# Deploys the chatbot to OpenShift: everything in openshift/, in the right order, waiting for each pod.
#
#   oc login <api-url>
#   ./openshift/build-push.sh v1        # on a machine with docker (once per release)
#   ./openshift/deploy.sh v1            # on a machine with oc
#
# Usage: deploy.sh <image-tag> [namespace]     (namespace defaults to ai-dca)
# Re-running is safe; to roll out a new release, build-push a new tag and run deploy.sh with it.
set -euo pipefail

TAG="${1:?usage: deploy.sh <image-tag> [namespace]}"
NS="${2:-ai-dca}"
cd "$(dirname "$0")"

command -v oc >/dev/null || { echo "oc is not installed on this machine."; exit 1; }
oc whoami >/dev/null 2>&1 || { echo "Not logged in: run  oc login <api-url>  first."; exit 1; }

# Refuse to deploy with a value still to fill in. <TAG> is filled in below; comments are ignored.
LEFT=$(grep -nE '<[A-Z][A-Z0-9_]*>' 01-secrets.yaml 02-configmap.yaml 08-routes.yaml 11-networkpolicy.yaml \
        | grep -vE '^[^:]*:[0-9]+: *#' || true)
if [ -n "$LEFT" ]; then
  echo "These values are still placeholders - fill them in, then run again:"
  echo "$LEFT" | sed -E 's/(Password=)[^;"]*/\1***/'
  exit 1
fi

oc get project "$NS" >/dev/null 2>&1 || oc new-project "$NS" >/dev/null
echo "Namespace: $NS   Tag: $TAG"

if ! oc get secret quay-pull-secret -n "$NS" >/dev/null 2>&1; then
  echo "WARNING: secret 'quay-pull-secret' does not exist in $NS, so pods cannot pull from Quay. Create it first:"
  echo "  oc create secret docker-registry quay-pull-secret -n $NS --docker-server=<quay-host> --docker-username=<user> --docker-password=<password>"
fi

apply() { sed "s|<TAG>|$TAG|g" "$1" | oc apply -n "$NS" -f - ; }

# Storage, config and secrets first; then the pods the others call; then the public routes; the network rules last.
# Skip the PVC when STORAGE_TYPE=quay (no shared volume needed).
if grep -qE 'STORAGE_TYPE:\s*"local"' 02-configmap.yaml; then
  echo "--> 03-pvc.yaml"
  apply "03-pvc.yaml"
else
  echo "--> skipping 03-pvc.yaml (STORAGE_TYPE != local)"
fi
for f in 01-secrets.yaml 02-configmap.yaml 07-services.yaml \
         05-deployment-parsing-indexing-service.yaml \
         05b-deployment-kb-worker.yaml \
         04-deployment-main-service.yaml \
         08-routes.yaml 11-networkpolicy.yaml; do
  echo "--> $f"
  apply "$f"
done

# A restart makes already-running pods pick up a changed ConfigMap/Secret on a re-deploy with the same tag.
for d in parsing-indexing-service kb-worker main-service; do
  oc rollout restart deploy/$d -n "$NS" >/dev/null 2>&1 || true
done
for d in parsing-indexing-service kb-worker main-service; do
  echo "--> waiting for $d"
  oc rollout status deploy/$d -n "$NS" --timeout=900s
done

API=$(oc get route main-service -n "$NS" -o jsonpath='{.spec.host}')
UI=$(oc get route frontend -n "$NS" -o jsonpath='{.spec.host}')
cat <<EOF

Deployed.
  UI   : https://$UI
  API  : https://$API/docs

One-time steps (DISABLE_AUTH is False, so sign-in needs accounts):
  # an admin, and two ordinary users to show that conversations are private
  oc exec -n $NS deploy/main-service -- python scripts/create_superuser.py --email admin@example.com --first-name Admin --last-name User --password '<choose>' --role admin
  oc exec -n $NS deploy/main-service -- python scripts/create_superuser.py --email alice@example.com --first-name Alice --last-name A --password '<choose>' --role user
  oc exec -n $NS deploy/main-service -- python scripts/create_superuser.py --email bob@example.com --first-name Bob --last-name B --password '<choose>' --role user

  # only if this database already holds documents from before versions / duplicate detection:
  oc exec -n $NS deploy/main-service -- python scripts/maintenance.py versions-sync
  oc exec -n $NS deploy/main-service -- python scripts/maintenance.py backfill-hashes

Logs:  oc logs -f -n $NS deploy/main-service
       oc logs -f -n $NS deploy/parsing-indexing-service
       oc logs -f -n $NS deploy/kb-worker
EOF
