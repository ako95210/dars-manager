@echo off
setlocal EnableExtensions

title Installation de Dars Manager

set "SOURCE_DIR=%~dp0"
for %%I in ("%SOURCE_DIR%.") do set "SOURCE_DIR=%%~fI"
set "INSTALL_DIR=%LOCALAPPDATA%\DarsManager\App"
set "DATA_DIR=%USERPROFILE%\Documents\DarsManager"
set "DESKTOP_DIR=%USERPROFILE%\Desktop"
set "LAUNCHER=%DESKTOP_DIR%\Dars Manager.bat"

echo.
echo Installation de Dars Manager
echo ----------------------------
echo.

where py >nul 2>nul
if not errorlevel 1 (
  set "HAS_PYTHON=1"
) else (
  where python >nul 2>nul
  if not errorlevel 1 (
    set "HAS_PYTHON=1"
  ) else (
    set "HAS_PYTHON=0"
  )
)

if "%HAS_PYTHON%"=="0" (
  echo Python 3 est necessaire.
  echo Une page de telechargement va s'ouvrir.
  start "" "https://www.python.org/downloads/windows/"
  echo Installez Python, puis relancez install_windows.bat.
  pause
  exit /b 1
)

if not exist "%DATA_DIR%" mkdir "%DATA_DIR%"

echo Copie de l'application dans:
echo %INSTALL_DIR%
echo.

if /I not "%SOURCE_DIR%"=="%INSTALL_DIR%" (
  if exist "%INSTALL_DIR%" rmdir /S /Q "%INSTALL_DIR%"
  mkdir "%INSTALL_DIR%"
  xcopy "%SOURCE_DIR%\*" "%INSTALL_DIR%\" /E /I /Y /Q >nul
  if errorlevel 1 (
    echo Echec de copie de l'application.
    pause
    exit /b 1
  )
)

echo Creation du raccourci sur le Bureau...
(
  echo @echo off
  echo cd /d "%INSTALL_DIR%"
  echo call drsm_windows.bat
) > "%LAUNCHER%"

echo Preparation des composants Python...
call "%INSTALL_DIR%\drsm_windows.bat" --prepare
if errorlevel 1 (
  echo L'installation n'a pas pu se terminer.
  pause
  exit /b 1
)

echo.
echo Installation terminee.
echo Raccourci cree:
echo %LAUNCHER%
echo.
echo Lancement de Dars Manager...
echo.

call "%INSTALL_DIR%\drsm_windows.bat"
