"""Fontes de dados da investigacao.

`Fontes` e a interface que o workflow conhece. `FonteCenarios` e a implementacao
ficticia: le cenarios em JSON (um arquivo por servico). Trocar por Prometheus,
CloudWatch Logs ou CI significa escrever outra classe com os mesmos metodos.
"""

import json
import re
from pathlib import Path
from typing import Protocol

from pydantic import TypeAdapter

from contratos import Deploy, LinhaLog, Metrica

NOME_SERVICO = re.compile(r"^[a-zA-Z0-9_-]{1,100}$")


class FonteIndisponivel(Exception):
    """A fonte nao respondeu ou nao tem dados para o servico."""


class Fontes(Protocol):
    def consultar_metricas(self, servico: str, janela: str) -> list[Metrica]: ...

    def consultar_logs(self, servico: str, janela: str) -> list[LinhaLog]: ...

    def consultar_deploys(self, servico: str, janela: str) -> list[Deploy]: ...


class FonteCenarios:
    """Fonte ficticia. A janela e aceita para manter a assinatura real, mas o
    cenario ja representa o periodo relevante."""

    def __init__(self, diretorio: Path):
        self.diretorio = diretorio

    def _carregar(self, servico: str, fonte: str) -> list:
        if not NOME_SERVICO.match(servico):
            raise FonteIndisponivel(f"nome de servico invalido: {servico!r}")
        arquivo = self.diretorio / f"{servico}.json"
        if not arquivo.exists():
            raise FonteIndisponivel(f"nenhum cenario para o servico {servico}")
        cenario = json.loads(arquivo.read_text(encoding="utf-8"))
        if fonte in cenario.get("fontes_indisponiveis", []):
            raise FonteIndisponivel(f"fonte {fonte} sem resposta (simulado)")
        return cenario.get(fonte, [])

    def consultar_metricas(self, servico: str, janela: str) -> list[Metrica]:
        dados = self._carregar(servico, "metricas")
        return TypeAdapter(list[Metrica]).validate_python(dados)

    def consultar_logs(self, servico: str, janela: str) -> list[LinhaLog]:
        dados = self._carregar(servico, "logs")
        return TypeAdapter(list[LinhaLog]).validate_python(dados)

    def consultar_deploys(self, servico: str, janela: str) -> list[Deploy]:
        dados = self._carregar(servico, "deploys")
        return TypeAdapter(list[Deploy]).validate_python(dados)
