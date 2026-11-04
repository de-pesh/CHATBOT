# Deploys the chatbot to OpenShift: everything in openshift/, in the right order, waiting for each pod.
#
#   oc login <api-url>
#   .\openshift\build-push.ps1 v1        # on a machine with docker (once per release)
#   .\openshift\deploy.ps1 v1            # on a machine with oc
#
# Usage: deploy.ps1 <image-tag> [namespace]     (namespace defaults to ai-dca)
# Re-running is safe; to roll out a new release, build-push a new tag and run deploy.ps1 with it.

param(
    [Parameter(Mandatory)][string]$Tag,
    [string]$Namespace = "ai-dca"
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command oc -ErrorAction SilentlyContinue)) {
    Write-Error "oc is not installed on this machine."; exit 1
}
oc whoami 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Error "Not logged in: run  oc login <api-url>  first."; exit 1
}

Set-Location (Split-Path -Parent $MyInvocation.MyCommand.Path)

# Refuse to deploy with a value still to fill in.
$checkFiles = @("01-secrets.yaml","02-configmap.yaml","08-routes.yaml","11-networkpolicy.yaml")
$left = @()
foreach ($f in $checkFiles) {
    if (Test-Path $f) {
        $lines = Get-Content $f
        for ($i = 0; $i -lt $lines.Count; $i++) {
            if ($lines[$i] -match '<[A-Z][A-Z0-9_]*>' -and $lines[$i] -notmatch '^\s*#') {
                $left += "${f}:$($i+1): $($lines[$i])"
            }
        }
    }
}
if ($left.Count -gt 0) {
    Write-Host "These values are still placeholders - fill them in, then run again:"
    $left | ForEach-Object { $_ -replace '(Password=)[^;"]*', '$1***' }
    exit 1
}

oc get project $Namespace 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { oc new-project $Namespace | Out-Null }
Write-Host "Namespace: $Namespace   Tag: $Tag"

oc get secret quay-pull-secret -n $Namespace 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Secret 'quay-pull-secret' does not exist in $Namespace - pods cannot pull from Quay. Create it first:"
    Write-Warning "  oc create secret docker-registry quay-pull-secret -n $Namespace --docker-server=<quay-host> --docker-username=<user> --docker-password=<password>"
}

function Apply-Manifest($file) {
    Write-Host "--> $file"
    (Get-Content $file -Raw) -replace '<TAG>', $Tag | oc apply -n $Namespace -f -
}

# Skip the PVC when STORAGE_TYPE=quay (no shared volume needed).
$configmapContent = Get-Content "02-configmap.yaml" -Raw
if ($configmapContent -match 'STORAGE_TYPE:\s*"local"') {
    Apply-Manifest "03-pvc.yaml"
} else {
    Write-Host "--> skipping 03-pvc.yaml (STORAGE_TYPE != local)"
}
foreach ($f in @(
    "01-secrets.yaml", "02-configmap.yaml", "07-services.yaml",
    "05-deployment-parsing-indexing-service.yaml",
    "05b-deployment-kb-worker.yaml",
    "04-deployment-main-service.yaml",
    "08-routes.yaml", "11-networkpolicy.yaml"
)) { Apply-Manifest $f }

# Restart already-running pods so they pick up a changed ConfigMap/Secret on a re-deploy with the same tag.
foreach ($d in @("parsing-indexing-service","kb-worker","main-service")) {
    oc rollout restart deploy/$d -n $Namespace 2>$null | Out-Null
}
foreach ($d in @("parsing-indexing-service","kb-worker","main-service")) {
    Write-Host "--> waiting for $d"
    oc rollout status deploy/$d -n $Namespace --timeout=900s
}

$API = oc get route main-service -n $Namespace -o jsonpath='{.spec.host}'
$UI  = oc get route frontend     -n $Namespace -o jsonpath='{.spec.host}'

Write-Host @"

Deployed.
  UI   : https://$UI
  API  : https://$API/docs

One-time steps (DISABLE_AUTH is False, so sign-in needs accounts):
  oc exec -n $Namespace deploy/main-service -- python scripts/create_superuser.py --email admin@example.com --first-name Admin --last-name User --password '<choose>' --role admin
  oc exec -n $Namespace deploy/main-service -- python scripts/create_superuser.py --email alice@example.com --first-name Alice --last-name A --password '<choose>' --role user
  oc exec -n $Namespace deploy/main-service -- python scripts/create_superuser.py --email bob@example.com --first-name Bob --last-name B --password '<choose>' --role user

  # only if this database already held documents before version tracking was added:
  oc exec -n $Namespace deploy/main-service -- python scripts/maintenance.py versions-sync
  oc exec -n $Namespace deploy/main-service -- python scripts/maintenance.py backfill-hashes

Logs:  oc logs -f -n $Namespace deploy/main-service
       oc logs -f -n $Namespace deploy/parsing-indexing-service
       oc logs -f -n $Namespace deploy/kb-worker
"@
