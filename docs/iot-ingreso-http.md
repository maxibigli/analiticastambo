# Mandar lecturas a LactIA por HTTP (ESP32, LoRaWAN)

> Este documento es de los equipos que **mandan** sus datos. Un PLC o gateway que **responde por Modbus TCP** no usa nada de esto: se carga en la misma tarjeta con el tipo «Responde por Modbus TCP» y LactIA lo consulta (ver `INSTALL.md`).

Para quien programa el equipo (un ESP32, un nodo LoRaWAN detrás de ChirpStack). El equipo **manda** sus lecturas; LactIA las muestra en
**Monitoreo IoT** como cualquier otro sensor, con su historial, colores y avisos.

## Antes de programar el equipo

En LactIA, **⚙ Configuración › 🔌 Entradas/Salidas › «Otros equipos que mandan datos por HTTP»**:

1. **➕ Agregar equipo**: nombre, formato y **ventana** (los minutos que pueden pasar sin que llegue nada antes de que sus sensores figuren
   «Sin datos»; tiene que ser **mayor que cada cuánto manda el equipo**: si manda cada 60 minutos, 120 es razonable; 30 lo daría por caído a cada rato).
2. **Copiá el token**. Se muestra una sola vez; si se pierde, «Nuevo token» (y el anterior deja de servir).
3. Agregá los **canales** que manda el equipo. Cada uno tiene una **clave** (la que usa el equipo en su envío: letras, números, `_` o `-`, hasta 40,
   sin puntos ni espacios) y es:
   - **Digital**: un contacto (`1`/`0`, o `true`/`false`). Después se asigna a una alarma, indicador o estado en «Sensores del tambo».
   - **Analógico**: un número. `valor = lo que manda × factor + offset` (vacíos = 1 y 0). Se elige a qué sensor del panel alimenta.

## Formato genérico (ESP32)

```
POST <dirección de envío>        (la muestra la tarjeta: https://… o http://IP-de-la-PC:puerto/api/iot/ingreso/<id>)
X-Token: <el token>
Content-Type: application/json   (si falta, igual se acepta el JSON)

{"lecturas": {"temp": 21.5, "puerta": 1}, "hora": "2026-11-23T10:15:00-03:00"}
```

- `lecturas` es un objeto `{clave del canal: valor}`; es obligatorio. Un canal que el equipo manda y no está declarado, o un valor que no sirve
  (texto, `NaN`, infinito, un `true` en un analógico), se **ignora y se cuenta**: no es un error y el resto del envío se guarda.
- `hora` es opcional (ISO 8601). Sin hora, o con una que no se entiende, **en el futuro** o **anterior al día en que se creó el equipo en LactIA** (un ESP32 sin
  internet para sincronizar la hora manda la de arranque, 1970), se usa la hora del servidor. Una hora pasada se respeta (para
  reenviar lo que el equipo guardó mientras no tenía red). Con zona (`-03:00`, `Z`) se convierte a la hora local del tambo.
- Para probar desde una PC (reemplazá la dirección, el canal y el token):

```
curl -X POST http://192.168.1.50:5310/api/iot/ingreso/esp32_del_corral -H "X-Token: <TOKEN>" -H "Content-Type: application/json" -d '{"lecturas": {"temp": 21.5}}'
```

**Un equipo dentro de la red del tambo tiene que usar la IP de la PC del servidor** (y el puerto de LactIA), no el nombre público: no depende de que
haya internet.

## Respuestas

| Código | Qué pasó |
|---|---|
| `200` | `{"ok": true, "recibidas": n, "ignorados": m}` |
| `400` | El cuerpo no es un objeto JSON, o `lecturas` no es un objeto. El texto dice cuál. **Queda anotado como «Último error» en la tarjeta del equipo.** |
| `403` | Token incorrecto, sin token o equipo que no existe (no se distinguen a propósito). |
| `405` | Se usó otro método (solo `POST`). |
| `413` | El cuerpo pasa de 65.536 bytes. |
| `503` | La base de datos estaba ocupada (la usa el gateway): reintentar en unos segundos. |
| `429` | Muchos intentos seguidos con un token incorrecto desde la misma IP **a ese equipo**: esperar unos 10 minutos. El token correcto tampoco entra mientras dura (los otros equipos no se ven afectados). |

## ChirpStack (LoRaWAN) — SIN confirmar con un servidor real

En ChirpStack, **Application › Integrations › HTTP**: la URL de eventos es la dirección de envío con `?event=up&token=<TOKEN>` (la tarjeta la muestra así
para el formato ChirpStack). Se usa **solo el evento `up`**; los demás (`join`, `status`, `ack`…) se contestan `200` con `recibidas: 0`.

