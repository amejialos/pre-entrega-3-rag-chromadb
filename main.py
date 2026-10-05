"""Prueba end-to-end: ingesta (si hace falta) y dos consultas, una real y una trampa.

La pregunta real tiene respuesta en data/ y debe volver con referencias. La trampa
no tiene nada que ver con los documentos: el sistema tiene que decir "No lo sé."
en lugar de responder con lo que el modelo sabe por su cuenta.

Uso:
    uv run python main.py
"""

import asyncio
import logging
import sys

from dotenv import load_dotenv

from config import build_embeddings
from ingest import ingestar
from rag import get_rag_response

PRUEBAS: list[tuple[str, str, bool]] = [
    # (título, pregunta, ¿debería tener respuesta en los documentos?)
    (
        "Pregunta con respuesta en los documentos",
        "¿Cuántas solicitudes por minuto permite el plan Estándar y qué tengo que hacer si recibo un error 429?",
        True,
    ),
    (
        "Pregunta trampa (fuera de los documentos)",
        "¿Cuál es la receta tradicional del locro argentino y cuánto tiempo hay que cocinarlo?",
        False,
    ),
]


async def main() -> int:
    try:
        _, indexados = ingestar(build_embeddings())
    except ValueError as error:
        print(f"error de configuración: {error}")
        return 1
    print(
        f"Ingesta: {indexados} fragmentos indexados" if indexados else "Ingesta: la base ya existía, no se reindexó",
        flush=True,
    )

    fallas = 0
    for titulo, pregunta, tiene_respuesta in PRUEBAS:
        print(f"\n=== {titulo} ===\nPregunta: {pregunta}", flush=True)
        try:
            resultado = await get_rag_response(pregunta)
        except Exception as error:  # cuota agotada, red, salida inválida: se informa sin traceback
            print(f"error controlado: {type(error).__name__}: {error}", flush=True)
            fallas += 1
            continue
        if tiene_respuesta:
            ok = not resultado.sin_respuesta and bool(resultado.referencias)
            esperado = "respuesta con referencias"
        else:
            ok = resultado.sin_respuesta and not resultado.referencias
            esperado = '"No lo sé." sin referencias'
        fallas += not ok
        print(resultado.model_dump_json(indent=2), flush=True)
        print(f"Verificación ({esperado}): {'OK' if ok else 'FALLA'}", flush=True)
    return 1 if fallas else 0


if __name__ == "__main__":
    load_dotenv()  # carga .env al entorno; el código nunca tiene keys escritas
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # el SDK loguea cada request en INFO
    sys.exit(asyncio.run(main()))
