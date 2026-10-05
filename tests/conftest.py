"""Fixtures compartidas: embeddings falsos, un dataset chico y una base Chroma en tmp_path.

Nada de esto usa red ni keys: los embeddings son `DeterministicFakeEmbedding` de
langchain-core (un vector pseudoaleatorio fijo por texto) y Chroma persiste en
una carpeta temporal distinta para cada test.
"""

from pathlib import Path

import pytest
from langchain_chroma import Chroma
from langchain_core.embeddings import DeterministicFakeEmbedding

from ingest import ingestar


class EmbeddingsContados(DeterministicFakeEmbedding):
    """Fake que cuenta cuántos textos se embebieron al indexar (para detectar reindexados)."""

    textos_indexados: int = 0

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.textos_indexados += len(texts)
        return super().embed_documents(texts)


PARRAFO = (
    "El servicio Garza procesa pedidos de envío y asigna cada paquete a un depósito según el "
    "código postal. Los pedidos urgentes salen el mismo día si entran antes de las 14 horas. "
)


@pytest.fixture
def embeddings() -> EmbeddingsContados:
    return EmbeddingsContados(size=64)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    carpeta = tmp_path / "data"
    carpeta.mkdir()
    # Dos archivos de unos 700 tokens: cada uno da dos fragmentos.
    (carpeta / "envios.md").write_text("# Envíos\n\n" + "\n\n".join([PARRAFO] * 18), encoding="utf-8")
    (carpeta / "depositos.txt").write_text("\n\n".join([PARRAFO.replace("Garza", "Garza Norte")] * 18), encoding="utf-8")
    (carpeta / "ignorar.pdf").write_bytes(b"%PDF-1.4 no es texto")
    return carpeta


@pytest.fixture
def persist_dir(tmp_path: Path) -> Path:
    return tmp_path / "vectorstore"


@pytest.fixture
def vectorstore(embeddings: EmbeddingsContados, data_dir: Path, persist_dir: Path) -> Chroma:
    vs, _ = ingestar(embeddings, data_dir=data_dir, persist_dir=persist_dir, collection_name="prueba")
    return vs