Dos advertencias:

1. **El token va en la URL**: cualquier cosa que registre las direcciones entre ChirpStack y LactIA (Cloudflare, un proxy) lo ve. Conviene que ChirpStack hable
   con LactIA por la red local.
2. **Hace falta el payload ya decodificado.** LactIA lee los valores de primer nivel de `object` (lo que decodifica el códec del dispositivo en ChirpStack);
   las claves son las de los canales. Si el dispositivo no tiene un códec, el evento trae solo `data` en base64 y LactIA contesta `400` con «el nodo no manda el
   payload decodificado (falta el códec en ChirpStack)». La hora se toma de `time`.

3. **La dirección es de toda una Application, no de un nodo**: LactIA no distingue qué dispositivo mandó cada uplink (ignora `deviceInfo`). Si dos nodos
   de la misma Application mandan las mismas claves (`temperature`…), escriben en el mismo canal y el sensor muestra el valor del último que llegó. Usá **una
   Application (y un equipo en LactIA) por nodo**, o claves distintas en cada nodo.

La documentación oficial de ChirpStack no confirma estos dos puntos (cabecera propia y el campo `object`): **probarlo con un ChirpStack real antes de usarlo**.

## Qué se ve en la tarjeta

Por equipo: la última recepción (con día de la semana), cuántas lecturas se recibieron e ignoraron en total, el último error con su fecha, y por canal el
último valor recibido. Si el equipo manda pero un sensor no se mueve, mirar acá si las claves coinciden (un canal mal escrito aparece como «ignorada»).

## Recibir órdenes (salidas del equipo)

Solo para equipos de formato **genérico** (ESP32) con «Puede recibir órdenes» tildado en LactIA. El equipo **no recibe conexiones**: *consulta* sus órdenes, las
ejecuta y *avisa*. En la tarjeta se agregan **salidas** (un relé) de dos modos: **pulso** (se activa unos segundos y se suelta sola; la duración la carga el tambo, hasta 60 s)
y **sostenida** (queda prendida hasta que llega otra orden «apagar»).

```
GET  <dirección de envío>/ordenes                       (cabecera X-Token)
  → 200 {"ordenes": [{"id": 7, "canal": "bomba", "accion": "pulso", "duracion_s": 1.5}]}     (acciones: pulso, prender, apagar)

POST <dirección de envío>/ordenes/7/ok                  (cabecera X-Token)
  cuerpo vacío                                 → la ejecutó
  cuerpo {"error": "el relé no responde"}      → no pudo
  → 200 {"ok": true, "estado": "ejecutada"}    (o "error")
```

- `canal` es la clave de la salida **tal como la cargaste en la tarjeta** (la misma que usa el equipo para sus lecturas).
- **Cada orden se entrega UNA sola vez.** Si la respuesta se pierde en el camino, LactIA NO la reenvía (el error seguro es no disparar dos veces): queda «sin confirmar».
- **Vence.** «Vence a los (segundos)» es lo que una orden puede esperar a que el equipo la levante, y de nuevo lo que tiene para confirmar. Tiene que ser **mayor que cada cuánto
  consulta tu equipo**. Una orden que venció no se ejecuta nunca, aunque el equipo vuelva a los 20 minutos.
- Estados que se ven en la tarjeta: *pendiente* (todavía no la levantó), *enviada* (la levantó, esperando su confirmación), *ejecutada*, *con error*, *vencida* (nadie la levantó
  a tiempo) y *sin confirmar* (la levantó y no avisó). Ninguna queda «enviada» para siempre.
- A lo sumo **una orden viva por salida**: apretar el botón dos veces no apila pulsos.
- Respuestas: `200`; `404` orden que no existe o es de otro equipo (no se distinguen); `409` orden que todavía no se te entregó, ya vencida o ya confirmada al revés
  (y también en la consulta si el equipo ya no puede recibir órdenes); `403`, `413`, `429`, `503` como en el envío de lecturas.
- **Tu firmware tiene que apagar todas sus salidas al arrancar.** LactIA no ve el estado real de un relé sostenido (solo cuál fue la última orden): si el equipo se reinicia o se
  cae la red, solo él sabe cómo quedó. Las sostenidas **no tienen tope de tiempo encendidas**.

Bucle mínimo del firmware (pseudocódigo):

```
cada N segundos (N menor que el vencimiento):
    ordenes = GET .../ordenes
    para cada orden:
        ejecutar(orden.canal, orden.accion, orden.duracion_s)      # pulso: prender, esperar, apagar
        POST .../ordenes/<id>/ok   (con {"error": "..."} si falló)
```
