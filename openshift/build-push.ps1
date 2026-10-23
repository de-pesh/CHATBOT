# Builds the images and pushes them to Quay. Run on a machine with Docker that can reach the registry.
#
#   docker login quay-quay-registry.apps.dda-desc-az1-01.desccii.local
#   .\openshift\build-push.ps1 v1               # all of them
#   .\openshift\build-push.ps1 v1 main-service  # one: main-service | indexing | frontend | tika
#
# The frontend has the API address baked in at build time: $API_URL must be the main-service route in 08-routes.yaml.

param(
    [Parameter(Mandatory)][string]$Tag,
    [string]$Only = "all"
)

$ErrorActionPreference = "Stop"

$REGISTRY    = if ($env:REGISTRY)          { $env:REGISTRY }          else { "quay-quay-registry.apps.dda-desc-az1-01.desccii.local/dda-desc" }
$API_URL     = if ($env:API_URL)           { $env:API_URL }           else { "https://chatbot-api.apps.desc-bot.desc.local" }
# On a network with no internet, point these at internal mirrors (leave empty otherwise).
$BASE_REGISTRY   = if ($env:BASE_REGISTRY)    { $env:BASE_REGISTRY }    else { "registry.access.redhat.com" }
$PIP_INDEX_URL   = if ($env:PIP_INDEX_URL)    { $env:PIP_INDEX_URL }    else { "" }
$PIP_TRUSTED_HOST= if ($env:PIP_TRUSTED_HOST) { $env:PIP_TRUSTED_HOST } else { "" }
$NPM_REGISTRY    = if ($env:NPM_REGISTRY)     { $env:NPM_REGISTRY }     else { "" }
$EPEL_RPM_URL    = if ($env:EPEL_RPM_URL)     { $env:EPEL_RPM_URL }     else { "https://dl.fedoraproject.org/pub/epel/epel-release-latest-9.noarch.rpm" }
$NODE_IMAGE      = if ($env:NODE_IMAGE)       { $env:NODE_IMAGE }       else { "" }
$NGINX_IMAGE     = if ($env:NGINX_IMAGE)      { $env:NGINX_IMAGE }      else { "" }
$TIKA_SOURCE     = if ($env:TIKA_SOURCE)      { $env:TIKA_SOURCE }      else { "apache/tika:4.0.0" }

Set-Location (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "..")

function Want($name) { $Only -eq "all" -or $Only -eq $name }

function Build-And-Push([string]$name, [string]$ctx, [string[]]$extra) {
    Write-Host "==> $REGISTRY/${name}:$Tag"
    # OpenShift nodes are x86_64: a silently arm64 image dies with "exec format error".
    $buildArgs = @("build", "--platform", "linux/amd64", "-t", "$REGISTRY/${name}:$Tag") + $extra + @($ctx)
    & docker @buildArgs
    if ($LASTEXITCODE -ne 0) { throw "docker build failed for $name" }
    docker push "$REGISTRY/${name}:$Tag"
    if ($LASTEXITCODE -ne 0) { throw "docker push failed for $name" }
}

if (Want "main-service") {
    $a = @("--build-arg","BASE_REGISTRY=$BASE_REGISTRY")
    if ($PIP_INDEX_URL)    { $a += "--build-arg","PIP_INDEX_URL=$PIP_INDEX_URL" }
    if ($PIP_TRUSTED_HOST) { $a += "--build-arg","PIP_TRUSTED_HOST=$PIP_TRUSTED_HOST" }
    Build-And-Push "chatbot-main-service" "main-service-chatbot" $a
}

# EMBEDDING_MODE=remote: the embedding model is called over the LiteLLM proxy, so no torch / model is baked in.
if (Want "indexing") {
    $a = @("--build-arg","BASE_REGISTRY=$BASE_REGISTRY","--build-arg","EPEL_RPM_URL=$EPEL_RPM_URL",
           "--build-arg","EMBEDDING_MODE=remote","--build-arg","OCR_MODE=optional")
    if ($PIP_INDEX_URL)    { $a += "--build-arg","PIP_INDEX_URL=$PIP_INDEX_URL" }
    if ($PIP_TRUSTED_HOST) { $a += "--build-arg","PIP_TRUSTED_HOST=$PIP_TRUSTED_HOST" }
    Build-And-Push "chatbot-parsing-indexing-service" "parsing-indexing-service" $a
}

if (Want "frontend") {
    $a = @("--build-arg","BASE_REGISTRY=$BASE_REGISTRY",
           "--build-arg","BUILD_SCRIPT=build:local",
           "--build-arg","VITE_API_BASE_URL=$API_URL")
    if ($NODE_IMAGE)    { $a += "--build-arg","NODE_IMAGE=$NODE_IMAGE" }
    if ($NGINX_IMAGE)   { $a += "--build-arg","NGINX_IMAGE=$NGINX_IMAGE" }
    if ($NPM_REGISTRY)  { $a += "--build-arg","NPM_REGISTRY=$NPM_REGISTRY" }
    Build-And-Push "chatbot-frontend" "FE" $a
}

# Tika is someone else's image: copy it into Quay so the cluster does not need internet.
# The tag is fixed (4.0.0), not $Tag: 09-deployment-tika.yaml refers to it by that.
if (Want "tika") {
    Write-Host "==> $REGISTRY/tika:4.0.0"
    docker pull --platform linux/amd64 $TIKA_SOURCE
    docker tag $TIKA_SOURCE "$REGISTRY/tika:4.0.0"
    docker push "$REGISTRY/tika:4.0.0"
}

Write-Host "Pushed tag $Tag. Next:  .\openshift\deploy.ps1 $Tag"
