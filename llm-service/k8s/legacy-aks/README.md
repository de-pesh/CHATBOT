Manifests from the AKS deployment, kept for reference. They are NOT applied on
OpenShift and `oc apply -f k8s/` skips this directory (it is not recursive).

- `ingress.yaml` - relied on the NGINX ingress controller and its
  `rewrite-target` annotation. OpenShift's HAProxy router cannot strip a path
  prefix, so each HTTP service uses a host-based `route.yaml` instead.
- `service.yaml` - this service is a Redis queue worker; it binds no
  port, so the Service could never have healthy endpoints (it also asked for a
  NodePort, which OpenShift restricts).
