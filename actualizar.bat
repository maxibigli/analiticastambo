@echo off
REM ===========================================================
REM  LactIA (Analitica DelPro) - actualizacion via git
REM  Baja los ultimos cambios del repositorio, para el servidor
REM  que este corriendo y lo vuelve a levantar con el codigo
REM  nuevo. Doble clic para actualizar.
REM
REM  IMPORTANTE: correr como Administrador. Si no, el paso 2
REM  puede no tener permiso para cerrar el proceso Python viejo,
REM  y quedaria corriendo con el codigo anterior aunque los
REM  archivos ya se hayan actualizado.
REM
REM  Este .bat asume que la carpeta ya esta enganchada al repo
REM  (git init / git remote add / git checkout -B master
REM  origin/master, una sola vez). Descarta cualquier cambio
REM  local sin commitear en los archivos versionados (git reset
REM  --hard) para garantizar que quede igual al repo; los
REM  archivos propios del servidor (usuarios.json, secret_key.txt,
REM  alertas_canales.json) no estan versionados, asi que no se
REM  tocan.
REM ===========================================================
setlocal
cd /d "%~dp0"

echo.
echo === 1/3: bajando cambios del repositorio ===
git fetch origin
if errorlevel 1 (
    echo ERROR: no se pudo hacer "git fetch". Revisa la conexion o si pide
    echo iniciar sesion en GitHub.
    pause
    exit /b 1
)
git reset --hard origin/master

echo.
echo === (extra) instalando dependencias nuevas si hay ===
where python >nul 2>&1 && (python -m pip install -q -r requirements.txt) || (
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" -m pip install -q -r requirements.txt
)

echo.
echo === 2/3: deteniendo el servidor actual ===
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*servidor.py*' } | ForEach-Object { Write-Host ('  deteniendo PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force }"
timeout /t 2 /nobreak >nul

echo.
echo === 3/3: levantando el servidor ===
if not exist "iniciar.bat" (
    echo ERROR: no se encontro iniciar.bat en esta carpeta.
    echo Levanta el servidor a mano con: python servidor.py
    pause
    exit /b 1
)
start "LactIA" iniciar.bat

echo.
echo Listo. Se abrio una ventana nueva con el servidor - revisala para
echo confirmar que arranco sin errores (sin tracebacks en rojo).
pause
