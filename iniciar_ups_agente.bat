@echo off
REM ===========================================================
REM  Agente de la UPS - corre en una PC DEL TAMBO.
REM  Lee la tarjeta de red de la UPS por SNMP (red local) y manda
REM  las lecturas a LactIA por HTTPS (solo salida: no abre puertos).
REM
REM  Archivos que necesita en la misma carpeta (nada mas):
REM     ups_agente.py   snmp_min.py   ups_snmp.py
REM
REM  UNA SOLA VEZ, en una consola de esa PC (las claves NO van en este archivo):
REM     setx UPS_INGESTA_TOKEN "el-token-que-te-dio-LactIA"
REM     setx UPS_SNMP_COMMUNITY "la-community-de-lectura-de-la-tarjeta"
REM  y despues cerrar y volver a abrir la consola.
REM ===========================================================
cd /d "%~dp0"

REM --- Completar con los datos del tambo (no son secretos) ----
set UPS_IP=192.168.102.230
set TAMBO=ponderosa
set DIRECCION_LACTIA=https://www.analiticastambo.com
REM -------------------------------------------------------------

set PY=python
where python >nul 2>&1 || set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe

if "%UPS_INGESTA_TOKEN%"=="" (
  echo Falta la variable UPS_INGESTA_TOKEN. Mira las instrucciones al principio de este archivo.
  pause
  exit /b 1
)

echo Iniciando el agente de la UPS (Ctrl+C para cortar)...
:otra_vez
"%PY%" ups_agente.py %UPS_IP% --tambo %TAMBO% --url %DIRECCION_LACTIA%
if %errorlevel%==130 exit /b 0
echo El agente se detuvo. Reintentando en 30 segundos...
timeout /t 30 /nobreak >nul
goto otra_vez
