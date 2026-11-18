import json
from pathlib import Path

import pytest
from langchain_core.runnables import RunnableLambda

from contratos import AnaliseModelo, Consolidacao
from fontes import FonteCenarios

RAIZ = Path(__file__).resolve().parent.parent
ENTRADAS = RAIZ / "entradas"


def ler_entrada(nome: str) -> dict:
    return json.loads((ENTRADAS / nome).read_text(encoding="utf-8"))


class ModeloFalso:
    """Substitui o chat model: devolve respostas fixas por schema e guarda os
    prompts recebidos, sem chamar nenhuma API."""

    def __init__(
        self, falhar_consolidacao: bool = False, falhar_analises: bool = False
    ):
        self.prompts: dict[str, list[str]] = {"AnaliseModelo": [], "Consolidacao": []}
        self.falhar_consolidacao = falhar_consolidacao
        self.falhar_analises = falhar_analises

    def with_structured_output(self, schema, **_):
        def responder(prompt):
            self.prompts[schema.__name__].append(prompt.to_string())
            if schema is AnaliseModelo:
                if self.falhar_analises:
                    raise RuntimeError("401 chave invalida")
                return AnaliseModelo(
                    evidencias=["evidencia de teste"],
                    hipotese="hipotese de teste",
                    severidade="alta",
                )
            if self.falhar_consolidacao:
                raise RuntimeError("modelo indisponivel")
            return Consolidacao(
                resumo="resumo de teste",
                convergencias=["c"],
                divergencias=["d"],
                proximos_passos=["p"],
            )

        return RunnableLambda(responder)


@pytest.fixture
def modelo() -> ModeloFalso:
    return ModeloFalso()


@pytest.fixture
def fontes() -> FonteCenarios:
    return FonteCenarios(RAIZ / "cenarios")
