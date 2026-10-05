# API Colibrí: límites de uso, errores y reintentos

> Manual interno del equipo de Plataforma de Pagos de Tienda Austral. Documento ficticio
> creado como dataset de ejemplo para la Pre-entrega 3 del curso AI Engineering.

## Planes y límites de uso

Cada servicio consumidor tiene asignado un plan que define cuántas solicitudes puede hacer.
Los límites se cuentan por cliente (el `client_id` del token), sumando todas las réplicas
del servicio, y se aplican con una ventana deslizante de 60 segundos.

| Plan | Solicitudes por minuto | Ráfaga máxima (por segundo) | Para quién |
|---|---|---|---|
| Básico | 60 | 5 | Herramientas internas, scripts y backoffice. |
| Estándar | 300 | 20 | La mayoría de los servicios de producción. |
| Alto volumen | 1500 | 100 | Checkout web y app móvil. |

Todos los servicios nuevos arrancan en el plan **Básico**. Para pasar al plan Estándar
tenés que pedirlo en el tablero PAGOS con una estimación del tráfico esperado. El plan Alto
volumen requiere además una prueba de carga en staging coordinada con el equipo de
Plataforma de Pagos, porque comparte capacidad con las pasarelas.

Durante los eventos de ventas especiales (por ejemplo, el aniversario de la tienda o las
fechas de descuentos masivos), los límites de los planes Estándar y Alto volumen se
duplican automáticamente entre las 00:00 y las 23:59 del día del evento. El calendario de
eventos se publica en `colibri-anuncios` con un mes de anticipación.

### Encabezados de límite

Todas las respuestas incluyen tres encabezados para que tu servicio sepa cuánto margen le
queda:

- `X-RateLimit-Limit`: el límite de solicitudes por minuto de tu plan.
- `X-RateLimit-Remaining`: cuántas solicitudes te quedan en la ventana actual.
- `X-RateLimit-Reset`: segundos hasta que la ventana se renueve por completo.

Una buena práctica es reducir la concurrencia de tu servicio cuando `X-RateLimit-Remaining`
baja del 10 % del límite, en lugar de esperar a recibir errores.

## Qué hacer cuando recibís un 429

Cuando superás el límite de tu plan, Colibrí responde `429 Too Many Requests` con el código
de error `limite_excedido` y el encabezado `Retry-After`, que indica cuántos segundos tenés
que esperar antes de volver a intentar.

Ante un 429, tu servicio tiene que:

1. **Respetar `Retry-After`**: no reintentes antes de que pasen esos segundos. Reintentar
   antes no sirve (la request vuelve a fallar) y además cuenta como una solicitud más.
2. **Usar backoff exponencial con jitter** si tenés que reintentar varias veces: esperá
   `Retry-After` la primera vez y después duplicá la espera en cada intento, sumándole un
   componente aleatorio de hasta un segundo para que todas las réplicas no reintenten al
   mismo tiempo.
3. **Cortar después de 5 intentos** como máximo. Si después de cinco intentos seguís
   recibiendo 429, registrá el error, devolvé una respuesta controlada a tu usuario y
   revisá si tu servicio necesita un plan más alto o si está haciendo polling de más.
4. **Reusar la misma `Idempotency-Key`** al reintentar una operación de escritura, para
   no duplicar el cobro.

Si tu servicio recibe más de 100 respuestas 429 en una hora, el equipo de Plataforma de
Pagos recibe una alerta automática y probablemente te contacte para revisar la integración.

## Formato de los errores

Todos los errores tienen el mismo formato JSON, sin importar la pasarela que haya detrás:

```json
{
  "error": {
    "codigo": "fondos_insuficientes",
    "mensaje": "La tarjeta no tiene fondos suficientes.",
    "id_solicitud": "sol_3k9d0f2m1x",
    "reintentable": false
  }
}
```

El campo `codigo` es estable y es el que tiene que usar tu código para decidir qué hacer;
el `mensaje` puede cambiar y es solo para personas. Cuando abras un ticket en el tablero
PAGOS, incluí siempre el `id_solicitud`: con ese identificador se encuentra la request en
los logs en segundos. El campo `reintentable` indica si tiene sentido volver a intentar la
misma operación.

## Códigos de error frecuentes

| HTTP | Código | Qué significa | ¿Reintentar? |
|---|---|---|---|
| 400 | `falta_idempotency_key` | La operación de escritura no tiene `Idempotency-Key`. | No: corregí la request. |
| 400 | `cuerpo_invalido` | El JSON no cumple el esquema; el mensaje dice qué campo falla. | No. |
| 401 | `token_expirado` | El token de acceso venció (duran 15 minutos). | Sí, con un token nuevo. |
| 403 | `scope_insuficiente` | El token no tiene el scope que la operación requiere. | No. |
| 404 | `pago_inexistente` | No existe un pago con ese id en este ambiente. | No. |
| 409 | `idempotency_key_reutilizada` | Misma clave con un cuerpo distinto. | No. |
| 409 | `operacion_en_curso` | La primera request con esa clave todavía se procesa. | Sí, en unos segundos. |
| 422 | `fondos_insuficientes` | La pasarela rechazó el pago por falta de fondos. | No. |
| 422 | `estado_invalido` | La operación no aplica al estado actual del pago. | No. |
| 429 | `limite_excedido` | Superaste el límite de tu plan. | Sí, respetando `Retry-After`. |
| 502 | `pasarela_no_disponible` | La pasarela de pago no respondió. | Sí, con backoff. |
| 503 | `mantenimiento` | Colibrí está en una ventana de mantenimiento. | Sí, después de la ventana. |

Los errores 4xx, salvo `token_expirado`, `operacion_en_curso` y `limite_excedido`, son
permanentes: reintentar la misma request va a dar el mismo error. Los 5xx son transitorios
y se pueden reintentar con backoff exponencial, siempre con la misma `Idempotency-Key`.

## Timeouts

Configurá en tu cliente HTTP un timeout de conexión de **3 segundos** y un timeout de
lectura de **30 segundos**. La creación de un pago puede tardar hasta 20 segundos cuando la
pasarela pide validaciones adicionales, así que un timeout de lectura más corto produce
cortes falsos.

Si una request de escritura se corta por timeout, no sabés si Colibrí la procesó o no. No
asumas que falló: reintentá con la misma `Idempotency-Key` (vas a recibir el resultado
original si ya se había procesado) o consultá el pago con `GET /pagos?referencia_externa=`.

## Ventanas de mantenimiento

Colibrí tiene una ventana de mantenimiento programada el **primer martes de cada mes,
entre las 02:00 y las 04:00** (hora de Buenos Aires). Durante la ventana, la API puede
responder `503` con el código `mantenimiento` y el encabezado `Retry-After`. Las ventanas
extraordinarias se anuncian en `colibri-anuncios` con al menos 72 horas de anticipación.

Si tu servicio procesa cobros en lote (por ejemplo, suscripciones), programalos fuera de
esa franja. Los procesos en lote que fallan por mantenimiento no se reintentan solos: es
responsabilidad de cada servicio volver a encolarlos.
