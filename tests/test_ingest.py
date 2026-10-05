from pathlib import Path

import pytest
import tiktoken
from langchain_core.documents import Document
from langchain_core.embeddings import DeterministicFakeEmbedding

from config import CHUNK_OVERLAP, CHUNK_SIZE, ENCODING_NAME
from ingest import abrir_vectorstore, base_existente, cargar_documentos, fragmentar, ingestar

ENC = tiktoken.get_encoding(ENCODING_NAME)
# El splitter suma los tokens de cada pedazo por separado; al unirlos, BPE puede
# tokenizar la juntura distinto y el fragmento final medir uno o dos tokens más.
TOLERANCIA = 5


def tokens(texto: str) -> int:
    return len(ENC.encode(texto))


def solapamiento(anterior: str, siguiente: str) -> str:
    """El texto más largo que es a la vez final de `anterior` y comienzo de `siguiente`."""
    for largo in range(min(len(anterior), len(siguiente)), 0, -1):
        if anterior.endswith(siguiente[:largo]):
            return siguiente[:largo]
    return ""


def test_configuracion_de_chunking_pedida_por_la_consigna() -> None:
    assert CHUNK_SIZE >= 500
    assert CHUNK_OVERLAP == 50


def test_carga_solo_md_y_txt(data_dir: Path) -> None:
    fuentes = [d.metadata["fuente"] for d in cargar_documentos(data_dir)]
    assert fuentes == ["depositos.txt", "envios.md"]


def test_carpeta_sin_documentos_da_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        cargar_documentos(tmp_path)


def test_fragmentos_miden_hasta_500_tokens_y_se_solapan_hasta_50() -> None:
    # Un solo párrafo largo: el splitter corta por palabras y el solapamiento se ve claro.
    texto = " ".join(f"palabra{i}" for i in range(3000))
    fragmentos = fragmentar([Document(page_content=texto, metadata={"fuente": "largo.md"})])

    assert len(fragmentos) > 3
    assert all(tokens(f.page_content) <= CHUNK_SIZE + TOLERANCIA for f in fragmentos)
    # Todos menos el último quedan cerca del máximo: no hay fragmentos diminutos.
    assert all(tokens(f.page_content) > CHUNK_SIZE * 0.9 for f in fragmentos[:-1])
    for anterior, siguiente in zip(fragmentos, fragmentos[1:]):
        comun = solapamiento(anterior.page_content, siguiente.page_content)
        assert 0 < tokens(comun) <= CHUNK_OVERLAP + TOLERANCIA


def test_fragmentos_tienen_fuente_e_id_unico(data_dir: Path) -> None:
    fragmentos = fragmentar(cargar_documentos(data_dir))
    ids = [f.metadata["id"] for f in fragmentos]
    assert len(ids) == len(set(ids))
    assert "envios.md#0" in ids and "depositos.txt#1" in ids
    assert all(f.metadata["id"] == f"{f.metadata['fuente']}#{f.metadata['fragmento']}" for f in fragmentos)


def test_base_existente_no_crea_la_carpeta(persist_dir: Path) -> None:
    assert base_existente(persist_dir, "prueba") is False
    assert not persist_dir.exists()


def test_primera_ingesta_indexa_todo(embeddings, data_dir: Path, persist_dir: Path) -> None:
    vs, indexados = ingestar(embeddings, data_dir=data_dir, persist_dir=persist_dir, collection_name="prueba")
    esperados = len(fragmentar(cargar_documentos(data_dir)))
    assert indexados == esperados > 0
    assert vs._collection.count() == esperados
    assert base_existente(persist_dir, "prueba")


def test_no_reindexa_si_la_base_ya_existe(embeddings, data_dir: Path, persist_dir: Path) -> None:
    ingestar(embeddings, data_dir=data_dir, persist_dir=persist_dir, collection_name="prueba")
    embebidos = embeddings.textos_indexados

    vs, indexados = ingestar(embeddings, data_dir=data_dir, persist_dir=persist_dir, collection_name="prueba")

    assert indexados == 0
    assert embeddings.textos_indexados == embebidos  # no se volvió a llamar al modelo
    assert vs._collection.count() == embebidos


def test_forzar_reindexa_sin_duplicar(embeddings, data_dir: Path, persist_dir: Path) -> None:
    _, primera = ingestar(embeddings, data_dir=data_dir, persist_dir=persist_dir, collection_name="prueba")
    vs, segunda = ingestar(
        embeddings, data_dir=data_dir, persist_dir=persist_dir, collection_name="prueba", forzar=True
    )
    assert segunda == primera
    assert vs._collection.count() == primera


def test_abrir_sin_base_da_error_claro(embeddings, persist_dir: Path) -> None:
    with pytest.raises(RuntimeError, match="ingest.py"):
        abrir_vectorstore(embeddings, persist_dir, "prueba")


def test_consultar_con_otro_modelo_de_embeddings_da_error(vectorstore, persist_dir: Path) -> None:
    class OtroModelo(DeterministicFakeEmbedding):
        model: str = "otro-modelo"

    with pytest.raises(RuntimeError, match="mismo modelo"):
        abrir_vectorstore(OtroModelo(size=64), persist_dir, "prueba")
