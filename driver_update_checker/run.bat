@echo off
REM ============================================================
REM  Driver Update Checker - Launcher
REM  Finds a real Python install, verifies deps, runs the app.
REM ============================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"

set PYCMD=

REM --- Prefer the py launcher: it never collides with the
REM --- Microsoft Store alias stub.
py --version >nul 2>&1
if %errorlevel% equ 0 (
    set PYCMD=py
    goto :found
)

REM --- Fall back to python, but confirm it is real and not the
REM --- Store redirect stub. The stub exits nonzero on --version.
python --version >nul 2>&1
if %errorlevel% equ 0 (
    set PYCMD=python
    goto :found
)

REM --- Last resort: probe common install locations directly.
for %%P in (
    "%LocalAppData%\Programs\Python\Python313\python.exe"
    "%LocalAppData%\Programs\Python\Python312\python.exe"
    "%LocalAppData%\Programs\Python\Python311\python.exe"
    "%LocalAppData%\Programs\Python\Python310\python.exe"
    "C:\Python313\python.exe"
    "C:\Python312\python.exe"
    "C:\Python311\python.exe"
    "C:\Program Files\Python313\python.exe"
    "C:\Program Files\Python312\python.exe"
    "C:\Program Files\Python311\python.exe"
) do (
    if exist %%P (
        set PYCMD=%%P
        goto :found
    )
)

REM --- Nothing usable found.
echo.
echo ============================================================
echo   Python was not found on this system.
echo ============================================================
echo.
echo The "python" command on Windows is often a placeholder that
echo only redirects to the Microsoft Store. A real install is
echo required.
echo.
echo   1. Download Python from https://www.python.org/downloads/
echo   2. During setup, CHECK "Add python.exe to PATH"
echo   3. Close this window, open a NEW terminal, run this again
echo.
echo If Python IS already installed, turn off the Store alias:
echo   Settings ^> Apps ^> Advanced app settings
echo            ^> App execution aliases
echo   Toggle OFF python.exe and python3.exe
echo.
pause
exit /b 1

:found
echo Using Python: !PYCMD!
for /f "tokens=*" %%v in ('!PYCMD! --version 2^>^&1') do echo Version: %%v
echo.

REM --- Ensure pywin32 is present; install on demand.
!PYCMD! -c "import win32com.client" >nul 2>&1
if !errorlevel! neq 0 (
    echo Dependency pywin32 is missing. Installing...
    echo.
    !PYCMD! -m pip install -r requirements.txt
    if !errorlevel! neq 0 (
        echo.
        echo Dependency installation failed.
        echo Try running this launcher as Administrator,
        echo or install manually:  !PYCMD! -m pip install pywin32
        echo.
        pause
        exit /b 1
    )
    echo.
    echo Dependencies installed.
    echo.
)

echo Starting Driver Update Checker...
echo.
!PYCMD! main.py
set EXITCODE=!errorlevel!

if !EXITCODE! neq 0 (
    echo.
    echo The application exited with an error ^(code !EXITCODE!^).
    pause
)

endlocal
exit /b %EXITCODE%
