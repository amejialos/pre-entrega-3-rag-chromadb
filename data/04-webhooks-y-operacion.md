# API Colibrí: webhooks, monitoreo y operación

> Manual interno del equipo de Plataforma de Pagos de Tienda Austral. Documento ficticio
> creado como dataset de ejemplo para la Pre-entrega 3 del curso AI Engineering.

## Para qué sirven los webhooks

Muchos cambios de estado en Colibrí ocurren después de que tu request terminó: una
autorización que vence, un pago que sale de revisión manual, un reembolso que se acredita
días más tarde. En lugar de consultar la API una y otra vez, tu servicio registra una URL y
Colibrí le avisa con un `POST` cada vez que pasa algo relevante.

Las suscripciones se administran con `POST /webhooks`, `GET /webhooks` y
`DELETE /webhooks/{id}`, y requieren el scope `webhooks:admin`. Cada suscripción indica la
URL de destino (obligatoriamente HTTPS) y la lista de eventos que querés recibir. Un mismo
servicio puede tener hasta 10 suscripciones por ambiente.

## Eventos disponibles

| Evento | Cuándo se envía |
|---|---|
| `pago.autorizado` | La pasarela reservó los fondos. |
| `pago.capturado` | Se cobró el pago (captura automática o manual). |
| `pago.rechazado` | La pasarela o el antifraude rechazaron el pago. |
| `pago.cancelado` | Se canceló una autorización, a mano o por vencimiento a los 7 días. |
| `pago.revision_manual` | El pago quedó esperando aprobación humana. |
| `reembolso.actualizado` | Un reembolso pasó a `acreditado` o a `fallido`. |
| `conciliacion.disponible` | Ya se puede descargar el reporte diario de conciliación. |

El cuerpo de cada notificación tiene el campo `evento`, el campo `creado_en` (fecha ISO 8601
en UTC), un `id_evento` único con el prefijo `evt_` y el objeto completo afectado en el
campo `datos`. Colibrí manda el objeto completo para que no tengas que hacer un `GET`
adicional.

## Verificación de la firma

Cada notificación incluye el encabezado `X-Colibri-Firma`, que es un HMAC-SHA256 del cuerpo
crudo de la request calculado con el secreto de la suscripción. El secreto se muestra una
sola vez, cuando creás la suscripción; guardalo en el gestor de secretos.

Para verificar una notificación:

1. Leé el cuerpo **crudo**, tal como llegó, antes de parsear el JSON. Si lo parseás y lo
   volvés a serializar, el orden de los campos o los espacios pueden cambiar y la firma no
   va a coincidir.
2. Calculá el HMAC-SHA256 de ese cuerpo con el secreto y codificalo en hexadecimal.
3. Compará tu resultado con el encabezado usando una comparación de tiempo constante (por
   ejemplo, `hmac.compare_digest` en Python), nunca con `==`.
4. Verificá también el encabezado `X-Colibri-Timestamp` y descartá notificaciones con más
   de **5 minutos** de antigüedad, para evitar ataques de repetición.

Una notificación con firma inválida se descarta y se responde `401`. La verificación de
firma es obligatoria para pasar a producción.

## Reintentos de entrega

Tu endpoint tiene que responder con un código 2xx en menos de **10 segundos**. Si responde
otro código o tarda más, Colibrí considera que la entrega falló y la reintenta con backoff
exponencial: a los 30 segundos, 2 minutos, 10 minutos, 1 hora, 6 horas y 24 horas. Después
de esos seis reintentos, la notificación se marca como no entregada y aparece en el panel
"Webhooks fallidos" del portal de desarrolladores, desde donde se puede reenviar a mano.

Como hay reintentos, **la misma notificación puede llegar más de una vez**, y no siempre en
orden. Tu servicio tiene que ser idempotente: guardá los `id_evento` ya procesados y, si
llega uno repetido, respondé 200 sin volver a procesarlo. Para el orden, compará el campo
`creado_en` del evento con la última actualización que registraste y descartá eventos más
viejos.

La forma recomendada de procesar webhooks es responder 200 apenas validás la firma y
encolar el trabajo pesado (actualizar el pedido, mandar correos) en una cola interna. Así
nunca superás los 10 segundos aunque tu base de datos esté lenta.

Si una suscripción acumula más de 1000 entregas fallidas seguidas, Colibrí la desactiva
automáticamente y avisa por correo al equipo dueño. Reactivarla requiere corregir el
endpoint y pedirlo en el tablero PAGOS.

## Monitoreo

El portal de desarrolladores tiene un tablero por servicio con el volumen de requests, la
tasa de errores por código, la latencia (percentiles 50, 95 y 99) y el estado de las
entregas de webhooks. Los mismos datos están disponibles como métricas con el prefijo
`colibri_cliente_` en el sistema de monitoreo corporativo, para que puedas armar tus
propias alertas.

Recomendamos tener como mínimo dos alertas propias: una cuando la tasa de errores 5xx de
tu servicio contra Colibrí supere el 2 % durante 5 minutos, y otra cuando haya webhooks no
entregados durante más de 30 minutos.

La página de estado `estado.colibri.austral.internal` muestra en tiempo real la
disponibilidad de la API y de cada pasarela. Antes de abrir un incidente, revisá ahí si ya
hay uno declarado.

## Acuerdo de nivel de servicio

Colibrí se compromete a una disponibilidad mensual del **99,95 %** en producción, sin
contar las ventanas de mantenimiento programadas. Eso equivale a unos 22 minutos de caída
permitida por mes. La latencia objetivo es de 300 milisegundos en el percentil 95 para las
operaciones de lectura y de 2 segundos para la creación de pagos (que depende de la
pasarela).

## Guardia e incidentes

El equipo de Plataforma de Pagos tiene una guardia de 24 horas, todos los días. Para
incidentes que afectan cobros en producción, llamá a la guardia por el sistema de alertas
con el servicio "Colibrí - Producción". Para todo lo demás, usá `#colibri-soporte` en
horario laboral (de lunes a viernes, de 9 a 18).

Los incidentes se clasifican por severidad:

- **SEV1**: no se puede cobrar en un canal completo (por ejemplo, todo el checkout web).
  La guardia responde en 15 minutos y se abre una sala de crisis.
- **SEV2**: una pasarela o un medio de pago falla, pero hay alternativas. Respuesta en
  1 hora.
- **SEV3**: errores aislados, degradación menor o problemas en staging o sandbox.
  Respuesta el siguiente día hábil.

Después de cada SEV1 o SEV2 se escribe un análisis post-incidente sin culpables dentro de
los 5 días hábiles, con la línea de tiempo, la causa raíz y las acciones para que no se
repita. Los análisis se publican en el espacio "Postmortems Pagos" de la wiki interna.
