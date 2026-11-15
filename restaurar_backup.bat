@echo off
REM ===========================================================
REM  Restaura el backup mas nuevo de cada tambo de C:\backupsdelpro
REM  (laponderosa, lamartina, sanjose) en SQL Server. Ver restaurar_backup.py.
REM
REM  CLIC DERECHO -> EJECUTAR COMO ADMINISTRADOR (usa tu usuario de Windows
REM  para entrar a SQL Server; no hay contrasenas en ningun lado).
REM
REM  Para ver que usaria sin tocar nada:   restaurar_backup.bat --solo-ver
REM  Para un solo tambo:                   restaurar_backup.bat --tambo laponderosa
REM ===========================================================
cd /d "%~dp0"

net session >nul 2>&1
if errorlevel 1 (
  echo Este archivo hay que ejecutarlo como Administrador: clic derecho, "Ejecutar como administrador".
  pause
  exit /b 1
)

set PY=python
where python >nul 2>&1 || set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe

"%PY%" restaurar_backup.py %*
set RESULTADO=%errorlevel%
if not "%RESULTADO%"=="0" echo.& echo Termino con errores: mira los mensajes de arriba.
pause
exit /b %RESULTADO%
