# API Colibrí: introducción y autenticación

> Manual interno del equipo de Plataforma de Pagos de Tienda Austral. Documento ficticio
> creado como dataset de ejemplo para la Pre-entrega 3 del curso AI Engineering.

## Qué es Colibrí

Colibrí es la API interna que centraliza todos los cobros, reembolsos y conciliaciones de
Tienda Austral. Antes de Colibrí, cada equipo (tienda online, app móvil, locales físicos y
mayoristas) hablaba directamente con tres pasarelas de pago distintas, cada una con su propio
formato de errores, sus propios reintentos y su propia forma de notificar un pago aprobado.
El resultado eran cobros duplicados, reembolsos que nadie podía rastrear y un cierre contable
mensual que llevaba cuatro días de trabajo manual.

Colibrí resuelve eso con una sola interfaz REST sobre HTTPS. Los equipos consumidores nunca
hablan con las pasarelas: le piden a Colibrí que cree un pago y Colibrí elige la pasarela,
aplica las reglas antifraude, guarda el registro contable y avisa por webhook cuando el
estado cambia. El nombre viene del primer prototipo, que se escribió en un fin de semana y
era "chiquito y rápido".

La API la mantiene el equipo de Plataforma de Pagos. El canal de soporte es
`#colibri-soporte` en el chat interno, y las incidencias se cargan en el tablero PAGOS del
gestor de tickets. Para pedidos de acceso nuevos, completá el formulario "Alta de cliente
Colibrí" en el portal de desarrolladores interno.

## Ambientes

Colibrí tiene tres ambientes, completamente aislados entre sí. Las credenciales de un
ambiente no sirven en otro.

| Ambiente | URL base | Para qué sirve |
|---|---|---|
| Sandbox | `https://sandbox.colibri.austral.internal/v2` | Desarrollo y pruebas automáticas. Usa pasarelas simuladas: nunca se mueve plata real. |
| Staging | `https://staging.colibri.austral.internal/v2` | Pruebas de integración antes de salir a producción. Usa las pasarelas reales en modo prueba. |
| Producción | `https://api.colibri.austral.internal/v2` | Tráfico real de clientes. |

En sandbox existen tarjetas de prueba con comportamientos fijos. La tarjeta que termina en
`4242` siempre se aprueba, la que termina en `0002` siempre se rechaza por fondos
insuficientes y la que termina en `3220` siempre pide autenticación adicional (3-D Secure).
Los montos terminados en `.99` en sandbox simulan un timeout de la pasarela, lo que sirve
para probar la lógica de reintentos e idempotencia de tu servicio.

Los datos de sandbox se borran todos los domingos a las 03:00 (hora de Buenos Aires). Si
tus pruebas automáticas dependen de datos creados previamente, creá los datos al principio
de cada corrida en lugar de suponer que siguen existiendo.

Para pasar a producción, tu servicio tiene que haber corrido al menos una semana en staging
sin errores 5xx atribuibles al cliente y tiene que tener implementada la verificación de
firma de webhooks. La revisión la hace una persona del equipo de Plataforma de Pagos y
suele demorar dos días hábiles.

## Autenticación con OAuth 2.0

Colibrí usa OAuth 2.0 con el flujo *client credentials*: la autenticación es entre
servicios, nunca en nombre de un usuario final. Cada servicio consumidor recibe un
`client_id` y un `client_secret` por ambiente cuando se aprueba el alta.

Para obtener un token de acceso, hacé un `POST` al endpoint de tokens del ambiente
correspondiente, por ejemplo `https://auth.colibri.austral.internal/oauth/token` en
producción, con el cuerpo en formato `application/x-www-form-urlencoded`:

```
grant_type=client_credentials
client_id=<tu client_id>
client_secret=<tu client_secret>
scope=pagos:escritura reembolsos:escritura
```

