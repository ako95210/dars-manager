@echo off
setlocal EnableExtensions

set "APP_DIR=%~dp0"
cd /d "%APP_DIR%"

set "VENV_DIR=%APP_DIR%.venv"
if not defined DRSM_WORK_DIR set "DRSM_WORK_DIR=%USERPROFILE%\Documents\DarsManager"
set "HF_HOME=%DRSM_WORK_DIR%\hf_cache"
set "XDG_CACHE_HOME=%DRSM_WORK_DIR%\cache"
set "DRSM_CLOUD_SAFE_DEFAULT=false"

where py >nul 2>nul
if not errorlevel 1 (
  set "PY_CMD=py -3"
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo Python 3 est introuvable.
    echo Une page de telechargement va s'ouvrir.
    start "" "https://www.python.org/downloads/windows/"
    echo Installez Python, puis relancez ce fichier.
    pause
    exit /b 1
  )
  set "PY_CMD=python"
)

if not exist "%DRSM_WORK_DIR%" mkdir "%DRSM_WORK_DIR%"

if not exist "%VENV_DIR%\Scripts\python.exe" (
  echo Creation de l'environnement Python...
  %PY_CMD% -m venv "%VENV_DIR%"
  if errorlevel 1 (
    echo Impossible de creer l'environnement Python.
    pause
    exit /b 1
  )
)

if not exist "%VENV_DIR%\.drsm_ready" (
  echo Installation des composants...
  "%VENV_DIR%\Scripts\python.exe" -m pip install --upgrade pip
  if errorlevel 1 (
    echo Echec de mise a jour de pip.
    pause
    exit /b 1
  )
  "%VENV_DIR%\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Echec d'installation des composants.
    pause
    exit /b 1
  )
  echo ready>"%VENV_DIR%\.drsm_ready"
)

if "%~1"=="--prepare" exit /b 0

echo.
echo Dars Manager demarre...
echo Donnees locales: %DRSM_WORK_DIR%
echo Adresse: http://localhost:8501
echo.

start "" "http://localhost:8501"
"%VENV_DIR%\Scripts\python.exe" -m streamlit run drsm_streamlit.py --server.address=localhost --server.port=8501

echo.
echo Dars Manager s'est arrete.
pause
