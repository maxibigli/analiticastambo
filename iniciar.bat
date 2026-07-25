@echo off
REM ===========================================================
REM  Analitica DelPro - inicio en modo produccion
REM  Doble clic para arrancar, o usarlo desde el Programador de
REM  tareas de Windows para que arranque solo con la PC.
REM ===========================================================
cd /d "%~dp0"

REM Busca Python en el PATH; si no esta, usa la instalacion del usuario.
set PY=python
where python >nul 2>&1 || set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe

REM 0.0.0.0 = accesible desde la red (celulares/tablets del tambo).
REM Cambiar a 127.0.0.1 para que solo la vea esta PC.
if not defined DELPRO_HOST set DELPRO_HOST=0.0.0.0
if not defined DELPRO_PORT set DELPRO_PORT=5310

echo Iniciando Analitica DelPro en %DELPRO_HOST%:%DELPRO_PORT% ...
"%PY%" servidor.py
pause
