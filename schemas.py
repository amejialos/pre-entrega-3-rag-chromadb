"""Contrato de salida del RAG: el texto de la respuesta y los fragmentos que la respaldan."""

from pydantic import BaseModel, Field

NO_LO_SE = "No lo sé."


class RespuestaRAG(BaseModel):
    respuesta: str = Field(
        description=(
            "Respuesta en español basada únicamente en el contexto. "
            f"Si el contexto no alcanza para responder, exactamente: {NO_LO_SE!r}"
        )
    )
    referencias: list[str] = Field(
        default_factory=list,
        description=(
            "IDs de los fragmentos del contexto que respaldan la respuesta, tal como aparecen "
            "entre corchetes (por ejemplo 'archivo.md#2'). Lista vacía si la respuesta es 'No lo sé.'"
        ),
    )

    @property
    def sin_respuesta(self) -> bool:
        """True si el modelo dijo que no sabe (el contexto no tenía la información)."""
        return self.respuesta.strip().lower().startswith("no lo sé")
