# Sistema de recuperación semántica local (RAG)

Flujo RAG end-to-end en Python 3.12 + LangChain: recibe una consulta, busca en una
base vectorial local (ChromaDB) ya poblada y genera una respuesta que usa **solo** esa
información. Si la respuesta no está en los documentos, dice `"No lo sé."` en lugar
de inventar. La salida es un objeto **Pydantic** con el texto y las referencias a los
fragmentos usados.

Pre-entrega 3 del curso AI Engineering: "Sistema de recuperación semántica local (RAG)".

## Estructura

```
data/           # dataset: manual ficticio de "Colibrí", una API interna de pagos (4 .md)
config.py       # constantes (chunking, top_k, rutas) y build_llm() / build_embeddings()
ingest.py       # script de ingesta: data/ -> fragmentos de 500 tokens -> ChromaDB (./vectorstore)
schemas.py      # RespuestaRAG: el modelo Pydantic de salida (respuesta + referencias)
rag.py          # cadena RAG asíncrona (LCEL) y get_rag_response(query)
main.py         # prueba end-to-end: pregunta real + pregunta trampa
tests/          # pytest, sin red y sin keys (LLM y embeddings falsos, Chroma en tmp_path)
docs/ejecucion.md  # salida de las corridas reales contra Gemini
.env.example    # variables de entorno
```

## Cómo funciona

### 1. Ingesta (`ingest.py`)

```
data/*.md, data/*.txt
  -> cargar_documentos()      un Document por archivo, con metadata "fuente"
  -> fragmentar()             RecursiveCharacterTextSplitter.from_tiktoken_encoder(
                                  chunk_size=500, chunk_overlap=50)   medido en TOKENS
                              cada fragmento lleva un id "archivo.md#n"
  -> Chroma(persist_directory="./vectorstore").add_documents(...)
```

- **Verifica antes de reindexar.** `base_existente()` abre la carpeta con un cliente
  persistente de Chroma y se fija si la colección existe y tiene fragmentos. Si ya
  está, no vuelve a embeber nada (ver `docs/ejecucion.md`, corrida 2). Para reindexar a
  propósito: `uv run python ingest.py --forzar`.
- **Chunking por tokens, cortando por párrafos.** Los límites de los modelos se miden
  en tokens, no en caracteres, así que el largo se mide con tiktoken (`cl100k_base`).
  Los cortes se hacen entre párrafos y solo bajan a líneas, oraciones o palabras si un
  párrafo no entra. Resultado con el dataset: 20 fragmentos de entre 195 y 496 tokens.
  El solapamiento se arma con párrafos enteros, así que es *de hasta* 50 tokens (puede
  ser 0 cuando el último párrafo del fragmento anterior ya mide más de 50). Se probó
  cortar primero por títulos (`\n## `), pero daba fragmentos de 50 tokens con solo el
  título del archivo y ningún solapamiento.
- **Mismo modelo de embeddings para indexar y consultar.** La colección guarda en su
  metadata con qué modelo se indexó. `abrir_vectorstore()` compara ese nombre con el
  modelo actual y, si no coinciden, falla con un mensaje claro: vectores de modelos
  distintos viven en espacios distintos y la búsqueda devolvería basura sin avisar.

### 2. Retriever + generación grounded (`rag.py`)

```
pregunta
  -> RunnableParallel(documentos=retriever, pregunta=passthrough)   similitud en Chroma, top_k=4
  -> .assign(respuesta =
         formatear_documentos      "[archivo.md#n]\n<texto>" por fragmento
       | PROMPT                    filtro de veracidad + instrucciones del parser
       | llm                       ChatOpenAI, llamada asíncrona (ainvoke)
       | PydanticOutputParser      -> RespuestaRAG)
  -> validar_referencias           descarta referencias que no estaban en el contexto
  -> RespuestaRAG(respuesta, referencias)
```

- **El retriever** convierte la pregunta en embedding con el mismo modelo de la ingesta
  y trae los `TOP_K = 4` fragmentos más parecidos (la consigna pide entre 3 y 5; `RAG`
  rechaza otros valores).
- **El prompt "filtro de veracidad"** le indica al modelo que responda únicamente con
  el contexto y que, si no alcanza, responda exactamente `"No lo sé."` con las
  referencias vacías.
