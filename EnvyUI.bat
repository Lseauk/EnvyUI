@echo off
cd /d "%~dp0"

:: EnvyUI uses SYSTEM Python (for PyQt6), NOT the TwinVine venv.
:: The venv is only used internally by envied when it runs as a subprocess.

:: Find a real system Python (skip 0-byte Microsoft Store stubs)
set "PYTHON="

for /f "delims=" %%i in ('where pythonw 2^>nul') do (
    if "%%i" neq "" (
        for %%S in ("%%i") do (
            if %%~zS gtr 0 (
                if not defined PYTHON set "PYTHON=%%i"
            )
        )
    )
)
if defined PYTHON goto :have_python

for /f "delims=" %%i in ('where python 2^>nul') do (
    if "%%i" neq "" (
        for %%S in ("%%i") do (
            if %%~zS gtr 0 (
                if not defined PYTHON set "PYTHON=%%i"
            )
        )
    )
)
if defined PYTHON goto :have_python

echo.
echo  ERROR: No Python installation found.
echo  The Microsoft Store Python stub cannot run EnvyUI.
echo.
echo  Please install Python from: https://www.python.org/downloads/
echo  Tick "Add Python to PATH" during installation.
echo.
pause
exit /b 1

:have_python
:: Auto-install PyQt6 into system Python if missing
"%PYTHON%" -c "import PyQt6" >nul 2>&1
if errorlevel 1 (
    echo Installing PyQt6 - please wait...
    "%PYTHON%" -m pip install PyQt6 --quiet
    if errorlevel 1 "%PYTHON%" -m pip install PyQt6 --user --quiet
)

:: Auto-install certifi so SSL certificates work on fresh Windows installs
"%PYTHON%" -c "import certifi" >nul 2>&1
if errorlevel 1 (
    echo Installing certifi - please wait...
    "%PYTHON%" -m pip install certifi --quiet
    if errorlevel 1 "%PYTHON%" -m pip install certifi --user --quiet
)

:: Auto-install pywinpty for real-time terminal output (live download progress)
"%PYTHON%" -c "import winpty" >nul 2>&1
if errorlevel 1 (
    echo Installing pywinpty - please wait...
    "%PYTHON%" -m pip install pywinpty --quiet
    if errorlevel 1 "%PYTHON%" -m pip install pywinpty --user --quiet
)

:: Auto-install PyQt6-WebEngine for the in-app terminal panel
"%PYTHON%" -c "import PyQt6.QtWebEngineWidgets" >nul 2>&1
if errorlevel 1 (
    echo Installing PyQt6-WebEngine - please wait...
    "%PYTHON%" -m pip install PyQt6-WebEngine --quiet
    if errorlevel 1 "%PYTHON%" -m pip install PyQt6-WebEngine --user --quiet
)


:: Auto-install uv if not on PATH (needed to run envied)
where uv >nul 2>&1
if errorlevel 1 (
    "%PYTHON%" -m uv --version >nul 2>&1
    if errorlevel 1 (
        echo Installing uv - please wait...
        "%PYTHON%" -m pip install uv --quiet
        if errorlevel 1 "%PYTHON%" -m pip install uv --user --quiet
    )
)

start "" /b "%PYTHON%" "%~dp0envy_launcher.py"
