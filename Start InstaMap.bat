@echo off
setlocal EnableExtensions
title InstaMap
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

rem Bump this when the app changes so the next double-click reinstalls it.
set "APPVERSION=0.2.0"
set "APPDIR=%LOCALAPPDATA%\InstaMap"
set "VENV=%APPDIR%\venv"
set "VPY=%VENV%\Scripts\python.exe"
set "STAMP=%APPDIR%\installed.txt"

echo.
echo   InstaMap
echo   --------
echo.

if not exist "%~dp0pyproject.toml" goto not_extracted

if not exist "%VPY%" goto setup
findstr /x /c:"%APPVERSION%" "%STAMP%" >nul 2>nul
if errorlevel 1 goto install
goto run

:setup
call :find_python
if defined PY goto make_venv
echo   Python isn't on this PC yet. Installing it now...
where winget >nul 2>nul
if errorlevel 1 goto no_winget
winget install -e --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements
call :find_python
if not defined PY goto python_failed

:make_venv
if not exist "%APPDIR%" mkdir "%APPDIR%"
echo   Using Python at %PY%
"%PY%" -m venv "%VENV%"
if not exist "%VPY%" goto python_failed

:install
echo.
echo   Setting up InstaMap. The first time this takes 5 to 15 minutes
echo   (it downloads the speech and text-reading tools). Later starts are instant.
echo.
"%VPY%" -m pip install --upgrade pip --disable-pip-version-check -q
"%VPY%" -m pip install --disable-pip-version-check ".[ml]"
if errorlevel 1 goto pip_failed
"%VPY%" -m spacy download en_core_web_sm --disable-pip-version-check -q
> "%STAMP%" echo %APPVERSION%

:run
echo.
echo   InstaMap is starting and will open in your web browser.
echo   Leave this window open while it works. You can minimise it.
echo   Close this window to stop InstaMap.
echo.
"%VPY%" -m instamap serve
echo.
echo   InstaMap has stopped.
pause
exit /b 0

rem ---------------------------------------------------------------------------
:find_python
set "PY="
for %%V in (3.12 3.11 3.13 3.10) do call :try_py %%V
if defined PY exit /b 0
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if defined PY exit /b 0
for /f "delims=" %%P in ('python -c "import sys; assert sys.version_info[1] >= 10; print(sys.executable)" 2^>nul') do set "PY=%%P"
exit /b 0

:try_py
if defined PY exit /b 0
for /f "delims=" %%P in ('py -%1 -c "import sys; print(sys.executable)" 2^>nul') do set "PY=%%P"
exit /b 0

:not_extracted
echo   It looks like this was opened from inside the zip file.
echo   Right-click the zip, choose "Extract All...", open the extracted folder,
echo   then double-click "Start InstaMap" there.
echo.
pause
exit /b 1

:no_winget
echo.
echo   This PC can't install Python automatically.
echo   Please install it from https://www.python.org/downloads/
echo   (tick "Add python.exe to PATH" on the first screen), then double-click this file again.
echo.
start "" "https://www.python.org/downloads/"
pause
exit /b 1

:python_failed
echo.
echo   Python was installed but couldn't be found yet.
echo   Close this window and double-click "Start InstaMap" again.
echo.
pause
exit /b 1

:pip_failed
echo.
echo   Setup didn't finish. Check you're connected to the internet,
echo   then double-click "Start InstaMap" again. It carries on where it stopped.
echo   If it keeps failing, send a screenshot of this window.
echo.
pause
exit /b 1
