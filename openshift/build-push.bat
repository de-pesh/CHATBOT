@echo off
REM Builds the images and pushes them to Quay.
REM Run from CMD (not PowerShell) when Docker Desktop only works in CMD.
REM
REM   docker login quay-quay-registry.apps.dda-desc-az1-01.desccii.local
REM   openshift\build-push.bat v1                 :: all images
REM   openshift\build-push.bat v1 main-service    :: one: main-service | indexing | frontend | tika
REM
REM The frontend has the API address baked in at build time.
REM API_URL must match the main-service Route in openshift\08-routes.yaml.
REM
REM Optional env-var overrides (set before running, or prefix the command):
REM   REGISTRY          Quay registry prefix  (default: quay-quay-registry.../dda-desc)
REM   API_URL           main-service Route     (default: https://chatbot-api.apps.dda-desc-az1-01.desccii.local)
REM   BASE_REGISTRY     RH base image mirror   (default: registry.access.redhat.com)
REM   PIP_INDEX_URL     internal PyPI mirror   (default: empty)
REM   PIP_TRUSTED_HOST  internal PyPI host     (default: empty)
REM   NPM_REGISTRY      internal npm mirror    (default: empty)
REM   EPEL_RPM_URL      internal EPEL RPM URL  (default: Fedora CDN)
REM   NODE_IMAGE        UBI9 Node image        (default: Dockerfile default)
REM   NGINX_IMAGE       UBI9 nginx image       (default: Dockerfile default)
REM   TIKA_SOURCE       Tika source image      (default: apache/tika:4.0.0)

setlocal enabledelayedexpansion

IF "%~1"=="" (
    echo Usage: openshift\build-push.bat ^<image-tag^> [main-service^|indexing^|frontend^|tika]
    exit /b 1
)
SET TAG=%~1
SET ONLY=%~2
IF "%ONLY%"=="" SET ONLY=all

IF "%REGISTRY%"==""      SET REGISTRY=quay-quay-registry.apps.dda-desc-az1-01.desccii.local/dda-desc
IF "%API_URL%"==""       SET API_URL=https://chatbot-api.apps.dda-desc-az1-01.desccii.local
IF "%BASE_REGISTRY%"=="" SET BASE_REGISTRY=registry.access.redhat.com
IF "%EPEL_RPM_URL%"==""  SET EPEL_RPM_URL=https://dl.fedoraproject.org/pub/epel/epel-release-latest-9.noarch.rpm
IF "%TIKA_SOURCE%"==""   SET TIKA_SOURCE=apache/tika:4.0.0

REM Change to repo root (one level above the openshift\ folder)
CD /D "%~dp0\.."

REM ---- main-service -------------------------------------------------------
IF NOT "%ONLY%"=="main-service" IF NOT "%ONLY%"=="all" GOTO skip_main
echo.
echo ==^> %REGISTRY%/chatbot-main-service:%TAG%
SET BARGS=--build-arg BASE_REGISTRY=%BASE_REGISTRY%
IF NOT "%PIP_INDEX_URL%"==""    SET BARGS=%BARGS% --build-arg PIP_INDEX_URL=%PIP_INDEX_URL%
IF NOT "%PIP_TRUSTED_HOST%"=="" SET BARGS=%BARGS% --build-arg PIP_TRUSTED_HOST=%PIP_TRUSTED_HOST%
docker build --platform linux/amd64 -t %REGISTRY%/chatbot-main-service:%TAG% %BARGS% main-service-chatbot
IF ERRORLEVEL 1 ( echo BUILD FAILED: chatbot-main-service & exit /b 1 )
docker push %REGISTRY%/chatbot-main-service:%TAG%
IF ERRORLEVEL 1 ( echo PUSH FAILED: chatbot-main-service & exit /b 1 )
:skip_main

REM ---- parsing-indexing-service (+ kb-worker, same image) -----------------
IF NOT "%ONLY%"=="indexing" IF NOT "%ONLY%"=="all" GOTO skip_indexing
echo.
echo ==^> %REGISTRY%/chatbot-parsing-indexing-service:%TAG%
SET BARGS=--build-arg BASE_REGISTRY=%BASE_REGISTRY% --build-arg EPEL_RPM_URL=%EPEL_RPM_URL% --build-arg EMBEDDING_MODE=remote --build-arg OCR_MODE=optional
IF NOT "%PIP_INDEX_URL%"==""    SET BARGS=%BARGS% --build-arg PIP_INDEX_URL=%PIP_INDEX_URL%
IF NOT "%PIP_TRUSTED_HOST%"=="" SET BARGS=%BARGS% --build-arg PIP_TRUSTED_HOST=%PIP_TRUSTED_HOST%
docker build --platform linux/amd64 -t %REGISTRY%/chatbot-parsing-indexing-service:%TAG% %BARGS% parsing-indexing-service
IF ERRORLEVEL 1 ( echo BUILD FAILED: chatbot-parsing-indexing-service & exit /b 1 )
docker push %REGISTRY%/chatbot-parsing-indexing-service:%TAG%
IF ERRORLEVEL 1 ( echo PUSH FAILED: chatbot-parsing-indexing-service & exit /b 1 )
:skip_indexing

REM ---- frontend -----------------------------------------------------------
IF NOT "%ONLY%"=="frontend" GOTO skip_frontend
echo.
echo ==^> %REGISTRY%/chatbot-frontend:%TAG%
SET BARGS=--build-arg BASE_REGISTRY=%BASE_REGISTRY% --build-arg BUILD_SCRIPT=build:local --build-arg VITE_API_BASE_URL=%API_URL%
IF NOT "%NODE_IMAGE%"==""   SET BARGS=%BARGS% --build-arg NODE_IMAGE=%NODE_IMAGE%
IF NOT "%NGINX_IMAGE%"==""  SET BARGS=%BARGS% --build-arg NGINX_IMAGE=%NGINX_IMAGE%
IF NOT "%NPM_REGISTRY%"=="" SET BARGS=%BARGS% --build-arg NPM_REGISTRY=%NPM_REGISTRY%
docker build --platform linux/amd64 -t %REGISTRY%/chatbot-frontend:%TAG% %BARGS% FE
IF ERRORLEVEL 1 ( echo BUILD FAILED: chatbot-frontend & exit /b 1 )
docker push %REGISTRY%/chatbot-frontend:%TAG%
IF ERRORLEVEL 1 ( echo PUSH FAILED: chatbot-frontend & exit /b 1 )
:skip_frontend

REM ---- tika (copy into Quay so cluster needs no internet access) ----------
IF NOT "%ONLY%"=="tika" GOTO skip_tika
echo.
echo ==^> %REGISTRY%/tika:4.0.0
docker pull --platform linux/amd64 %TIKA_SOURCE%
IF ERRORLEVEL 1 ( echo PULL FAILED: %TIKA_SOURCE% & exit /b 1 )
docker tag %TIKA_SOURCE% %REGISTRY%/tika:4.0.0
docker push %REGISTRY%/tika:4.0.0
IF ERRORLEVEL 1 ( echo PUSH FAILED: tika & exit /b 1 )
:skip_tika

echo.
echo Pushed tag %TAG%.
echo Next:  oc login ^<api-url^>  then  openshift\deploy.ps1 %TAG%
endlocal
