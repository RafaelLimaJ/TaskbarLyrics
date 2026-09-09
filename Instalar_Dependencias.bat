@echo off
cd /d "%~dp0"
title Instalando Dependencias do TaskbarLyrics
echo Verificando Python e instalando dependencias necessarias...
echo.
python -m pip install --upgrade pip
python -m pip install PyQt6 pywin32 websockets
echo.
echo Dependencias instaladas com sucesso!
echo Voce ja pode iniciar o aplicativo pelo Iniciar_Letras.bat.
pause
