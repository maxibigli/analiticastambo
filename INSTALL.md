# Instalación en una PC de producción

Guía paso a paso para dejar **Analítica DelPro** funcionando en la PC del tambo.
Tiempo estimado: 20-30 minutos.

> ¿Vas a **conectar la app a la base de la rotativa en producción por red** (otra
> PC)? Andá directo a la sección **[Conexión segura a la base de producción](#conexión-segura-a-la-base-de-producción-en-la-red)**.

---

## 1. Requisitos en la PC nueva

| Requisito | Cómo obtenerlo | Notas |
|---|---|---|
| **Windows** 10/11 o Windows Server | — | |
| **Python 3.12** | <https://www.python.org/downloads/> | ⚠️ Al instalar, tildar **"Add python.exe to PATH"** |
| **ODBC Driver 18 for SQL Server** | <https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server> | Suele venir con DelPro; verificar (paso 2) |
| **Acceso a la base DDM** | SQL Server local o por red | Ver paso 4 |

### Verificar lo que ya está instalado

Abrí PowerShell y ejecutá:

```powershell
python --version
Get-OdbcDriver | Where-Object { $_.Name -like '*SQL Server*' } | Select-Object Name
```

Tenés que ver Python 3.12+ y `ODBC Driver 18 for SQL Server` en la lista.

---

## 2. Copiar la aplicación

Copiá toda la carpeta `delpro-analitica` a la PC nueva, por ejemplo a:

```
C:\DelProAnalitica\
```

No hace falta copiar `__pycache__`, `server.log` ni `server.log.err`.

---

## 3. Instalar las dependencias

En PowerShell, parado en la carpeta:

```powershell
cd C:\DelProAnalitica
python -m pip install -r requirements.txt
```

Instala Flask, pyodbc, waitress y la librería de Anthropic (para las preguntas por IA).

---

## 4. Configurar la conexión a la base

### 4.1 Crear un usuario de SOLO LECTURA (recomendado)

La aplicación solo hace `SELECT`, pero conviene que la base **también** lo impida.
Ejecutá esto en SQL Server Management Studio (o `sqlcmd`) **con permisos de
administrador**, eligiendo tu propia contraseña:

```sql
USE master;
CREATE LOGIN delpro_lectura WITH PASSWORD = 'PONÉ_UNA_CONTRASEÑA_FUERTE',
    DEFAULT_DATABASE = DDM, CHECK_POLICY = ON;
USE DDM;
CREATE USER delpro_lectura FOR LOGIN delpro_lectura;
ALTER ROLE db_datareader ADD MEMBER delpro_lectura;   -- solo lectura
```

> Si el SQL Server no tiene habilitada la autenticación mixta, activala en
> SSMS → Propiedades del servidor → Seguridad → "SQL Server y Windows", y reiniciá
> el servicio. O bien saltea este paso y usá autenticación de Windows (4.2).

### 4.2 Editar `tambos.py`

Abrí `tambos.py` y ajustá el servidor, la base y el modo de autenticación.
**La contraseña NO se escribe acá** (ver 4.3):

```python
TAMBOS = {
    "ponderosa": {
        "nombre": "La Ponderosa",
        "server": "localhost\\DELPRO",   # o "192.168.1.20\\DELPRO" si es otra PC
        "database": "DDM",
        # Opción A (recomendada): usuario de solo lectura
        "auth": "sql", "user": "delpro_lectura",
        # Opción B: sesión de Windows (usa los permisos del usuario que corre la app)
        # "auth": "windows",
    },
}
```

Para **agregar otro tambo**, copiá el bloque con su servidor y base: aparece solo
en el listbox de la aplicación.

### 4.3 Guardar la contraseña (fuera del código)

Con `auth="sql"`, la contraseña se lee de una **variable de entorno**, así no
queda en texto plano en ningún archivo. El nombre es `DELPRO_PWD_` + el id del
tambo en mayúsculas:

```powershell
setx DELPRO_PWD_PONDEROSA "LA_QUE_ELEGISTE"
```

Para otros tambos: `DELPRO_PWD_DON_GERMAN`, etc. (o poné el nombre que quieras
con la clave `"password_env"` en `tambos.py`).

> Cerrá y volvé a abrir PowerShell para que tome la variable. Si la app arranca
> sin encontrarla, avisa con un mensaje claro diciendo qué variable falta.
>
> Si la app corre como **tarea programada con otro usuario**, definí la variable
> a nivel de máquina (PowerShell como administrador):
> `[Environment]::SetEnvironmentVariable('DELPRO_PWD_PONDEROSA','LA_QUE_ELEGISTE','Machine')`

### 4.4 Probar la conexión

```powershell
sqlcmd -S "localhost\DELPRO" -U delpro_lectura -P "$env:DELPRO_PWD_PONDEROSA" -d DDM -Q "SELECT COUNT(*) FROM BasicAnimal"
```

---

## 5. (Opcional) Habilitar las preguntas por IA

Solo si querés la función de preguntas en lenguaje natural. Necesita una clave de
<https://console.anthropic.com> con saldo:

```powershell
setx ANTHROPIC_API_KEY "sk-ant-..."
```

Cerrá y volvé a abrir PowerShell para que tome la variable. Sin clave, el resto de
la aplicación (dashboard, rotativa, tareas y consultas) funciona igual.

---

## 6. Arrancar

Doble clic en **`iniciar.bat`**, o desde PowerShell:

```powershell
cd C:\DelProAnalitica
python servidor.py
```

Abrí <http://localhost:5310> en el navegador.

> ⏳ **La primera carga tarda unos minutos.** La aplicación precalienta todas las
> consultas en segundo plano; mientras tanto muestra "Cargando datos…". Después
> queda instantánea (caché de 10 minutos que se refresca solo).

---

## 7. Que arranque sola con la PC

Con el **Programador de tareas** de Windows:

1. Abrí *Programador de tareas* → **Crear tarea** (no "tarea básica").
2. **General**: nombre `Analitica DelPro`; marcá *Ejecutar tanto si el usuario
   inició sesión como si no* y *Ejecutar con los privilegios más altos*.
3. **Desencadenadores** → Nuevo → *Al iniciar el equipo*.
4. **Acciones** → Nuevo → Programa: `C:\DelProAnalitica\iniciar.bat`,
   *Iniciar en*: `C:\DelProAnalitica`.
5. **Configuración**: destildá *Detener la tarea si se ejecuta más de...*.

---

## 8. Verla desde otras PCs o tablets del tambo

Por defecto la app escucha **solo en esa PC** (`127.0.0.1`). Para abrirla a la red:

1. Editá `iniciar.bat` y cambiá:
   ```bat
   set DELPRO_HOST=0.0.0.0
   ```
2. Abrí el puerto en el firewall (PowerShell como administrador):
   ```powershell
   New-NetFirewallRule -DisplayName "Analitica DelPro" -Direction Inbound `
       -Protocol TCP -LocalPort 5310 -Action Allow -Profile Private
   ```
3. Desde otra PC entrá a `http://IP-DE-LA-PC:5310`.

> ⚠️ La aplicación **no tiene usuarios ni contraseña**: cualquiera en la red la ve.
> Exponela solo en la red interna del tambo, nunca a internet.

---

## 9. Rendimiento

- El acceso a SQL está **serializado** (una consulta por vez) porque SQL Server
  Express queda con poca memoria. Si la PC de producción tiene más RAM, se puede
  subir a 2 consultas simultáneas en `db.py` (`_slots` / `threading.Semaphore`).
- La vista de ordeño es **tiempo real** solo si la app apunta a la base DDM que
  DelPro escribe durante el ordeño (la PC conectada a la rotativa), no a una copia.
- Los parámetros ajustables están arriba de cada archivo: `ordeno.py` (puestos de
  la rotativa, ventanas de tiempo), `tareas.py` (umbrales de las tareas
  pendientes), `tambos.py` (tambos y conexiones).

### 9.1 Precalentar los cachés — IMPORTANTE

Las secciones de análisis guardan su resultado en memoria por 30 a 60 minutos.
El cálculo en sí no es el problema: el problema es que **el primero que entra
después de que el caché vence se come toda la espera**. La peor es Tasa de
Preñez, con unos 75 segundos.

`precalentar.bat` pide esas secciones desde afuera para que el caché ya esté
lleno. Medido: después de una pasada, **todas responden en ~120 ms**.

**Probarlo a mano** (con el servidor andando, doble clic en `precalentar.bat`):

```
[04:00:12] precalentando http://127.0.0.1:5310 (tambo=ponderosa, rebaño=1)
  ok  Tasa de Preñez                 39.2 s
  ok  Análisis Reproductivo           3.0 s
  ok  Performance · peak              3.0 s
  ...
```

**Dejarlo corriendo solo** (recomendado — mantiene los cachés siempre calientes,
no solo a la mañana):

1. Programador de tareas → Crear tarea → nombre `LactIA precalentar`.
2. Desencadenador: **Al iniciar el equipo**, con unos minutos de retraso para
   que el servidor ya esté arriba.
3. Acción → Iniciar un programa:
   - Programa: `precalentar.bat` (ruta completa)
   - Agregar argumentos: `--loop`
   - Iniciar en: la carpeta de la aplicación, **sin comillas**.
4. Propiedades → *Ejecutar tanto si el usuario inició sesión como si no*.
5. Propiedades → Configuración → **destildar** *Detener la tarea si se ejecuta
   durante más de...* (si no, Windows lo mata a las 3 días).

La alternativa más simple es una pasada diaria a las 04:00 sin `--loop`, pero
solo cubre la primera entrada del día.

**Si se agrega una sección nueva**, hay que sumarla a la lista `rutas()` de
`precalentar.py` — y con cuidado, porque acá está la trampa:

> La clave del caché **incluye los parámetros de la consulta**. El script tiene
> que pedir EXACTAMENTE lo mismo que pide la pantalla, si no calienta un caché
> que nadie usa y desde afuera parece que funciona.
>
> El caso concreto que lo rompe: la pantalla manda `rebano=1`; si el script no
> manda nada, el servidor usa su valor por defecto, que es la **lista** `[1]`, y
> la clave queda `...:[1]` contra `...:1`. Por eso el script arranca
> preguntando cuál es el rebaño del tambo y lo manda igual que la pantalla.

**Cómo verificar que quedó bien:** correr `precalentar.bat`, después abrir esa
sección en el navegador. Tiene que aparecer al instante. Si sale el cartel de
"calculando", la clave no coincide.

Detalle completo en `PRECALENTAR.md`.

---

## 10. Problemas frecuentes

| Síntoma | Causa / solución |
|---|---|
| `Data source name not found` | Falta el **ODBC Driver 18**. Instalarlo. |
| `Login failed for user` | Usuario/clave mal, o autenticación SQL deshabilitada (ver 4.1). |
| `python no se reconoce` | Python no está en el PATH. Reinstalar tildando "Add to PATH", o poner la ruta completa en `iniciar.bat`. |
| Se queda en "Cargando datos…" | Normal en el primer arranque. Si pasan >10 min, revisar `server.log.err`. |
| Las preguntas por IA dan error | Falta `ANTHROPIC_API_KEY` o la cuenta no tiene saldo/está deshabilitada. |
| Todo se cuelga en "Cargando datos…" y no avanza | **Falta de RAM en la PC**: SQL Server no consigue memoria y las consultas se traban. Cerrar navegadores y apps pesadas (revisar con `Get-Process \| Sort WorkingSet64 -Descending`). SQL Express necesita RAM libre para trabajar. |

---

## Conexión segura a la base de producción (en la red)

> Para cuando la app se conecta a la base **DDM de la rotativa que está grabando
> en vivo** en otra PC (ejemplo: `192.168.1.20`). El objetivo: **leer sin tocar
> ni trabar nada**. La app ya trae tres barreras para garantizarlo.

### Las 3 barreras que ya tiene la app

1. **Solo SELECT** — `db.py` valida y rechaza INSERT/UPDATE/DELETE/DROP/etc.
2. **Usuario de solo lectura** — con permiso `db_datareader`, la base rechaza
   cualquier escritura aunque la app tuviera un error.
3. **Lectura sin bloqueos** (`SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED`,
   ya activado en `db.py`) — las consultas **no toman candados**, así que nunca
   frenan las escrituras del ordeño. La rotativa sigue grabando normal.

Además: las consultas corren **de a una por vez**, con tope de memoria, y los
resultados quedan cacheados — la app no satura el SQL de producción.

### A. En la PC de la rotativa (192.168.1.20) — una sola vez

> Es configuración de acceso; **no modifica los datos ni la operación del ordeño**.

1. **Confirmar** el nombre de la instancia y la base (normalmente `DELPRO` / `DDM`).
2. **Crear el usuario de solo lectura** (SSMS con permisos de administrador; elegí
   una contraseña fuerte) — ver script del punto **4.1**.
3. **Habilitar conexión remota** (SQL Server Configuration Manager):
   - Protocolo **TCP/IP** habilitado.
   - Servicio **SQL Server Browser** en ejecución (para instancias con nombre).
   - Abrir en el firewall el puerto de la instancia (o permitir `sqlservr.exe`).

### B. En la PC donde corre la app

4. **`tambos.py`** → apuntar a producción y marcarlo como tal:
   ```python
   "ponderosa": {
       "nombre": "La Ponderosa",
       "server": "192.168.1.20\\DELPRO",   # IP\instancia real
       "database": "DDM",
       "auth": "sql", "user": "delpro_lectura",
       "produccion": True,    # ← candado: bloquea las preguntas por IA
   },
   ```
   > **`"produccion": True`** deshabilita del todo las preguntas por IA (lo único
   > que ejecuta SQL generado). Quedan solo el dashboard, la rotativa, las tareas y
   > las consultas fijas, todas de solo lectura. Es la garantía extra si te
   > conectás con una cuenta que **podría** escribir (p. ej. autenticación de
   > Windows con un usuario administrador). El listbox muestra "(producción)" y el
   > cuadro de IA aparece bloqueado con un candado.
5. **Guardar la contraseña** fuera del código:
   ```powershell
   setx DELPRO_PWD_PONDEROSA "LA_CONTRASEÑA_FUERTE"
   ```
6. **Probar la conexión** (antes de arrancar la app):
   ```powershell
   sqlcmd -S "192.168.1.20\DELPRO" -U delpro_lectura -P "$env:DELPRO_PWD_PONDEROSA" -d DDM -Q "SELECT COUNT(*) FROM BasicAnimal"
   ```

### C. Primer arranque, con cuidado

7. Hacé la **primera conexión fuera del horario de ordeño** si podés (por las dudas).
   Arrancá con `iniciar.bat` y abrí el dashboard.
8. **Confirmá que no hay bloqueos** — en la PC de producción, mientras la app carga:
   ```sql
   SELECT session_id, blocking_session_id, wait_type, program_name
   FROM sys.dm_exec_requests r
   JOIN sys.dm_exec_sessions s ON s.session_id = r.session_id
   WHERE s.is_user_process = 1;
   ```
   Ninguna sesión de la app (`program_name` con "python") debe figurar como
   `blocking_session_id` de una sesión de la rotativa.
9. **Durante un ordeño**, verificá que la vista pasa a **EN VIVO** y que la
   rotativa sigue grabando normal.

### Volver atrás (si algo no gusta)

Revertir es instantáneo y sin consecuencias: en `tambos.py` volvés el `server` a
la base de copia (o `auth: "windows"` local) y reiniciás la app. En producción no
quedó nada tocado.

## Integraciones externas (CICLA, La Serenísima, WhatsApp)

Todas usan el mismo criterio que las contraseñas de SQL: **solo por variable de
entorno, nunca en el código**. Sin estas variables, esas secciones del dashboard
simplemente muestran un aviso de "faltan variables" — el resto de la app sigue
funcionando normal.

### CICLA/SISCLAC (caudalímetro de la rotativa)

```powershell
setx CICLA_USUARIO "tu_usuario_de_cicla"
setx CICLA_PASSWORD "tu_contraseña_de_cicla"
```

Trae litros medidos y temperatura de entrega por carga (tarjeta "Entregas de
leche (CICLA)"). El informe de CICLA pagina de a ~40 filas; para rangos largos
puede avisar que el resultado está incompleto (ver aviso en la tarjeta).

### La Serenísima (comprador oficial — tambo 1565, período actual)

```powershell
setx LASER_USUARIO "tu_usuario_de_la_serenisima"
setx LASER_PASSWORD "tu_contraseña_de_la_serenisima"
```

Trae litros, grasa, proteínas, U.F.C. y temperatura oficiales por entrega
(tarjeta "Calidad de leche"), y alimenta la comparación diaria contra CICLA.

### Haasten (computadora del mixer — sección "🌾 Alimentación")

```powershell
setx HASTEN_USUARIO "tu_usuario_de_haasten"
setx HASTEN_PASSWORD "tu_contraseña_de_haasten"
```

**Ojo con el nombre: `HASTEN_`, con una sola "a"**, aunque el sitio se llame
haasten.io. Si lo "corregís" a `HAASTEN_` la aplicación deja de encontrarlas.

Trae los lotes del mixer con sus cabezas, los ingredientes y las descargas por
lote. Es lo que alimenta la **conciliación de grupos**: qué lote del mixer
corresponde a qué grupo de DelPro, que es el puente para poder calcular después
costo por vaca y eficiencia de conversión.

Como todas las variables de entorno de esta app, `setx` las guarda pero **los
procesos que ya estaban abiertos no las ven**: hay que reiniciar la aplicación
(en el servidor, `actualizar.bat` como Administrador) para que las tome. Si no,
la pantalla va a seguir diciendo que faltan las variables aunque ya estén.

### Alertas — tres canales, cada uno se tilda/destilda desde la tarjeta "🔔 Alertas"

Se puede tener más de uno activo a la vez (manda por todos los que estén
tildados y configurados). **Telegram y Email son gratis** — Twilio/WhatsApp
tiene costo (o el sandbox gratuito, con las limitaciones ya conocidas).

### Telegram (gratis, recomendado)

1. Hablale a **@BotFather** en Telegram, mandale `/newbot` y seguí los pasos
   (nombre, usuario). Al final te da un **token**.
2. Buscá tu bot por su usuario y mandale cualquier mensaje (ej. `hola`) para
   habilitar el chat.
3. Andá a `https://api.telegram.org/bot<tu_token>/getUpdates` (con tu token
   en vez de `<tu_token>`) y buscá `"chat":{"id":` — ese número es tu
   `TELEGRAM_CHAT_ID`.
4. Configurá (en tu terminal):
   ```powershell
   setx TELEGRAM_BOT_TOKEN "123456789:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
   setx TELEGRAM_CHAT_ID "123456789"
   ```
5. Reiniciá la app, tildá "Telegram" en la tarjeta "🔔 Alertas" y probá con
   "Probar envío".

### Email (gratis, vía SMTP — ej. Gmail)

1. Si usás Gmail, generá una **contraseña de aplicación** en
   myaccount.google.com → Seguridad → Verificación en 2 pasos → Contraseñas
   de aplicaciones (no sirve la contraseña normal de la cuenta).
2. Configurá:
   ```powershell
   setx SMTP_HOST "smtp.gmail.com"
   setx SMTP_PORT "587"
   setx SMTP_USUARIO "tu_cuenta@gmail.com"
   setx SMTP_PASSWORD "la_contraseña_de_aplicación"
   setx SMTP_DESTINO "donde_quieras_recibir_las_alertas@ejemplo.com"
   ```
3. Reiniciá la app, tildá "Email" en la tarjeta "🔔 Alertas" y probá con
   "Probar envío".

### Alertas por WhatsApp (Twilio)

1. Creá una cuenta gratis en [twilio.com](https://www.twilio.com) (el trial
   incluye crédito para probar).
2. En la consola de Twilio: **Messaging → Try it out → Send a WhatsApp
   message**. Ahí te muestra el número sandbox (suele ser
   `+1 415 523 8886`) y un código para unirte, tipo `join palabra-palabra`.
3. Desde **tu propio celular** (el que va a recibir las alertas), mandale por
   WhatsApp ese código (`join palabra-palabra`) al número sandbox. Sin este
   paso, Twilio no te puede escribir.
4. En la página principal de la consola de Twilio vas a ver tu **Account SID**
   y tu **Auth Token**. Configurá todo (vos mismo, en tu terminal):
   ```powershell
   setx TWILIO_ACCOUNT_SID "ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
   setx TWILIO_AUTH_TOKEN "tu_auth_token"
   setx TWILIO_WHATSAPP_FROM "whatsapp:+14155238886"
   setx WHATSAPP_TELEFONO "+549341XXXXXXX"
   ```
   (`WHATSAPP_TELEFONO` es tu número, con código de país, el mismo que mandó
   el "join").
5. Reiniciá la app y probá con el botón "Probar envío" en la tarjeta
   "🔔 Alertas por WhatsApp" (no hace falta esperar al horario programado).

La app revisa a las **8:00 y 20:00** y avisa (una sola vez por condición,
hasta que se resuelva) cuando:
- Temperatura del caudalímetro (CICLA) > 5°C.
- U.F.C. de una entrega (La Serenísima) > 40.
- Score de una sesión de Rutina de ordeño < 60%.
- Un puesto de la rotativa tiene incidencias muy por encima de lo normal.

Nota: como en todas las variables de entorno de esta app, `setx` las guarda en
el registro de Windows — hace falta reiniciar la app (o abrir una terminal
nueva) para que tome el valor.
