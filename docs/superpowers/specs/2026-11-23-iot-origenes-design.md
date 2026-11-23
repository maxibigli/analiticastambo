# Orígenes de datos del Monitoreo IoT (Modbus configurable, ESP32, LoRaWAN)

Estado: **borrador para aprobar** (23/11/2026). Nada de esto está construido.

## Para qué

LactIA se instala en varios tambos y cada uno puede traer sus señales de un equipo distinto: el PUSR M300 de La Ponderosa, otro
PLC o gateway Modbus con otro mapa de direcciones, un ESP32 propio o sensores LoRaWAN. Hoy solo las entradas **analógicas** del
M300 tienen mapeo editable; las digitales, las salidas, el unit ID y el tipo de función están escritos en el código
(`iot_lavado.CANALES`, `ACTUADORES`, `device_id=1`). Un tambo con otro equipo obliga a tocar código.

Objetivo: que **cada tambo declare desde la web de dónde viene cada señal** y, donde corresponda, cómo se comanda cada salida.
Sin tocar código y **sin cambiar nada en La Ponderosa**.

## Lo que ya existe y se reusa (no se toca su contrato)

El panel, las alertas, el historial, el catálogo de sensores y el estado general solo leen tablas SQLite por canal
(`lecturas_sensor`, `digitales_estado`, `eventos_di`, `analogicas_crudo`) y encolan órdenes en `comandos_actuador`. No saben de
Modbus. **Cualquier origen nuevo solo tiene que dejar sus datos ahí.** `iot_lavado.py` sigue siendo el único dueño de las
conexiones Modbus; Flask nunca escribe Modbus.

## No-objetivos

* Comandar salidas por **LoRaWAN** (los downlinks esperan al próximo uplink del nodo): queda para una etapa posterior. El diseño
  no lo impide, pero un origen LoRaWAN nace de solo lectura.
* Escribir el firmware del ESP32 (se entrega el protocolo documentado y un ejemplo con `curl`).
* MQTT. Si algún día hace falta, entra como un tipo de origen más.
* Que el Lavado Automático y los comandos de voz usen salidas de otros orígenes: en esta etapa siguen siendo del equipo
  principal (`RELES_VALIDOS`, `ACTUADORES_VALIDOS` no cambian).

## Enfoques considerados

* **A. Orígenes con dos puertas (elegido).** Modbus (lo consultamos nosotros) y HTTP (nos lo mandan). El ESP32 y LoRaWAN entran
  por la misma puerta HTTP: ChirpStack y TTN traen una «HTTP integration» que reenvía cada uplink ya decodificado, así que el
  servidor LoRaWAN puede estar en cualquier lado.
* B. Broker MQTT como bus único: más prolijo, pero suma un proceso por tambo en una PC con poca RAM. Descartado por ahora.
* C. Perfiles por marca en código: cada gateway nuevo vuelve a requerir programar. Descartado.

## Diseño

### 1. Origen

Un origen es una fuente de canales. Se guardan en `iot_origenes.json` (estado propio de cada instalación, **fuera de git**,
porque lleva tokens). Hay uno que siempre existe, `principal`: el equipo Modbus de siempre, con la IP y el puerto de
`iot_conexion` (no se mueven de ahí).

| tipo | quién habla | campos propios |
|---|---|---|
| `modbus_tcp` | `iot_lavado.py` consulta | `host`, `port`, `unit_id` |
| `http` | el equipo nos manda | `token`, `formato` (`generico` o `chirpstack`), `puede_comandar`, `vencimiento_orden_s` (solo si comanda) |

Todos: `id` (clave interna, no cambia nunca), `nombre`, `ventana_lectura_min`.

**`ventana_lectura_min`** reemplaza la constante global `VENTANA_LECTURA_RECIENTE_MIN = 30` para los canales de ese origen. Un
nodo LoRaWAN que manda cada 30 a 60 min quedaría «mudo» (sin datos) todo el tiempo con 30. El origen `principal` conserva 30.

### 2. Canal

Los canales del `principal` conservan sus claves (`lavado_rotativa`, `barrido_rotativa`, `di_3`..`di_32`, `ai_1`..`ai_32`,
`do_1`..`do_32`): están en el historial y en los sensores guardados. Los de otros orígenes se llaman `<id_origen>.<canal>`
(`esp32_corral.temp`).

Cada canal tiene `grupo` (`entrada_digital`, `entrada_analogica` o `salida`), `nombre` y, según el origen:

* **Modbus**: `funcion` (`discrete_input`, `coil`, `input_register`, `holding_register`), `direccion`, `unit_id` (si no, el del
  origen), `tipo_dato` (`bit`, `uint16`, `int16`, `float32_ab_cd`), `factor` y `offset` (valor = crudo × factor + offset),
  `invertido`. Las salidas llevan además `modo` (`pulso` o `sostenida`) y `pulso_s`.
* **HTTP**: solo el nombre y, si es analógico, `factor` y `offset`.

Para el `principal` se **generan** los valores de siempre (la entrada n es `discrete_input` en la dirección n−1, la salida n es
`coil` en la n−1, `unit_id` 1, pulso de 0,5 s) y solo se guarda lo que el tambo cambió. Sin archivo, el comportamiento es
idéntico al de hoy; una prueba lo fija (mismas llamadas Modbus, mismas direcciones).
Las analógicas del `principal` siguen calibrándose en `iot_analogicas` (4-20 mA, `raw_4`/`raw_20`); no se toca.
Las direcciones en notación Modicon (`30001`, `10001`…) se aceptan y se convierten, igual que ya hace `iot_analogicas`.

### 3. Sensores

