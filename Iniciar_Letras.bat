@echo off
cd /d "%~dp0"
taskkill /f /im pythonw.exe >nul 2>&1
start "" "C:\Users\rafae\AppData\Local\Programs\Python\Python312\pythonw.exe" "app.py"
exit
