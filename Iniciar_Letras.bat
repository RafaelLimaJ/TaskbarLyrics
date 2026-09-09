@echo off
cd /d "%~dp0"
taskkill /f /im pythonw.exe >nul 2>&1

where pythonw >nul 2>&1
if %errorlevel% equ 0 (
    start "" pythonw app.py
) else (
    start "" python app.py
)
exit
