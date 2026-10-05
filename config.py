"""Configuración del sistema RAG: constantes y construcción de modelos.

Es el único módulo que lee variables de entorno. La ingesta y la cadena reciben
el modelo de embeddings y el LLM por parámetro (inyección de dependencias), así
los tests usan fakes sin red y sin keys.

Funciona igual con una key real de OpenAI o con una key de Gemini a través de su
endpoint compatible con OpenAI (`OPENAI_BASE_URL`).
"""

import os
from pathlib import Path

from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
VECTORSTORE_DIR = BASE_DIR / "vectorstore"
COLLECTION_NAME = "manual_colibri"

# Chunking medido en tokens (tiktoken), no en caracteres.
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
ENCODING_NAME = "cl100k_base"

# Cantidad de fragmentos que recupera el retriever. La consigna pide entre 3 y 5:
# menos deja afuera contexto útil, más mete ruido y gasta tokens.
TOP_K = 4

DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_OPENAI_EMBEDDING = "text-embedding-3-small"
DEFAULT_GEMINI_EMBEDDING = "gemini-embedding-001"


def _require_env(var: str) -> str:
    value = os.environ.get(var, "").strip()
    if not value:
        raise ValueError(f"Falta la variable de entorno {var}. Copiá .env.example a .env y completala.")
    return value


def _base_url() -> str | None:
    return os.environ.get("OPENAI_BASE_URL", "").strip() or None


def embedding_model_name() -> str:
    """Modelo de embeddings: EMBEDDING_MODEL, o un default según el endpoint."""
    explicit = os.environ.get("EMBEDDING_MODEL", "").strip()
    if explicit:
        return explicit
    return DEFAULT_GEMINI_EMBEDDING if _base_url() else DEFAULT_OPENAI_EMBEDDING


def build_embeddings() -> Embeddings:
    """El mismo modelo se usa para indexar y para consultar (ver ingest.verificar_embeddings)."""
    return OpenAIEmbeddings(
        model=embedding_model_name(),
        api_key=_require_env("OPENAI_API_KEY"),
        base_url=_base_url(),
        # Por defecto OpenAIEmbeddings tokeniza el texto con tiktoken y manda los
        # tokens. El endpoint de Gemini solo acepta texto: con False manda el texto.
        # Con OpenAI también funciona (los fragmentos son mucho más cortos que el límite).
        check_embedding_ctx_length=False,
        chunk_size=100,  # textos por request; Gemini acepta hasta 100 por lote
    )


def build_llm() -> BaseChatModel:
    return ChatOpenAI(
        model=os.environ.get("OPENAI_MODEL", "").strip() or DEFAULT_OPENAI_MODEL,
        api_key=_require_env("OPENAI_API_KEY"),
        base_url=_base_url(),
        temperature=0,  # respuestas fieles al contexto, lo más deterministas posible
        # El SDK reintenta 429/5xx con backoff exponencial. El default (2) se queda corto
        # con el tier gratuito de Gemini, que devuelve 503 seguidos en horas de demanda.
        max_retries=5,
    )
