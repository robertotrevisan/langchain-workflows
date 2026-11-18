import itertools
import json
from pathlib import Path

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from agente import Agente
from ambiente import AmbienteSimulado

RAIZ = Path(__file__).resolve().parent.parent
ENTRADAS = RAIZ / "entradas"
SERVICO = "checkout-api"

_ids = itertools.count(1)


def ler_entrada(nome: str) -> dict:
    return json.loads((ENTRADAS / nome).read_text(encoding="utf-8"))


def chamar(ferramenta: str, **args) -> AIMessage:
    """Resposta do modelo que chama uma ferramenta."""
    return AIMessage(
        content="",
        tool_calls=[{"name": ferramenta, "args": args, "id": f"c{next(_ids)}"}],
    )


def agir(ferramenta: str, servico: str = SERVICO, **args) -> AIMessage:
    return chamar(
        ferramenta, servico=servico, motivo="hipotese de teste com evidencia", **args
    )


def consultar(ferramenta: str = "consultar_metricas", servico: str = SERVICO):
    return chamar(ferramenta, servico=servico)


def concluir(status: str, resumo: str = "resumo de teste") -> AIMessage:
    return chamar("Desfecho", status=status, resumo=resumo)


class ModeloRoteirizado(BaseChatModel):
    """Substitui o chat model: devolve as respostas do roteiro em ordem, sem
    chamar nenhuma API. Uma Exception no roteiro e lancada naquela chamada."""

    roteiro: list = []
    chamadas: int = 0

    @property
    def _llm_type(self) -> str:
        return "roteirizado"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if self.chamadas >= len(self.roteiro):
            raise AssertionError(f"roteiro esgotado na chamada {self.chamadas + 1}")
        item = self.roteiro[self.chamadas]
        self.chamadas += 1
        if isinstance(item, Exception):
            raise item
        return ChatResult(generations=[ChatGeneration(message=item)])


@pytest.fixture
def ambiente() -> AmbienteSimulado:
    return AmbienteSimulado(RAIZ / "cenarios")


def criar_agente(ambiente: AmbienteSimulado, roteiro: list, **opcoes) -> Agente:
    opcoes.setdefault("acoes_com_aprovacao", ())
    return Agente(ModeloRoteirizado(roteiro=roteiro), ambiente, **opcoes)
