@echo off
::
:: setup/setup.bat
:: Validates the install directory and imports the dynamically-generated
:: setup.reg into the current user's registry (HKCU).
::
:: This script is called by installer.ps1 after it has generated setup.reg.
:: It should not be run directly by the user in normal circumstances.
::
:: No paths are hardcoded.  No PATH modifications are made.
::

setlocal

:: -- Locate setup directory and parent (install directory) ------------------
::
:: %~dp0 always ends with a backslash.  No strip is performed -- stripping the
:: backslash from a drive-root path (e.g. C:\) would produce C:, a relative
:: reference rather than an absolute one.  The for /f canonicalization below
:: handles the trailing backslash correctly without stripping.
::
set "SETUP_DIR=%~dp0"

:: Resolve parent directory (the install root, one level above setup/).
::
:: for /f "delims=" %%i in ("...") processes a literal string rather than a
:: file or command.  %%~fi expands to the fully-qualified canonical path,
:: resolving the trailing "..\" into the actual parent directory.  This is
:: less conventional than a plain "for %%i in (...)" loop but produces the
:: same result and is safe on normal local NTFS paths.  If path forms
:: containing special characters such as &, (, ), ^, or UNC paths are ever
:: used, this line should be tested explicitly -- those forms are not
:: exercised by the standard installer.
::
for /f "delims=" %%i in ("%SETUP_DIR%..") do set "INSTALL_DIR=%%~fi"

:: -- Validate required files exist ------------------------------------------
if not exist "%INSTALL_DIR%\shuffles.bat" (
    echo ERROR: shuffles.bat not found in expected install directory:
    echo        %INSTALL_DIR%
    echo.
    echo This script must be run from within the setup\ subfolder of the
    echo Shuffles install directory.  Do not move setup.bat out of setup\.
    exit /b 1
)

if not exist "%INSTALL_DIR%\shuffles.py" (
    echo ERROR: shuffles.py not found in expected install directory:
    echo        %INSTALL_DIR%
    exit /b 1
)

:: -- Validate setup.reg was generated ---------------------------------------
set "REG_FILE=%SETUP_DIR%setup.reg"
if not exist "%REG_FILE%" (
    echo ERROR: setup.reg not found at:
    echo        %REG_FILE%
    echo.
    echo Run installer.ps1 first -- it generates setup.reg before calling this script.
    exit /b 1
)

:: -- Import registry entries ------------------------------------------------
echo Importing registry entries for install directory:
echo   %INSTALL_DIR%
echo.

:: reg import silently overwrites any existing keys with the same paths.
:: This is intentional: re-running the installer as a repair operation should
:: always bring the registry back to the correct state.
::
:: KNOWN LIMITATION -- HKCU under elevation:
:: If installer.ps1 is run elevated (e.g. via "Run as administrator"), reg
:: import targets the elevated session's HKCU hive.  Under some UAC
:: configurations this may differ from the standard user's hive, causing the
:: context menu entries to appear for the elevated account only.  The
:: installer is designed for non-elevated use; if entries do not appear after
:: install, verify that the installer was not run elevated.
::
reg import "%REG_FILE%"

:: %errorlevel% is checked immediately after reg import on the next line,
:: outside any parenthesized block.  This is correct: expansion happens at
:: execution time here, not at parse time.  Do not "fix" this by adding
:: delayed expansion -- it is not needed and the current form is reliable.
if %errorlevel% neq 0 (
    echo ERROR: reg import failed (exit code %errorlevel%).
    exit /b 1
)

echo.
echo Registry entries imported successfully.
echo Right-click inside any folder to see the new context menu entries.
echo.
echo NOTE: If Explorer does not show the new entries immediately,
echo       sign out and back in, or restart Explorer.
::
:: No pause here.  When called by installer.ps1 (via Start-Process -Wait),
:: a pause would block indefinitely waiting for a keypress that never comes.
:: The installer's own success dialog handles user notification.  Standalone
:: users will see the output above before the console window closes.