- **`PydanticOutputParser`** agrega al prompt las instrucciones de formato (el esquema
  JSON de `RespuestaRAG`) y parsea la respuesta del modelo, incluso si viene envuelta en
  un bloque ```` ```json ````. Si el modelo devuelve texto libre, falla con
  `OutputParserException`.
- **`validar_referencias`**: como la cadena conserva los documentos recuperados junto
  a la respuesta, después del LLM se comprueba que cada referencia citada exista de
  verdad. Las inventadas se descartan (con un warning) y un `"No lo sé."` nunca sale
  con referencias.

### La función asíncrona

```python
from rag import get_rag_response

respuesta = await get_rag_response("¿Cuánto duran los tokens de acceso?")
respuesta.respuesta      # texto de la respuesta, por ejemplo "Duran 15 minutos (900 segundos)."
respuesta.referencias    # ids de los fragmentos usados, con la forma "archivo.md#n"
respuesta.sin_respuesta  # False (True si respondió "No lo sé.")
```

`get_rag_response(query: str)` usa la base de `./vectorstore` y los modelos del `.env`.
Por dentro delega en la clase `RAG(vectorstore, llm, top_k)`, que recibe todo por
parámetro: así los tests le pasan un LLM falso y una base en una carpeta temporal.

## Instalación

Requiere Python 3.12 o superior.

**Con `uv` (recomendado):**

```bash
uv sync
```

**Con `venv` clásico:**

```bash
python3.12 -m venv .venv
source .venv/bin/activate        # en Windows: .venv\Scripts\activate
pip install langchain-core langchain-openai langchain-chroma langchain-text-splitters \
    chromadb tiktoken openai pydantic python-dotenv pytest pytest-asyncio
```

## Variables de entorno

Copiá `.env.example` a `.env` y completalo. El `.env` está en `.gitignore`: nunca se
sube, y el código no tiene ninguna key escrita.

| Variable | Obligatoria | Default | Para qué |
|---|---|---|---|
| `OPENAI_API_KEY` | Sí | | Key de OpenAI (o de Gemini, ver abajo) |
| `OPENAI_MODEL` | No | `gpt-4o-mini` | Modelo de chat |
| `OPENAI_BASE_URL` | No | | Endpoint alternativo compatible con la API de OpenAI |
| `EMBEDDING_MODEL` | No | `text-embedding-3-small` (o `gemini-embedding-001` si hay `OPENAI_BASE_URL`) | Modelo de embeddings |

Si cambiás `EMBEDDING_MODEL` después de indexar, reindexá con `--forzar`: el sistema
se niega a consultar con un modelo distinto al de la ingesta.

### Probar gratis con Gemini

Google expone un endpoint compatible con la API de OpenAI y una key gratuita en
[Google AI Studio](https://aistudio.google.com/apikey). `ChatOpenAI` y
`OpenAIEmbeddings` hablan con Gemini sin cambiar código:

```
OPENAI_API_KEY=<tu key de Gemini>
OPENAI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
OPENAI_MODEL=gemini-3.6-flash
```

Dos detalles de este endpoint:

- `OpenAIEmbeddings` por defecto tokeniza el texto con tiktoken y manda los **tokens**;
  Gemini solo acepta texto. Por eso `build_embeddings()` usa
  `check_embedding_ctx_length=False`, que manda el texto tal cual (con OpenAI también
  funciona, porque los fragmentos son mucho más cortos que el límite del modelo).
- El tier gratuito tiene cuotas bajas por modelo (unas 20 requests por día y 5 por
  minuto) y devuelve `503` en horas de mucha demanda. El LLM se crea con
  `max_retries=5` para absorber los `503` sueltos. Si un modelo agotó la cuota, podés
  probar con otro sin tocar el `.env`:
  `OPENAI_MODEL=gemini-3.5-flash uv run python main.py`.

## Cómo ejecutarlo

```bash
uv run python ingest.py            # 1. indexa data/ en ./vectorstore (si no estaba)
uv run python main.py              # 2. corre la pregunta real y la pregunta trampa
```

`main.py` también llama a la ingesta, así que con correr solo el paso 2 alcanza. Hace
dos consultas con `get_rag_response`:

1. **Pregunta con respuesta en los documentos**: "¿Cuántas solicitudes por minuto
   permite el plan Estándar y qué tengo que hacer si recibo un error 429?". Tiene que
   volver con la respuesta y referencias.
2. **Pregunta trampa**: "¿Cuál es la receta tradicional del locro argentino y cuánto
   tiempo hay que cocinarlo?". El modelo sabe la respuesta por su cuenta, pero no está
   en los documentos: tiene que decir `"No lo sé."` sin referencias.

Imprime cada `RespuestaRAG` en JSON y una línea `Verificación: OK/FALLA`. Termina con
código 0 si las dos verificaciones pasan. Un error del proveedor (cuota, red) se
informa como `error controlado` sin traceback.

### Salida real (resumida)

Corrida del 2026-10-05 con `gemini-3.5-flash` y `gemini-embedding-001`. La salida
completa de las dos corridas (con y sin reindexado) está en
[`docs/ejecucion.md`](docs/ejecucion.md).

```
Ingesta: 20 fragmentos indexados

