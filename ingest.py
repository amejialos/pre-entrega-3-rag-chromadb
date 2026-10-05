"""Ingesta: lee los .txt/.md de data/, los fragmenta por tokens y los guarda en ChromaDB.

Uso:
    uv run python ingest.py            # indexa solo si la colección no existe
    uv run python ingest.py --forzar   # borra la colección y reindexa

Antes de indexar verifica si la colección ya existe y tiene fragmentos: reindexar
cada vez duplicaría trabajo (y costo de embeddings) sin cambiar nada.
"""

import argparse
import logging
from pathlib import Path

import chromadb
from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    COLLECTION_NAME,
    DATA_DIR,
    ENCODING_NAME,
    VECTORSTORE_DIR,
    build_embeddings,
)

logger = logging.getLogger("rag.ingesta")

EXTENSIONES = (".md", ".txt")
# Clave de metadata de la colección donde se anota con qué modelo se indexó.
CLAVE_MODELO = "embedding_model"

# Separadores de mayor a menor: se corta entre párrafos y, solo si un párrafo no
# entra en un fragmento, entre líneas, oraciones y, como último recurso, palabras.
# Cortar por títulos ("\n## ") daba fragmentos de 50 tokens (título + aviso) y
# ningún solapamiento; por párrafos los fragmentos quedan cerca de 500 tokens.
# El solapamiento se arma con párrafos enteros: es de hasta 50 tokens y es 0
# cuando el último párrafo del fragmento anterior ya supera los 50.
SEPARADORES = ["\n\n", "\n", ". ", " ", ""]


def nombre_modelo(embeddings: Embeddings) -> str:
    """Identifica el modelo de embeddings (los fakes de los tests no tienen `model`)."""
    return getattr(embeddings, "model", None) or type(embeddings).__name__


def cargar_documentos(data_dir: Path = DATA_DIR) -> list[Document]:
    """Un Document por archivo .md/.txt de la carpeta, con el nombre como `fuente`."""
    data_dir = Path(data_dir)
    if not data_dir.is_dir():
        raise FileNotFoundError(f"No existe la carpeta de datos: {data_dir}")
    archivos = sorted(p for p in data_dir.iterdir() if p.suffix.lower() in EXTENSIONES)
    if not archivos:
        raise FileNotFoundError(f"No hay archivos .md ni .txt en {data_dir}")
    return [
        Document(page_content=archivo.read_text(encoding="utf-8"), metadata={"fuente": archivo.name})
        for archivo in archivos
    ]


def crear_splitter(
    chunk_size: int = CHUNK_SIZE, chunk_overlap: int = CHUNK_OVERLAP
) -> RecursiveCharacterTextSplitter:
    """Splitter que mide el largo en tokens de tiktoken, no en caracteres."""
    return RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        encoding_name=ENCODING_NAME,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=SEPARADORES,
    )


def fragmentar(documentos: list[Document], splitter: RecursiveCharacterTextSplitter | None = None) -> list[Document]:
    """Fragmenta cada documento y numera los fragmentos: el id es `archivo#n`."""
    splitter = splitter or crear_splitter()
    fragmentos: list[Document] = []
    for documento in documentos:
        for numero, texto in enumerate(splitter.split_text(documento.page_content)):
            fuente = documento.metadata["fuente"]
            fragmentos.append(
                Document(
                    page_content=texto,
                    metadata={"fuente": fuente, "fragmento": numero, "id": f"{fuente}#{numero}"},
                )
            )
    return fragmentos


def base_existente(persist_dir: Path = VECTORSTORE_DIR, collection_name: str = COLLECTION_NAME) -> bool:
    """True si la colección ya existe en disco y tiene al menos un fragmento."""
    persist_dir = Path(persist_dir)
    if not persist_dir.is_dir():
        return False  # no crea la carpeta solo por preguntar
    client = chromadb.PersistentClient(path=str(persist_dir))
    if collection_name not in {c.name for c in client.list_collections()}:
        return False
    return client.get_collection(collection_name).count() > 0


def _vectorstore(embeddings: Embeddings, persist_dir: Path, collection_name: str) -> Chroma:
    return Chroma(
        collection_name=collection_name,
        embedding_function=embeddings,
        persist_directory=str(persist_dir),
        collection_metadata={CLAVE_MODELO: nombre_modelo(embeddings)},
    )


def ingestar(
    embeddings: Embeddings,
    data_dir: Path = DATA_DIR,
    persist_dir: Path = VECTORSTORE_DIR,
    collection_name: str = COLLECTION_NAME,
    forzar: bool = False,
) -> tuple[Chroma, int]:
    """Indexa data_dir en ChromaDB. Devuelve el vectorstore y cuántos fragmentos indexó (0 = reutilizó)."""
    if base_existente(persist_dir, collection_name) and not forzar:
        logger.info("La colección %r ya existe en %s: no se reindexa.", collection_name, persist_dir)
        return abrir_vectorstore(embeddings, persist_dir, collection_name), 0

    if forzar and base_existente(persist_dir, collection_name):
        logger.info("--forzar: se borra la colección %r.", collection_name)
        chromadb.PersistentClient(path=str(persist_dir)).delete_collection(collection_name)

    fragmentos = fragmentar(cargar_documentos(data_dir))
    vectorstore = _vectorstore(embeddings, persist_dir, collection_name)
    vectorstore.add_documents(fragmentos, ids=[f.metadata["id"] for f in fragmentos])
    logger.info(
        "Indexados %d fragmentos de %d archivos con %s.",
        len(fragmentos),
        len({f.metadata["fuente"] for f in fragmentos}),
        nombre_modelo(embeddings),
    )
    return vectorstore, len(fragmentos)


def abrir_vectorstore(
    embeddings: Embeddings,
    persist_dir: Path = VECTORSTORE_DIR,
    collection_name: str = COLLECTION_NAME,
) -> Chroma:
    """Abre una colección existente y verifica que se consulte con el mismo modelo con que se indexó."""
    if not base_existente(persist_dir, collection_name):
        raise RuntimeError(f"No hay una base indexada en {persist_dir}. Corré primero: uv run python ingest.py")
    vectorstore = _vectorstore(embeddings, persist_dir, collection_name)
    indexado_con = (vectorstore._collection.metadata or {}).get(CLAVE_MODELO)
    actual = nombre_modelo(embeddings)
    if indexado_con and indexado_con != actual:
        # Vectores de modelos distintos viven en espacios distintos: la búsqueda
        # devolvería basura sin dar error. Mejor fallar y avisar.
        raise RuntimeError(
            f"La colección se indexó con {indexado_con!r} pero estás consultando con {actual!r}. "
            "Usá el mismo modelo o reindexá con: uv run python ingest.py --forzar"
        )
    return vectorstore


def main() -> None:
    parser = argparse.ArgumentParser(description="Indexa los documentos de data/ en ChromaDB.")
    parser.add_argument("--forzar", action="store_true", help="borra la colección y reindexa")
    args = parser.parse_args()
    _, indexados = ingestar(build_embeddings(), forzar=args.forzar)
    print(f"Fragmentos indexados en esta corrida: {indexados}")


if __name__ == "__main__":
    load_dotenv()  # carga .env al entorno; el código nunca tiene keys escritas
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    main()
