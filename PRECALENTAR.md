# Precalentar los cachés

Las secciones de análisis guardan su resultado en memoria durante 30 a 60
minutos. El problema no es el cálculo en sí: es que **el primero que entra
después de que el caché vence se come toda la espera**. La peor es Tasa de
Preñez, con unos 75 segundos.

`precalentar.bat` pide esas secciones desde afuera para que el caché ya esté
lleno. Medido: después de una pasada, todas responden en **~120 ms**.

## Probarlo a mano

Con el servidor andando, doble clic en `precalentar.bat`. Tiene que verse así:

```
[04:00:12] precalentando http://127.0.0.1:5310 (tambo=ponderosa, rebaño=1)
  ok  Tasa de Preñez                 39.2 s
  ok  Análisis Reproductivo           3.0 s
  ok  Performance · peak              3.0 s
  ...
```

Si alguna dice `FALLA`, el mensaje al lado explica por qué. Los casos típicos:
el servidor no está levantado, o el puerto no es el 5310 (se cambia con la
variable `DELPRO_PORT`).

## Que corra solo

Hay dos formas. La segunda es la que conviene.

### Opción A — una vez por día, de madrugada

Sirve para que el primero que llega a la mañana no espere. El caché vence a la
media hora, así que solo cubre la primera entrada del día.

1. Abrir el **Programador de tareas** de Windows.
2. Crear tarea básica → nombre `LactIA precalentar`.
3. Desencadenador: **Diariamente**, a las **04:00**.
4. Acción: **Iniciar un programa**.
   - Programa: `C:\Users\DelPro\...\delpro-analitica\precalentar.bat`
   - Iniciar en: la misma carpeta (sin comillas).
5. En Propiedades, tildar **Ejecutar tanto si el usuario inició sesión como si
   no**.

### Opción B — todo el día, cada 25 minutos (recomendada)

Mantiene los cachés siempre calientes, no solo a la mañana. Es un proceso que
queda corriendo y duerme entre pasada y pasada, así que no consume nada
mientras espera.

Igual que arriba pero:

- Desencadenador: **Al iniciar el equipo**.
- Acción → **Agregar argumentos**: `--loop`
- En Propiedades → Configuración, destildar *Detener la tarea si se ejecuta
  durante más de...*

Conviene ponerla **después** de la que arranca `iniciar.bat`, o dejar unos
minutos de retraso en el desencadenador, para que el servidor ya esté arriba.

## Lo que hay que saber si se cambia algo

**El precalentador tiene que pedir EXACTAMENTE lo mismo que pide la pantalla.**
La clave del caché incluye los parámetros, así que si no coinciden se calienta
un caché que nadie va a usar, y desde afuera parece que funciona.

Los dos lugares donde eso se rompe fácil:

- **El rebaño.** Si la pantalla manda `rebano=1` y el script no manda nada, el
  servidor usa su valor por defecto, que es la lista `[1]`, y la clave queda
  `...:[1]` contra `...:1`. Por eso el script arranca preguntando cuál es el
  rebaño del tambo.
- **Las fechas.** Se calculan con `date.today()`. Si se agrega una sección
  nueva a `precalentar.py`, hay que copiar los mismos valores por defecto que
  usa el frontend en `templates/index.html`.

Para verificar que quedó bien: correr `precalentar.bat` y después entrar a la
sección desde el navegador. Tiene que aparecer al instante, sin el cartel de
"calculando". Si aparece el cartel, la clave no coincide.
