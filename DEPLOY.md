# Deployment

Everything for OpenShift is in `../deploy/` - three files:

    manifests.yaml   the whole deployment in one file
    build.sh         builds and pushes the nine images
    README.md

    oc apply -n ai-common -f ../deploy/manifests.yaml

`<service>/k8s/legacy-aks/` holds the retired AKS manifests (nginx Ingress,
NodePort Services) for reference only. The Azure DevOps pipelines in each
service targeted AKS and ACR and do not apply to the OpenShift deployment.
