import json
from pathlib import Path

import pytest
from langchain_core.runnables import RunnableLambda

from contratos import AnaliseModelo, Consolidacao, EscolhaRunbook, Roteamento
from fontes import FonteCenarios
from runbooks import carregar_catalogo
from triagem import Triagem
from workflow import Investigacao

RAIZ = Path(__file__).resolve().parent.parent
ENTRADAS = RAIZ / "entradas"


def ler_entrada(nome: str) -> dict:
    return json.loads((ENTRADAS / nome).read_text(encoding="utf-8"))


class ModeloFalso:
    """Substitui o chat model: devolve respostas fixas por schema e guarda os
    prompts recebidos, sem chamar nenhuma API."""

    def __init__(
        self,
        falhar_consolidacao: bool = False,
        falhar_analises: bool = False,
        destino: str = "investigar",
        confianca: float = 0.9,
        runbook_id: str | None = "rb-pool-conexoes",
        falhar_recomendacao: bool = False,
    ):
        self.prompts: dict[str, list[str]] = {
            "AnaliseModelo": [],
            "Consolidacao": [],
            "Roteamento": [],
            "EscolhaRunbook": [],
        }
        self.falhar_consolidacao = falhar_consolidacao
        self.falhar_analises = falhar_analises
        self.roteamento = Roteamento(
            destino=destino, confianca=confianca, justificativa="justificativa de teste"
        )
        self.runbook_id = runbook_id
        self.falhar_recomendacao = falhar_recomendacao

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
            if schema is Roteamento:
                return self.roteamento
            if schema is EscolhaRunbook:
                if self.falhar_recomendacao:
                    raise RuntimeError("modelo indisponivel")
                return EscolhaRunbook(
                    runbook_id=self.runbook_id,
                    justificativa="runbook de teste",
                    confianca=0.8,
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


def criar_triagem(modelo: ModeloFalso, fontes: FonteCenarios, limiar: float = 0.7):
    return Triagem(
        modelo,
        modelo,
        Investigacao(modelo, fontes),
        carregar_catalogo(RAIZ / "runbooks" / "catalogo.json"),
        limiar,
    )
