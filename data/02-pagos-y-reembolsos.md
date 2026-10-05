# API Colibrí: pagos y reembolsos

> Manual interno del equipo de Plataforma de Pagos de Tienda Austral. Documento ficticio
> creado como dataset de ejemplo para la Pre-entrega 3 del curso AI Engineering.

## Modelo de un pago

Un pago en Colibrí representa la intención de cobrarle un monto a un cliente con un medio
de pago determinado. Cada pago tiene un identificador con el prefijo `pag_` seguido de 24
caracteres alfanuméricos, por ejemplo `pag_8f2k1m9q0z7x3c5v6b4n2a1s`. Los montos siempre
se expresan en **centavos** y como número entero, para evitar errores de redondeo: un cobro
de 1.250,50 pesos se envía como `125050`. La moneda va en el campo `moneda` con el código
ISO 4217 (`ARS`, `USD`, `UYU`). Hoy Colibrí acepta esas tres monedas; cualquier otra
devuelve `422` con el código `moneda_no_soportada`.

El monto mínimo de un pago es de 100 centavos (un peso) y el máximo, sin aprobación
manual, es de 5.000.000 de pesos (500.000.000 centavos). Los pagos por encima de ese monto
quedan en estado `revision_manual` hasta que alguien del equipo de Riesgo los apruebe.

### Estados

Un pago pasa por estos estados:

1. `pendiente`: se creó el pago pero todavía no se envió a la pasarela.
2. `autorizado`: la pasarela reservó los fondos, pero todavía no se cobraron.
3. `capturado`: los fondos se cobraron. Es el estado final de un pago exitoso.
4. `rechazado`: la pasarela o el antifraude rechazaron el pago. Es un estado final.
5. `cancelado`: se liberó una autorización antes de capturarla. Es un estado final.
6. `revision_manual`: el pago espera una aprobación humana (montos altos o alertas de fraude).

Una autorización que no se captura vence a los **7 días** y pasa sola a `cancelado`. Si tu
flujo necesita más tiempo entre la reserva y el cobro (por ejemplo, para productos por
encargo), capturá un monto parcial y creá un pago nuevo por la diferencia.

## Crear un pago

Para crear un pago hacé un `POST /pagos` con un cuerpo JSON como este:

```json
{
  "monto": 125050,
  "moneda": "ARS",
  "medio_de_pago": {"tipo": "tarjeta", "token": "tok_visa_4242"},
  "captura": "automatica",
  "referencia_externa": "pedido-98123",
  "descripcion": "Pedido web 98123"
}
```

El campo `captura` acepta `automatica` (autoriza y captura en un solo paso) o `manual`
(solo autoriza; después tenés que llamar a `POST /pagos/{id}/captura`). La captura manual
se usa cuando el monto final puede cambiar, por ejemplo si falta confirmar el stock.

El `token` del medio de pago nunca es el número de tarjeta: lo genera el SDK de
tokenización en el navegador o en la app, para que los datos de la tarjeta no pasen por
los servidores de Tienda Austral. Si recibís números de tarjeta en claro, frená la
integración y avisá al equipo de Seguridad.

La respuesta es `201 Created` con el pago completo, incluido su `id` y su `estado`. Que la
respuesta sea 201 no significa que el pago se aprobó: revisá siempre el campo `estado`.

## Idempotencia

Las operaciones que crean o modifican algo (`POST` de pagos, capturas, cancelaciones y
reembolsos) **requieren** el encabezado `Idempotency-Key`. Sin ese encabezado, la API
responde `400 Bad Request` con el código `falta_idempotency_key`.

La clave tiene que ser un UUID v4 generado por tu servicio para cada operación de negocio,
no para cada intento. Si una request se corta por un timeout y la reintentás, mandá la
**misma** clave: Colibrí detecta que ya procesó esa operación y devuelve la misma respuesta
que la primera vez, en lugar de cobrar dos veces. Por eso conviene guardar la clave junto
al pedido antes de llamar a la API.

Las claves de idempotencia se guardan durante **24 horas**. Si reusás una clave con un
cuerpo distinto al original, la API responde `409 Conflict` con el código
`idempotency_key_reutilizada`. Si reusás una clave mientras la primera request todavía se
está procesando, la respuesta es `409` con el código `operacion_en_curso`; esperá unos
segundos y volvé a intentar con la misma clave.

## Consultar y listar pagos

`GET /pagos/{id}` devuelve un pago con su historial de estados. `GET /pagos` lista pagos y
acepta los filtros `estado`, `desde`, `hasta` (fechas en formato ISO 8601) y
`referencia_externa`.

El listado usa **paginación por cursor**, no por número de página. Cada respuesta trae
como máximo `limite` elementos (por defecto 50, máximo 200) y un campo `siguiente_cursor`.
Para traer la página siguiente, mandá ese valor en el parámetro `cursor`. Cuando
`siguiente_cursor` viene en `null`, no hay más resultados. Los cursores vencen a los 10
minutos; si recorrés listados grandes, no los guardes para retomarlos más tarde.

No uses el listado para "esperar" a que un pago cambie de estado consultándolo una y otra
vez: para eso están los webhooks. El polling intensivo consume tu cuota de requests y es
la causa más común de errores 429 entre los equipos nuevos.

## Cancelar una autorización

Un pago en estado `autorizado` se puede cancelar con `POST /pagos/{id}/cancelacion`. La
cancelación libera los fondos reservados en la tarjeta del cliente. Un pago `capturado` no
se puede cancelar: para devolver la plata hay que hacer un reembolso. Intentar cancelar un
pago capturado devuelve `422` con el código `estado_invalido`.

## Reembolsos

Los reembolsos se crean con `POST /pagos/{id}/reembolsos` y siempre requieren el scope
`reembolsos:escritura`. El cuerpo indica el monto en centavos y un motivo:

```json
{"monto": 50000, "motivo": "producto_danado"}
```

Los motivos válidos son `producto_danado`, `arrepentimiento`, `cobro_duplicado`,
`producto_no_entregado` y `otro`. Si usás `otro`, el campo `detalle` es obligatorio.

Un pago admite **reembolsos parciales** sucesivos hasta completar el monto capturado. La
suma de todos los reembolsos nunca puede superar el monto original; si lo intentás, la
respuesta es `422` con el código `monto_excede_capturado`. Solo se pueden reembolsar pagos
capturados en los últimos **180 días**; después de ese plazo, el reembolso se gestiona por
transferencia bancaria a través del equipo de Atención al Cliente.

Los reembolsos se procesan de forma asíncrona. La respuesta inmediata es `202 Accepted` con
el reembolso en estado `en_proceso`, y el resultado final (`acreditado` o `fallido`) llega
por el webhook `reembolso.actualizado`. En tarjetas de crédito, el dinero puede tardar
hasta dos resúmenes en verse reflejado; eso depende del banco emisor, no de Colibrí.

Los reembolsos por más de 1.000.000 de pesos requieren una segunda aprobación de alguien
con rol de supervisor de Atención al Cliente, que se hace desde el backoffice.

## Conciliación

Todos los días a las 06:00 se genera un reporte de conciliación con los pagos capturados y
los reembolsos acreditados del día anterior, agrupados por pasarela. Se descarga con
`GET /conciliacion/{fecha}` (scope `conciliacion:lectura`) en formato CSV. El equipo de
Contabilidad usa ese reporte para el cierre diario, así que cualquier diferencia entre el
reporte y los registros de tu servicio tiene que reportarse en el tablero PAGOS dentro de
las 48 horas.
