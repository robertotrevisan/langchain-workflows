"""Ambiente simulado com estado: consultas e acoes sobre os servicos.

Cada servico tem um cenario em `cenarios/{servico}.json` com o estado inicial,
os dados degradados e saudaveis e o efeito de cada acao. As acoes mudam o
estado, e as consultas seguintes refletem a mudanca. Trocar por um ambiente real
significa escrever outra classe com os mesmos metodos (Prometheus, Kubernetes,
pipeline de deploy).

Efeitos possiveis de uma acao:
    resolve     o servico fica saudavel
    temporario  saudavel nas proximas `consultas_ate_recair` consultas de
                metricas, depois volta a degradar
    nenhum      nada muda
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import TypeAdapter

from contratos import Deploy, LinhaLog, Metrica

NOME_SERVICO = re.compile(r"^[a-zA-Z0-9_-]{1,100}$")
MAX_REPLICAS = 20

Saude = Literal["degradado", "saudavel"]


class FonteIndisponivel(Exception):
    """A fonte nao respondeu ou nao tem dados para o servico."""


class AcaoInvalida(Exception):
    """A acao nao pode ser executada no estado atual do servico."""


@dataclass
class ServicoSimulado:
    cenario: dict
    versao: str
    versao_anterior: str | None
    replicas: int
    deploys: list[dict]
    saude: Saude = "degradado"
    consultas_ate_recair: int | None = None
    acoes: list[str] = field(default_factory=list)


class AmbienteSimulado:
    """Estado em memoria, por processo. A janela e aceita para manter a
    assinatura de uma fonte real, mas o cenario ja representa o periodo."""

    def __init__(self, diretorio: Path):
        self.diretorio = diretorio
        self._servicos: dict[str, ServicoSimulado] = {}

    def _servico(self, servico: str) -> ServicoSimulado:
        if servico in self._servicos:
            return self._servicos[servico]
        if not NOME_SERVICO.match(servico):
            raise FonteIndisponivel(f"nome de servico invalido: {servico!r}")
        arquivo = self.diretorio / f"{servico}.json"
        if not arquivo.exists():
            raise FonteIndisponivel(f"nenhum cenario para o servico {servico}")
        cenario = json.loads(arquivo.read_text(encoding="utf-8"))
        inicial = cenario["estado_inicial"]
        estado = ServicoSimulado(
            cenario=cenario,
            versao=inicial["versao"],
            versao_anterior=inicial.get("versao_anterior"),
            replicas=inicial["replicas"],
            deploys=[dict(d) for d in cenario.get("deploys", [])],
        )
        self._servicos[servico] = estado
        return estado

    def _exigir_fonte(self, estado: ServicoSimulado, fonte: str) -> None:
        if fonte in estado.cenario.get("fontes_indisponiveis", []):
            raise FonteIndisponivel(f"fonte {fonte} sem resposta (simulado)")

    # 1. Consultas (leitura, sem efeito no servico)

    def consultar_metricas(self, servico: str, janela: str) -> list[Metrica]:
        estado = self._servico(servico)
        self._exigir_fonte(estado, "metricas")
        if estado.consultas_ate_recair is not None:
            # Melhora temporaria: conta as consultas ate o servico recair.
            if estado.consultas_ate_recair > 0:
                estado.consultas_ate_recair -= 1
            else:
                estado.saude = "degradado"
                estado.consultas_ate_recair = None
        dados = estado.cenario[estado.saude]["metricas"]
        return TypeAdapter(list[Metrica]).validate_python(dados)

    def consultar_logs(self, servico: str, janela: str) -> list[LinhaLog]:
        estado = self._servico(servico)
        self._exigir_fonte(estado, "logs")
        dados = estado.cenario[estado.saude]["logs"]
        return TypeAdapter(list[LinhaLog]).validate_python(dados)

    def consultar_deploys(self, servico: str, janela: str) -> list[Deploy]:
        estado = self._servico(servico)
        self._exigir_fonte(estado, "deploys")
        return TypeAdapter(list[Deploy]).validate_python(estado.deploys)

    def consultar_estado(self, servico: str) -> dict:
        """Configuracao atual, sem a saude: o agente descobre a saude pelas
        metricas, como faria num ambiente real."""
        estado = self._servico(servico)
        return {
            "versao": estado.versao,
            "versao_anterior": estado.versao_anterior,
            "replicas": estado.replicas,
            "acoes_executadas": list(estado.acoes),
        }

    # 2. Acoes (mudam o estado do servico)

    def _aplicar_efeito(self, estado: ServicoSimulado, acao: str) -> str:
        efeito = estado.cenario["efeitos"].get(acao, {"efeito": "nenhum"})
        estado.acoes.append(acao)
        if efeito["efeito"] == "resolve":
            estado.saude = "saudavel"
            estado.consultas_ate_recair = None
        elif efeito["efeito"] == "temporario":
            estado.saude = "saudavel"
            estado.consultas_ate_recair = efeito.get("consultas_ate_recair", 1)
        # O efeito real nao e revelado: o agente precisa verificar.
        return "executado"

    def reiniciar_servico(self, servico: str) -> str:
        estado = self._servico(servico)
        self._aplicar_efeito(estado, "reiniciar_servico")
        return f"{servico} reiniciado ({estado.replicas} replicas)"

    def escalar_replicas(self, servico: str, replicas: int) -> str:
        estado = self._servico(servico)
        if not 1 <= replicas <= MAX_REPLICAS:
            raise AcaoInvalida(f"replicas deve estar entre 1 e {MAX_REPLICAS}")
        if replicas == estado.replicas:
            raise AcaoInvalida(f"{servico} ja tem {replicas} replicas")
        anterior, estado.replicas = estado.replicas, replicas
        self._aplicar_efeito(estado, "escalar_replicas")
        return f"{servico} escalado de {anterior} para {replicas} replicas"

    def reverter_deploy(self, servico: str) -> str:
        estado = self._servico(servico)
        if estado.versao_anterior is None:
            raise AcaoInvalida(f"{servico} nao tem versao anterior para reverter")
        atual, alvo = estado.versao, estado.versao_anterior
        for deploy in estado.deploys:
            if deploy["versao"] == atual:
                deploy["status"] = "revertido"
        estado.deploys.append(
            {"versao": alvo, "horario": "agora", "autor": "agente", "status": "sucesso"}
        )
        estado.versao, estado.versao_anterior = alvo, None
        self._aplicar_efeito(estado, "reverter_deploy")
        return f"{servico} revertido de {atual} para {alvo}"
