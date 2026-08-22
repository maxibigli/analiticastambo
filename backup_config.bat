@echo off
REM ===========================================================
REM  LactIA - backup de la configuracion propia de esta PC
REM  Copia los archivos que NO van a git (usuarios, credenciales,
REM  umbrales, precios, cache) a una carpeta fechada. El CODIGO
REM  no hace falta respaldarlo aca: ya esta en git/GitHub.
REM  Doble clic para hacer el backup.
REM
REM  DESTINO: cambia la linea de abajo si tenes un disco externo
REM  o una carpeta de red/nube (OneDrive, etc.) - por defecto
REM  queda en Documentos, dentro de esta misma PC.
REM ===========================================================
setlocal
set ORIGEN=%~dp0
set DESTINO_BASE=%USERPROFILE%\Documents\LactIA_backups

REM Fecha AAAA-MM-DD sin depender del idioma/formato regional de Windows.
for /f %%f in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set FECHA=%%f
set DESTINO=%DESTINO_BASE%\%FECHA%

echo.
echo === Backup de configuracion de LactIA ===
echo Origen:  %ORIGEN%
echo Destino: %DESTINO%
echo.

if not exist "%DESTINO%" mkdir "%DESTINO%"

set ARCHIVOS=usuarios.json secret_key.txt alertas_canales.json podal.db iot_sensores.db metas_reproductivas.json parametros_reproductivos.json conciliacion_grupos.json clima_cache.json sala_convencional.json configuracion_tambos.json Precios_alimentos.xlsx tablero_umbrales.json tablero_lecturas.json checklist.db checklist.db-wal checklist.db-shm

for %%A in (%ARCHIVOS%) do (
    if exist "%ORIGEN%%%A" (
        copy /y "%ORIGEN%%%A" "%DESTINO%\" >nul
        echo   copiado: %%A
    )
)

REM checklist_fotos es una carpeta, no un archivo suelto: se copia aparte.
if exist "%ORIGEN%checklist_fotos" (
    xcopy "%ORIGEN%checklist_fotos" "%DESTINO%\checklist_fotos\" /e /i /y >nul
    echo   copiada: checklist_fotos\
)

echo.
echo Listo. Backup en: %DESTINO%
echo.
echo RECORDATORIO: configuracion_tambos.json tiene contrasenas de conexion en
echo texto plano. Guarda esta carpeta de backup con el mismo cuidado que el
echo original - no la dejes en algo compartido sin proteger.
echo.
pause