El catálogo (`iot_sensores`) hoy valida `entrada in ENTRADAS`. Pasa a validar contra **todas las entradas digitales de todos los
orígenes**. Una entrada sigue yendo a un solo sensor. Un sensor analógico de otro origen se asigna en la definición del canal
(`sensor`), igual que `iot_analogicas` lo hace para el principal. El resto del catálogo (roles, colores, estados, avisos) no
cambia.

### 4. Origen HTTP: entrada de datos

`POST /api/iot/ingreso/<id_origen>` con la cabecera `X-Token`. Cuerpo genérico:

    {"lecturas": {"temp": 21.4, "puerta": 1}, "hora": "2026-11-23T10:15:00-03:00"}

Las claves son nombres de canal del origen; `hora` es opcional (si falta, la del servidor). Con `formato: chirpstack` el
cuerpo es el de la HTTP integration de ChirpStack y las lecturas salen de su campo `object` (el payload ya decodificado por el
códec del nodo). Un canal que el origen no declaró se ignora y se cuenta en la respuesta (`ignorados`), no es un error.

El endpoint escribe en las mismas tablas que el poller: `digitales_estado` y `eventos_di` (latido y cambios) para los
digitales, y `lecturas_sensor` para los analógicos con sensor asignado. Es público (el ESP32 no tiene sesión) y se agrega a la
lista de rutas públicas **por prefijo**; su única defensa es el token (se compara con `hmac.compare_digest`), así que:
token aleatorio de 32 bytes que genera el servidor y se ve una sola vez al crearlo; token incorrecto = 403 sin decir si el
origen existe; no se loguea el token.

### 5. Comandar un ESP32 (origen HTTP con `puede_comandar`)

El ESP32 no recibe conexiones: **consulta** sus órdenes.

* `GET /api/iot/ingreso/<id>/ordenes` (token) devuelve las órdenes pendientes: `{id, canal, accion, duracion_s}` con `accion` en
  `pulso`, `prender`, `apagar`.
* `POST /api/iot/ingreso/<id>/ordenes/<orden>/ok` (token) confirma que las ejecutó (o `{"error": "..."}` si no pudo).
* Desde el panel y la pantalla, el botón de una salida de un origen HTTP encola la orden en `comandos_actuador` (tabla que ya
  existe; se le suma `origen` y `vence`). `iot_lavado.ejecutar_comandos_pendientes` sigue atendiendo las del `principal`.

**Una orden vence.** Un ESP32 sin red que vuelve a los 20 minutos no puede disparar un pulso viejo sobre un equipo real: pasado
`vencimiento_orden_s` la orden se marca «vencida» y no se entrega nunca. **Ese valor no tiene un defecto**: al tildar
«puede comandar» el tambo tiene que cargarlo (depende de cada cuánto consulta su ESP32; mismo criterio que el resto de los
umbrales de la app). El panel muestra cada orden como pendiente, ejecutada, con error o vencida; nunca queda en «enviada» sin
saber qué pasó.

### 6. Pantalla de configuración (⚙ Configuración › 🔌 Entradas/Salidas)

* **Orígenes**: lista con alta, baja y estado de cada uno (última lectura recibida, error de conexión). Para un origen HTTP
  muestra el token una vez y la URL de ingreso lista para copiar.
* **Mapa de canales** por origen, tres tablas (entradas digitales, entradas analógicas, salidas), con las columnas de arriba.
  Para el `principal` aparecen los valores generados y se resaltan los que el tambo cambió. Botón «Cargar mapa PUSR M300» que
  vuelve a los valores de siempre.
* Las dos pantallas que faltaban (digitales y salidas) son estas tablas: mismo patrón que la de analógicas.

### 7. Cambios en `iot_lavado.py`

Pasa de un `ModbusTcpClient` a uno por origen Modbus (hoy es uno). En cada ciclo recorre los orígenes Modbus; un origen caído
no frena a los demás ni al `principal`. Lo que se lee sigue limitado a lo usado (`canales_a_leer`, `actuadores_en_uso`): ahora
por origen. Mantiene el `client.close()` ante error, que fuerza la reconexión. Los orígenes se vuelven a leer en cada ciclo,
salvo la IP y el puerto, que como hoy piden reiniciar el proceso.

## Etapas (cada una se entrega y se prueba sola)

1. **Mapa Modbus por canal del `principal`** + las tablas de digitales y salidas en la web. Es el pedido original; no cambia
   nada si no se edita.
2. **Más de un equipo Modbus**: orígenes `modbus_tcp` adicionales y un cliente por origen en `iot_lavado`.
3. **Origen HTTP de solo lectura**: ingreso con token, formato genérico y ChirpStack, ventana de lectura por origen.
4. **Comandar un ESP32**: cola con vencimiento, consulta y confirmación, y el estado de cada orden en el panel.

## Pruebas que no se negocian

* Sin ningún archivo nuevo, las lecturas y escrituras Modbus del `principal` son las de hoy (misma función, dirección y unit ID).
* Un origen Modbus que no responde no frena al `principal`.
* Token inválido, ausente o de otro origen: 403 y nada se escribe.
* Una orden vencida no se entrega; una orden entregada no se entrega dos veces.
* Un nodo con `ventana_lectura_min` de 90 no figura «mudo» a los 60 min; uno del `principal` sí a los 31.
* Un canal repetido entre sensores, o una entrada de un origen que se borró, se rechaza al guardar con un mensaje claro.

## Lo que queda para quien construya (decisiones menores, tomadas acá)

* Un ChirpStack = un origen HTTP (un token por aplicación); los nodos se distinguen por el nombre del campo en `object`.
* Los canales de un origen HTTP se **declaran** antes de usarse (un nombre que llega sin declarar se ignora): evita que un
  error de tipeo en el firmware cree sensores fantasma.
* `pulso_s` por salida reemplaza al `DURACION_PULSO_S` fijo solo para las salidas que el tambo edite.
