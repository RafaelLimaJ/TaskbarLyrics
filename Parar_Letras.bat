@echo off
title Parar TaskbarLyrics
taskkill /f /im pythonw.exe >nul 2>&1
taskkill /f /im python.exe >nul 2>&1
echo TaskbarLyrics fechado com sucesso!
timeout /t 2 >nul
exit
