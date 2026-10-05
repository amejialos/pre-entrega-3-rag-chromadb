# Evidencia de ejecución

Corridas reales del 2026-10-05 contra Gemini, por su endpoint compatible con la API
de OpenAI (`ChatOpenAI` + `OpenAIEmbeddings`, sin cambiar código).

- Embeddings: `gemini-embedding-001` (el mismo para indexar y para consultar).
- LLM: `gemini-3.5-flash`, pasado por variable de entorno al correr
  (`OPENAI_MODEL=gemini-3.5-flash uv run python main.py`). El modelo del `.env`,
  `gemini-3.6-flash`, había agotado ese día la cuota gratuita (20 requests diarias por
  modelo) y devolvía `429 RESOURCE_EXHAUSTED`. Las variables de entorno ya definidas
  tienen prioridad sobre el `.env` porque `load_dotenv()` no las pisa.

La ruta absoluta del proyecto se abrevió como `<proyecto>`.

## Corrida 1: base vacía, se indexa

Se borró `vectorstore/` antes de correr. Hay tres llamadas a `embeddings`: una con los
20 fragmentos (la ingesta, en un solo lote) y una por cada pregunta.

```
$ rm -rf vectorstore && OPENAI_MODEL=gemini-3.5-flash uv run python main.py
INFO    httpx2: HTTP Request: POST https://generativelanguage.googleapis.com/v1beta/openai/embeddings "HTTP/1.1 200 OK"
INFO    rag.ingesta: Indexados 20 fragmentos de 4 archivos con gemini-embedding-001.
Ingesta: 20 fragmentos indexados

=== Pregunta con respuesta en los documentos ===
Pregunta: ¿Cuántas solicitudes por minuto permite el plan Estándar y qué tengo que hacer si recibo un error 429?
INFO    rag.cadena: Consulta: ¿Cuántas solicitudes por minuto permite el plan Estándar y qué tengo que hacer si recibo un error 429?
INFO    httpx2: HTTP Request: POST https://generativelanguage.googleapis.com/v1beta/openai/embeddings "HTTP/1.1 200 OK"
INFO    httpx2: HTTP Request: POST https://generativelanguage.googleapis.com/v1beta/openai/chat/completions "HTTP/1.1 200 OK"
INFO    rag.cadena: Referencias: ['03-limites-y-errores.md#0', '03-limites-y-errores.md#1']
{
  "respuesta": "El plan Estándar permite 300 solicitudes por minuto. Ante un error 429, tu servicio debe:\n1. Respetar el encabezado `Retry-After` sin reintentar antes de tiempo.\n2. Usar backoff exponencial con jitter si se reintenta varias veces.\n3. Cortar después de un máximo de 5 intentos.\n4. Reusar la misma `Idempotency-Key` en operaciones de escritura.",
  "referencias": [
    "03-limites-y-errores.md#0",
    "03-limites-y-errores.md#1"
  ]
}
Verificación (respuesta con referencias): OK

=== Pregunta trampa (fuera de los documentos) ===
Pregunta: ¿Cuál es la receta tradicional del locro argentino y cuánto tiempo hay que cocinarlo?
INFO    rag.cadena: Consulta: ¿Cuál es la receta tradicional del locro argentino y cuánto tiempo hay que cocinarlo?
INFO    httpx2: HTTP Request: POST https://generativelanguage.googleapis.com/v1beta/openai/embeddings "HTTP/1.1 200 OK"
INFO    httpx2: HTTP Request: POST https://generativelanguage.googleapis.com/v1beta/openai/chat/completions "HTTP/1.1 200 OK"
INFO    rag.cadena: Referencias: (ninguna)
{
  "respuesta": "No lo sé.",
  "referencias": []
}
Verificación ("No lo sé." sin referencias): OK
```

## Corrida 2: la base ya existe, no se reindexa

Mismo comando sin borrar `vectorstore/`. La ingesta detecta la colección y no llama al
modelo de embeddings para indexar: solo hay una llamada a `embeddings` por pregunta.
En la pregunta trampa se ve además un `503` de Gemini (sobrecarga) que el SDK de
OpenAI reintentó solo (`max_retries=5` en `config.build_llm`).

```
$ OPENAI_MODEL=gemini-3.5-flash uv run python main.py
INFO    rag.ingesta: La colección 'manual_colibri' ya existe en <proyecto>/vectorstore: no se reindexa.
Ingesta: la base ya existía, no se reindexó

=== Pregunta con respuesta en los documentos ===
Pregunta: ¿Cuántas solicitudes por minuto permite el plan Estándar y qué tengo que hacer si recibo un error 429?
INFO    rag.cadena: Consulta: ¿Cuántas solicitudes por minuto permite el plan Estándar y qué tengo que hacer si recibo un error 429?
INFO    httpx2: HTTP Request: POST https://generativelanguage.googleapis.com/v1beta/openai/embeddings "HTTP/1.1 200 OK"
INFO    httpx2: HTTP Request: POST https://generativelanguage.googleapis.com/v1beta/openai/chat/completions "HTTP/1.1 200 OK"
INFO    rag.cadena: Referencias: ['03-limites-y-errores.md#0', '03-limites-y-errores.md#1']
{
  "respuesta": "El plan Estándar permite 300 solicitudes por minuto. Ante un error 429 (límite excedido), tu servicio debe:\n1. Respetar el encabezado `Retry-After` y no reintentar antes del tiempo indicado.\n2. Usar backoff exponencial con jitter si se requiere reintentar varias veces.\n3. Cortar los reintentos después de un máximo de 5 intentos.\n4. Reusar la misma `Idempotency-Key` al reintentar operaciones de escritura.",
  "referencias": [
    "03-limites-y-errores.md#0",
    "03-limites-y-errores.md#1"
  ]
}
Verificación (respuesta con referencias): OK

=== Pregunta trampa (fuera de los documentos) ===
Pregunta: ¿Cuál es la receta tradicional del locro argentino y cuánto tiempo hay que cocinarlo?
INFO    rag.cadena: Consulta: ¿Cuál es la receta tradicional del locro argentino y cuánto tiempo hay que cocinarlo?
INFO    httpx2: HTTP Request: POST https://generativelanguage.googleapis.com/v1beta/openai/embeddings "HTTP/1.1 200 OK"
INFO    httpx2: HTTP Request: POST https://generativelanguage.googleapis.com/v1beta/openai/chat/completions "HTTP/1.1 503 Service Unavailable"
INFO    openai._base_client: Retrying request in 0.496495 seconds (retry 1 of 5)
INFO    httpx2: HTTP Request: POST https://generativelanguage.googleapis.com/v1beta/openai/chat/completions "HTTP/1.1 200 OK"
INFO    rag.cadena: Referencias: (ninguna)
{
  "respuesta": "No lo sé.",
  "referencias": []
}
Verificación ("No lo sé." sin referencias): OK
```

## Lo que muestran

- **Pregunta real**: respuesta correcta (300 solicitudes por minuto y los cuatro pasos
  ante un 429) con referencias a los fragmentos `03-limites-y-errores.md#0` y `#1`, que
  son los que contienen la tabla de planes y la sección "Qué hacer cuando recibís un 429".
- **Pregunta trampa**: el modelo conoce la receta del locro, pero respondió
  `"No lo sé."` sin referencias porque el contexto recuperado (fragmentos del manual de
  Colibrí) no habla de eso. El filtro de veracidad del prompt funcionó.
- **Sin reindexado**: la segunda corrida no volvió a embeber los documentos.
