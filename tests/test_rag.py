import json

import pytest
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import FakeListChatModel

from rag import PROMPT, RAG, formatear_documentos
from schemas import NO_LO_SE, RespuestaRAG


def llm_que_responde(respuesta: str, referencias: list[str]) -> FakeListChatModel:
    return FakeListChatModel(responses=[json.dumps({"respuesta": respuesta, "referencias": referencias})])


def ids_en(vectorstore) -> set[str]:
    return {m["id"] for m in vectorstore._collection.get()["metadatas"]}


async def test_devuelve_modelo_pydantic_con_referencias(vectorstore) -> None:
    # El corpus de prueba tiene 4 fragmentos y top_k=5: el retriever trae todos.
    assert ids_en(vectorstore) == {"envios.md#0", "envios.md#1", "depositos.txt#0", "depositos.txt#1"}
    rag = RAG(vectorstore, llm_que_responde("Salen el mismo día si entran antes de las 14.", ["envios.md#0"]), top_k=5)

    resultado = await rag.get_rag_response("¿Cuándo salen los pedidos urgentes?")

    assert isinstance(resultado, RespuestaRAG)
    assert resultado.respuesta == "Salen el mismo día si entran antes de las 14."
    assert resultado.referencias == ["envios.md#0"]
    assert not resultado.sin_respuesta


async def test_descarta_referencias_inventadas_y_duplicadas(vectorstore) -> None:
    llm = llm_que_responde("Antes de las 14.", ["envios.md#1", "inventado.md#9", "envios.md#1"])
    resultado = await RAG(vectorstore, llm, top_k=5).get_rag_response("¿Hasta qué hora?")
    assert resultado.referencias == ["envios.md#1"]


async def test_camino_no_lo_se_deja_referencias_vacias(vectorstore) -> None:
    # Aunque el modelo cite algo, "No lo sé." no puede venir respaldado por fragmentos.
    llm = llm_que_responde(NO_LO_SE, ["envios.md#0"])
    resultado = await RAG(vectorstore, llm).get_rag_response("¿Cuál es la receta del locro?")
    assert resultado.sin_respuesta
    assert resultado.respuesta == NO_LO_SE
    assert resultado.referencias == []


async def test_parsea_json_dentro_de_bloque_de_codigo(vectorstore) -> None:
    # Muchos modelos envuelven el JSON en ```json ... ```; PydanticOutputParser lo tolera.
    contenido = '```json\n{"respuesta": "Antes de las 14.", "referencias": ["depositos.txt#0"]}\n```'
    resultado = await RAG(vectorstore, FakeListChatModel(responses=[contenido]), top_k=5).get_rag_response("¿Hora?")
    assert resultado.referencias == ["depositos.txt#0"]


async def test_salida_que_no_cumple_el_esquema_falla(vectorstore) -> None:
    llm = FakeListChatModel(responses=["Los pedidos salen a las 14, creo."])
    with pytest.raises(OutputParserException):
        await RAG(vectorstore, llm).get_rag_response("¿Hora?")


async def test_consulta_vacia_da_error(vectorstore) -> None:
    with pytest.raises(ValueError, match="vacía"):
        await RAG(vectorstore, llm_que_responde("x", [])).get_rag_response("   ")


@pytest.mark.parametrize("top_k", [2, 6])
def test_top_k_fuera_de_rango(vectorstore, top_k: int) -> None:
    with pytest.raises(ValueError, match="entre 3 y 5"):
        RAG(vectorstore, llm_que_responde("x", []), top_k=top_k)


async def test_retriever_trae_top_k_fragmentos(vectorstore) -> None:
    documentos = await vectorstore.as_retriever(search_kwargs={"k": 3}).ainvoke("pedidos urgentes")
    assert len(documentos) == 3


def test_prompt_incluye_filtro_de_veracidad_y_contexto(vectorstore) -> None:
    documentos = vectorstore.similarity_search("pedidos", k=3)
    mensajes = PROMPT.format_messages(contexto=formatear_documentos(documentos), pregunta="¿Algo?")
    sistema, humano = mensajes[0].content, mensajes[1].content
    assert NO_LO_SE in sistema and "ÚNICAMENTE" in sistema
    assert '"respuesta"' in sistema and '"referencias"' in sistema  # instrucciones de formato del parser
    assert f"[{documentos[0].metadata['id']}]" in humano
    assert "¿Algo?" in humano
