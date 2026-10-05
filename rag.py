"""Cadena RAG asíncrona: retriever de ChromaDB + prompt "filtro de veracidad" + LLM + PydanticOutputParser.

La cadena (LCEL):

    pregunta
      -> RunnableParallel(documentos=retriever, pregunta=passthrough)   búsqueda de similitud (top_k)
      -> .assign(respuesta = formatear_documentos | PROMPT | llm | PydanticOutputParser)
      -> validar_referencias                                            descarta referencias inventadas
      -> RespuestaRAG

Se guarda la lista de documentos recuperados junto a la respuesta para poder
verificar, después del LLM, que cada referencia citada exista de verdad.
"""

import logging
from operator import itemgetter
from typing import Any

from langchain_core.documents import Document
from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.retrievers import BaseRetriever
from langchain_core.runnables import Runnable, RunnableLambda, RunnableParallel, RunnablePassthrough
from langchain_core.vectorstores import VectorStore

from config import TOP_K, build_embeddings, build_llm
from ingest import abrir_vectorstore
from schemas import NO_LO_SE, RespuestaRAG

logger = logging.getLogger("rag.cadena")

parser = PydanticOutputParser(pydantic_object=RespuestaRAG)

SYSTEM = f"""Sos un asistente que responde preguntas sobre documentación técnica interna.

Reglas (filtro de veracidad):
1. Respondé usando ÚNICAMENTE la información del contexto. No uses conocimiento propio ni supongas nada.
2. Si el contexto no contiene la respuesta, o la contiene solo en parte y no alcanza para responder,
   respondé exactamente "{NO_LO_SE}" y dejá las referencias vacías.
3. En "referencias" poné los IDs (lo que está entre corchetes) de los fragmentos que usaste. No inventes IDs.
4. Respondé en español, de forma breve y concreta.

{{format_instructions}}"""

HUMAN = """Contexto:
{contexto}

Pregunta: {pregunta}"""

PROMPT = ChatPromptTemplate.from_messages([("system", SYSTEM), ("human", HUMAN)]).partial(
    format_instructions=parser.get_format_instructions()
)


def formatear_documentos(documentos: list[Document]) -> str:
    """Cada fragmento va precedido de su ID entre corchetes, que el modelo usa para citar."""
    if not documentos:
        return "(no se encontraron fragmentos)"
    return "\n\n---\n\n".join(f"[{d.metadata.get('id', '?')}]\n{d.page_content}" for d in documentos)


def validar_referencias(salida: dict[str, Any]) -> RespuestaRAG:
    """Conserva solo las referencias a fragmentos realmente recuperados."""
    respuesta: RespuestaRAG = salida["respuesta"]
    if respuesta.sin_respuesta:
        return respuesta.model_copy(update={"referencias": []})
    recuperados = {d.metadata.get("id") for d in salida["documentos"]}
    validas = [r for r in dict.fromkeys(respuesta.referencias) if r in recuperados]  # sin duplicados
    descartadas = [r for r in respuesta.referencias if r not in recuperados]
    if descartadas:
        logger.warning("Referencias descartadas (no estaban en el contexto): %s", descartadas)
    return respuesta.model_copy(update={"referencias": validas})


def build_chain(retriever: BaseRetriever, llm: BaseChatModel) -> Runnable[str, RespuestaRAG]:
    generacion = (
        {
            "contexto": itemgetter("documentos") | RunnableLambda(formatear_documentos),
            "pregunta": itemgetter("pregunta"),
        }
        | PROMPT
        | llm
        | parser
    )
    return (
        RunnableParallel(documentos=retriever, pregunta=RunnablePassthrough())
        .assign(respuesta=generacion)
        | RunnableLambda(validar_referencias)
    )


class RAG:
    """Sistema RAG sobre un vectorstore ya poblado."""

    def __init__(self, vectorstore: VectorStore, llm: BaseChatModel, top_k: int = TOP_K) -> None:
        if not 3 <= top_k <= 5:
            raise ValueError(f"top_k debe estar entre 3 y 5 (recibido: {top_k})")
        retriever = vectorstore.as_retriever(search_type="similarity", search_kwargs={"k": top_k})
        self.chain = build_chain(retriever, llm)

    async def get_rag_response(self, query: str) -> RespuestaRAG:
        """Busca los fragmentos más parecidos, arma el prompt, llama al LLM y parsea la respuesta."""
        query = query.strip()
        if not query:
            raise ValueError("La consulta está vacía.")
        logger.info("Consulta: %s", query)
        respuesta = await self.chain.ainvoke(query)
        logger.info("Referencias: %s", respuesta.referencias or "(ninguna)")
        return respuesta


_rag_por_defecto: RAG | None = None


async def get_rag_response(query: str) -> RespuestaRAG:
    """Atajo con la firma de la consigna: usa la base de ./vectorstore y los modelos del .env."""
    global _rag_por_defecto
    if _rag_por_defecto is None:
        _rag_por_defecto = RAG(abrir_vectorstore(build_embeddings()), build_llm())
    return await _rag_por_defecto.get_rag_response(query)
