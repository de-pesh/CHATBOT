@echo off
REM Deploys the chatbot to OpenShift from CMD.
REM
REM   oc login <api-url>
REM   openshift\build-push.bat v1
REM   openshift\deploy.bat v1
REM
REM Usage: openshift\deploy.bat <image-tag> [namespace]
REM Namespace defaults to ai-dca.

setlocal enabledelayedexpansion

IF "%~1"=="" (
    echo Usage: openshift\deploy.bat ^<image-tag^> [namespace]
    exit /b 1
)
SET TAG=%~1
SET NS=%~2
IF "%NS%"=="" SET NS=ai-dca

REM Work from the openshift\ folder so all file paths are relative to it.
CD /D "%~dp0"

where oc >nul 2>&1
IF ERRORLEVEL 1 ( echo ERROR: oc is not installed. & exit /b 1 )
oc whoami >nul 2>&1
IF ERRORLEVEL 1 ( echo ERROR: Not logged in. Run:  oc login ^<api-url^> & exit /b 1 )

REM ---- placeholder check (skip comment lines) ---------------------------------
SET _BAD=0
FOR %%F IN (01-secrets.yaml 02-configmap.yaml 08-routes.yaml 11-networkpolicy.yaml) DO (
    FOR /F "usebackq tokens=*" %%L IN (`findstr /r "<[A-Z][A-Z0-9_]*>" "%%F" 2^>nul`) DO (
        echo %%L | findstr /b " *#" >nul 2>&1 || (
            echo PLACEHOLDER still set in %%F: %%L
            SET _BAD=1
        )
    )
)
IF "%_BAD%"=="1" ( echo Fill in the placeholders above, then re-run. & exit /b 1 )

REM ---- namespace --------------------------------------------------------------
oc get project %NS% >nul 2>&1
IF ERRORLEVEL 1 ( oc new-project %NS% >nul )
echo Namespace: %NS%   Tag: %TAG%

oc get secret quay-pull-secret -n %NS% >nul 2>&1
IF ERRORLEVEL 1 (
    echo.
    echo WARNING: quay-pull-secret not found in %NS% - pods cannot pull images from Quay.
    echo Create it with:
    echo   oc create secret docker-registry quay-pull-secret -n %NS% ^
    echo     --docker-server=quay-quay-registry.apps.dda-desc-az1-01.desccii.local ^
    echo     --docker-username=dda-desc+testbot ^
    echo     --docker-password=^<robot-token^>
    echo.
)

REM ---- skip PVC when STORAGE_TYPE != local ------------------------------------
findstr /c:"STORAGE_TYPE: ""local""" 02-configmap.yaml >nul 2>&1
IF NOT ERRORLEVEL 1 (
    CALL :apply 03-pvc.yaml
) ELSE (
    echo --^> skipping 03-pvc.yaml  ^(STORAGE_TYPE=quay^)
)

REM ---- apply manifests in order -----------------------------------------------
CALL :apply 01-secrets.yaml
CALL :apply 02-configmap.yaml
CALL :apply 07-services.yaml
CALL :apply 05-deployment-parsing-indexing-service.yaml
CALL :apply 05b-deployment-kb-worker.yaml
CALL :apply 04-deployment-main-service.yaml
CALL :apply 08-routes.yaml
CALL :apply 11-networkpolicy.yaml

REM ---- restart pods so config changes are picked up on a same-tag redeploy ---
FOR %%D IN (parsing-indexing-service kb-worker main-service) DO (
    oc rollout restart deploy/%%D -n %NS% >nul 2>&1
)

REM ---- wait for rollouts ------------------------------------------------------
FOR %%D IN (parsing-indexing-service kb-worker main-service) DO (
    echo --^> waiting for %%D ...
    oc rollout status deploy/%%D -n %NS% --timeout=900s
    IF ERRORLEVEL 1 ( echo ROLLOUT FAILED: %%D & exit /b 1 )
)

REM ---- print result -----------------------------------------------------------
FOR /F %%H IN ('oc get route main-service -n %NS% -o jsonpath={.spec.host} 2^>nul') DO SET _API=%%H
FOR /F %%H IN ('oc get route frontend     -n %NS% -o jsonpath={.spec.host} 2^>nul') DO SET _UI=%%H
echo.
echo Deployed.
echo   UI  : https://%_UI%
echo   API : https://%_API%/docs
echo.
echo One-time: create user accounts (only needed on a fresh namespace):
echo   oc exec -n %NS% deploy/main-service -- python scripts/create_superuser.py --email admin@example.com --first-name Admin --last-name User --password ^<choose^> --role admin
echo.
GOTO :eof

REM ---- subroutine: apply one manifest, substituting ^<TAG^> --------------------
:apply
    echo --^> %~1
    REM powershell is used here only for the ^<TAG^> text substitution; the result
    REM is piped straight to oc apply. This is an inline command, not a .ps1 file.
    powershell -NoProfile -Command "(Get-Content '%~1') -replace '<TAG>','%TAG%' | oc apply -n %NS% -f -"
    IF ERRORLEVEL 1 ( echo FAILED: %~1 & exit /b 1 )
    GOTO :eof
