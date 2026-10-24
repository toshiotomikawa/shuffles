@echo off
rem Installed interpreter is pinned so Explorer's inherited PATH is irrelevant.
rem Console Python keeps --quiet JSON output and exit status available to callers.
setlocal
set "PY_SCRIPT=%~dp0shuffles.py"
set "PYTHON_EXE="
if exist "%~dp0python-path.txt" set /p PYTHON_EXE=<"%~dp0python-path.txt"
if defined PYTHON_EXE goto :validate
for %%P in (python.exe) do set "PYTHON_EXE=%%~$PATH:P"
:validate
if not defined PYTHON_EXE (
    echo Shuffles: Python unavailable. Run the installer to repair interpreter configuration. 1>&2
    exit /b 1
)
if not exist "%PYTHON_EXE%" (
    echo Shuffles: configured Python is missing. Run the installer to refresh its location. 1>&2
    exit /b 1
)
if not exist "%PY_SCRIPT%" (
    echo Shuffles: shuffles.py is missing beside this launcher. 1>&2
    exit /b 1
)
"%PYTHON_EXE%" "%PY_SCRIPT%" %*
exit /b %errorlevel%