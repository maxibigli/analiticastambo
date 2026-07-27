@echo off
REM ===========================================================
REM  LactIA - precalentar los caches de las secciones pesadas
REM
REM  La primera carga de Tasa de Prenez tarda ~75 segundos y se
REM  la come el primero que entra. Esto la deja lista de antes.
REM
REM  Uso:
REM    precalentar.bat          una pasada y termina
REM    precalentar.bat --loop   una pasada cada 25 minutos
REM
REM  Para que corra solo, ver PRECALENTAR.md.
REM ===========================================================
cd /d "%~dp0"

REM Busca Python en el PATH; si no esta, usa la instalacion del usuario.
set PY=python
where python >nul 2>&1 || set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe

if not defined DELPRO_PORT set DELPRO_PORT=5310

"%PY%" precalentar.py %*