=== Pregunta con respuesta en los documentos ===
{
  "respuesta": "El plan Estándar permite 300 solicitudes por minuto. Ante un error 429, tu servicio debe:\n1. Respetar el encabezado `Retry-After` sin reintentar antes de tiempo.\n2. Usar backoff exponencial con jitter si se reintenta varias veces.\n3. Cortar después de un máximo de 5 intentos.\n4. Reusar la misma `Idempotency-Key` en operaciones de escritura.",
  "referencias": ["03-limites-y-errores.md#0", "03-limites-y-errores.md#1"]
}
Verificación (respuesta con referencias): OK

=== Pregunta trampa (fuera de los documentos) ===
{
  "respuesta": "No lo sé.",
  "referencias": []
}
Verificación ("No lo sé." sin referencias): OK
```

En la segunda corrida la primera línea es `Ingesta: la base ya existía, no se
reindexó` y no hay llamada de embeddings para la ingesta.

## Correr los tests

```bash
uv run pytest -q          # 21 passed
```

Los tests no usan red ni keys:

- **Embeddings**: `DeterministicFakeEmbedding` de langchain-core (un vector fijo por
  texto), con un contador para detectar si se volvió a indexar.
- **LLM**: `FakeListChatModel` con respuestas JSON guionadas. El prompt, el
  `PydanticOutputParser` y `validar_referencias` son los reales.
- **Chroma**: una carpeta en `tmp_path` distinta para cada test.

Qué cubren: que los fragmentos midan hasta 500 tokens con solapamiento de hasta 50,
que se carguen solo `.md`/`.txt`, que la segunda ingesta no reindexe (el contador de
embeddings no se mueve) y `--forzar` no duplique, que consultar con otro modelo de
embeddings falle, que `get_rag_response` devuelva un `RespuestaRAG` con referencias
válidas (y descarte las inventadas), el camino `"No lo sé."`, salidas fuera de esquema,
`top_k` fuera de rango y el contenido del prompt.

tiktoken descarga el archivo de la codificación `cl100k_base` la primera vez que se
usa y después lo lee del caché local. Si corrés los tests en una máquina sin ese
caché y sin red, los de chunking fallan; con correr una vez `ingest.py` (o cualquier
test) con conexión alcanza.

## Decisiones

- **`langchain-chroma` en lugar de chromadb directo** para el vectorstore, así el
  retriever es un `Runnable` que entra en la cadena LCEL. Para *verificar* si la base
  existe se usa `chromadb.PersistentClient` directo, que permite listar colecciones sin
  crearlas.
- **IDs de fragmento estables (`archivo.md#n`)**: se usan como id en Chroma (reindexar
  reemplaza en lugar de duplicar) y como referencia que cita el modelo.
- **`top_k = 4`**: el dataset tiene 20 fragmentos; con 4 entran las secciones vecinas
  que suelen hacer falta (en la prueba, la tabla de planes y la sección sobre el 429
  estaban en fragmentos consecutivos) sin llenar el prompt de ruido.
- **`temperature=0`**: en un RAG se busca fidelidad al contexto, no creatividad.
- **Dataset ficticio**: el manual de "Colibrí" está inventado a propósito. Si el
  sistema responde bien sobre algo que no existe fuera de `data/`, la respuesta salió
  del contexto y no del conocimiento previo del modelo.
