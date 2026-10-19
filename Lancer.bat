@echo off
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
    echo Python n'est pas installe.
    echo Installe-le depuis https://www.python.org/downloads/ puis relance ce fichier.
    pause
    exit /b
)
py -c "import psutil, requests, websocket, pystray, PIL, webview" >nul 2>nul
if errorlevel 1 (
    echo Installation des dependances (une seule fois^)...
    py -m pip install --user psutil requests websocket-client pystray pillow pywebview
)
rem pyw : pas de fenetre noire ; l'app s'ouvre dans sa propre fenetre + icone W pres de l'horloge
start "" pyw app.py