La respuesta incluye el `access_token`, el tipo (`Bearer`) y el campo `expires_in`. Los
tokens de acceso duran **15 minutos** (900 segundos). Después de ese tiempo, cualquier
request con ese token devuelve `401 Unauthorized` con el código de error
`token_expirado`.

Cada request a la API tiene que llevar el token en el encabezado
`Authorization: Bearer <access_token>`. Además, todas las requests tienen que incluir el
encabezado `X-Colibri-Cliente` con el nombre corto de tu servicio (por ejemplo
`app-movil` o `checkout-web`), que se usa para métricas y para aplicar los límites de uso.

### Buenas prácticas con los tokens

No pidas un token nuevo por cada request: el endpoint de tokens tiene su propio límite de
**30 solicitudes por minuto por cliente**, y superarlo bloquea la emisión de tokens durante
cinco minutos. Guardá el token en memoria y renovalo cuando falten menos de 60 segundos
para que venza. Si tu servicio corre en varias réplicas, cada réplica puede tener su propio
token; no hace falta compartirlo.

Nunca escribas el `client_secret` en el código ni en archivos de configuración versionados.
Los secretos de Colibrí se guardan en el gestor de secretos corporativo, en la ruta
`pagos/colibri/<ambiente>/<servicio>`, y se inyectan como variables de entorno al iniciar
el contenedor.

## Scopes

Los scopes limitan qué puede hacer cada token. Pedí solo los que tu servicio necesita: el
equipo de Plataforma de Pagos revisa los scopes en el alta y rechaza pedidos excesivos.

| Scope | Permite |
|---|---|
| `pagos:lectura` | Consultar pagos y su historial de estados. |
| `pagos:escritura` | Crear, capturar y cancelar pagos. Incluye `pagos:lectura`. |
| `reembolsos:escritura` | Crear reembolsos totales o parciales. |
| `conciliacion:lectura` | Descargar los reportes diarios de conciliación. |
| `webhooks:admin` | Registrar, modificar y borrar suscripciones a webhooks. |

Si pedís un token con un scope que tu cliente no tiene habilitado, el endpoint de tokens
responde `400 Bad Request` con el error `invalid_scope`. Si usás un token válido para una
operación que requiere un scope que el token no tiene, la API responde `403 Forbidden` con
el código `scope_insuficiente`.

## Rotación de credenciales

Los `client_secret` se rotan obligatoriamente cada **90 días**. Diez días antes del
vencimiento, el equipo dueño del servicio recibe un aviso en su canal y por correo. La
rotación se hace desde el portal de desarrolladores con el botón "Generar secreto nuevo".

Durante la rotación, el secreto anterior y el nuevo conviven por **48 horas**. Ese período
de gracia existe para que puedas desplegar el secreto nuevo en todas las réplicas sin
cortar el servicio. Pasadas las 48 horas, el secreto viejo deja de funcionar y el endpoint
de tokens devuelve `401` con el error `invalid_client`.

Si sospechás que un secreto se filtró (por ejemplo, porque apareció en un log o en un
repositorio), no esperes la rotación programada: revocalo de inmediato desde el portal con
"Revocar ahora". La revocación inmediata no tiene período de gracia, así que coordiná el
despliegue del secreto nuevo antes de hacerla si podés, y avisá en `#colibri-soporte`.

## Versionado de la API

La versión actual es la `v2`, que va en la URL. La `v1` quedó deprecada en marzo de 2026 y
se apaga definitivamente el 30 de noviembre de 2026; desde esa fecha cualquier request a
`/v1` devuelve `410 Gone`. Los cambios compatibles (campos nuevos en las respuestas,
endpoints nuevos) se agregan a la `v2` sin aviso previo, así que tu cliente tiene que
ignorar los campos que no conoce en lugar de fallar.

Los cambios incompatibles solo se publican en una versión mayor nueva, con al menos seis
meses de convivencia entre versiones y un anuncio en la lista de correo
`colibri-anuncios`. Suscribite a esa lista si mantenés un servicio que usa Colibrí.
