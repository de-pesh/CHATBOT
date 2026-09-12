# Deployment

Everything for OpenShift is in `../deploy/` - three files:

    manifests.yaml   the whole deployment in one file
    build.sh         builds and pushes the images
    README.md

    oc apply -n ai-common -f ../deploy/manifests.yaml

`<service>/k8s/legacy-aks/` holds the retired AKS manifests (nginx Ingress,
NodePort Services) for reference only.

> **Stale.** `../deploy/manifests.yaml` still declares NEO4J_* keys and
> Deployments for the five services removed from this repo, and `build.sh`
> still expects their build contexts. Both need reconciling with the three
> images compose now builds: main-service, indexing-rpc-server and the
> frontend. chatbot-inferencing-service has since been merged into
> main-service, so that Deployment must go too.

The Azure DevOps pipelines in each service targeted AKS and ACR and do not
apply to the OpenShift deployment.
