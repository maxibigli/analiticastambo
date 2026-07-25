@echo off
REM ===========================================================
REM  Poller del gateway IoT (M300) - lee el estado de la lavadora
REM  por Modbus TCP y lo guarda en iot_sensores.db (SQLite).
REM  Corre aparte de la app principal (iniciar.bat).
REM ===========================================================
cd /d "%~dp0"

set PY=python
where python >nul 2>&1 || set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe

echo Iniciando poller IoT (Ctrl+C para cortar)...
"%PY%" iot_lavado.py
pause
