@echo off
REM ===========================================================
REM  Panel de despliegue de LactIA (variables de entorno +
REM  actualizar / iniciar / parar LactIA y Cloudflare).
REM  Doble clic EN EL SERVIDOR. Se vuelve a abrir solo como
REM  Administrador (pide confirmación de Windows): sin eso no
REM  puede guardar variables de Equipo ni cerrar procesos.
REM ===========================================================
cd /d "%~dp0"

set PY=python
where python >nul 2>&1 || set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe

"%PY%" panel_despliegue.py
if errorlevel 1 pause
