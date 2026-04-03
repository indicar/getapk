@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

set "LAST_FILE=%~dp0.last_apk_path"

set "LASTPATH="
if exist "%LAST_FILE%" (
    set /p LASTPATH=<"%LAST_FILE%"
    for /f "tokens=* delims= " %%a in ("!LASTPATH!") do set "LASTPATH=%%a"
    if not exist "!LASTPATH!" set "LASTPATH="
)

echo ========================================
echo   APK Upload
echo ========================================
echo.

set "APK_PATH="
if defined LASTPATH (
    echo Using: !LASTPATH!
    set /p CHANGE="Change? (y/n): "
    if /i "!CHANGE!"=="y" (
        set /p APK_PATH="Enter new APK path: "
    ) else (
        set "APK_PATH=!LASTPATH!"
    )
    if "!CHANGE!"=="" set "APK_PATH=!LASTPATH!"
) else (
    set /p APK_PATH="Enter APK path: "
)

if "!APK_PATH!"=="" (
    echo Error: APK path required
    pause
    exit /b 1
)

echo !APK_PATH! > "%LAST_FILE%"

python "%~dp0upload_apk.py" "!APK_PATH!"
pause