from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

MAX_DESCRICAO = 4000

Origem = Literal["alertmanager", "cloudwatch", "generico"]
StatusAlerta = Literal["firing", "resolved"]
Severidade = Literal["info", "warning", "critical"]
Perspectiva = Literal["metricas", "logs", "deploys"]


# 1. Contrato canonico: toda origem e normalizada para Incidente


class Incidente(BaseModel):
    id: str = Field(description="Identificador gerado pela aplicacao")
    fingerprint: str = Field(description="Identidade do alerta na origem")
    origem: Origem
    status: StatusAlerta
    titulo: str = Field(min_length=1, max_length=200)
    descricao: str = Field(max_length=MAX_DESCRICAO)
    servico: str | None = Field(
        default=None,
        description="Servico afetado; sem servico nao ha fonte para consultar",
    )
    severidade: Severidade = "warning"
    labels: dict[str, str] = Field(default_factory=dict)
    inicio: datetime


class EventoGenerico(BaseModel):
    """Entrada do webhook generico, para origens sem adaptador e para testes."""

    fingerprint: str | None = Field(
        default=None,
        description="Quando ausente, e derivado de titulo e servico",
    )
    status: StatusAlerta = "firing"
    titulo: str = Field(min_length=1, max_length=200)
    descricao: str = Field(default="", max_length=MAX_DESCRICAO)
    servico: str | None = None
    severidade: Severidade = "warning"
    labels: dict[str, str] = Field(default_factory=dict)
    inicio: datetime | None = None


# 2. Payloads das origens (somente os campos usados)


class AlertaAlertmanager(BaseModel):
    status: StatusAlerta
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    startsAt: datetime
    fingerprint: str


class WebhookAlertmanager(BaseModel):
    version: str
    status: StatusAlerta
    alerts: list[AlertaAlertmanager] = Field(min_length=1)


class MensagemSns(BaseModel):
    Type: Literal["SubscriptionConfirmation", "Notification", "UnsubscribeConfirmation"]
    MessageId: str
    TopicArn: str
    Message: str
    SubscribeURL: str | None = None


class DimensaoCloudWatch(BaseModel):
    name: str
    value: str


class TriggerCloudWatch(BaseModel):
    MetricName: str | None = None
    Namespace: str | None = None
    Dimensions: list[DimensaoCloudWatch] = Field(default_factory=list)


class AlarmeCloudWatch(BaseModel):
    AlarmName: str
    AlarmDescription: str | None = None
    AlarmArn: str
    NewStateValue: Literal["ALARM", "OK", "INSUFFICIENT_DATA"]
    NewStateReason: str
    StateChangeTime: datetime
    Trigger: TriggerCloudWatch = Field(default_factory=TriggerCloudWatch)


# 3. Dados devolvidos pelas fontes


class PontoSerie(BaseModel):
    horario: str
    valor: float


class Metrica(BaseModel):
    nome: str
    unidade: str
    pontos: list[PontoSerie]


class LinhaLog(BaseModel):
    horario: str
    nivel: Literal["DEBUG", "INFO", "WARN", "ERROR"]
    mensagem: str


class Deploy(BaseModel):
    versao: str
    horario: str
    autor: str
    status: Literal["sucesso", "falha", "revertido"]


# 4. Resultado da investigacao


class AnaliseModelo(BaseModel):
    """O que o modelo produz em cada ramo. A perspectiva e definida pelo codigo."""

    evidencias: list[str] = Field(
        description="Fatos observados nos dados consultados, sem interpretacao",
    )
    hipotese: str = Field(
        description="Hipotese da perspectiva, marcada como inferencia",
    )
    severidade: Literal["baixa", "media", "alta", "critica"] = Field(
        description="Gravidade percebida a partir desta perspectiva",
    )


class Analise(BaseModel):
    perspectiva: Perspectiva
    status: Literal["ok", "falha"]
    evidencias: list[str] = Field(default_factory=list)
    hipotese: str | None = None
    severidade: Literal["baixa", "media", "alta", "critica"] | None = None
    origem_falha: Literal["fonte", "processamento"] | None = Field(
        default=None,
        description="fonte: sem dados; processamento: erro de modelo ou de codigo",
    )
    erro: str | None = None


class Consolidacao(BaseModel):
    resumo: str = Field(description="Sintese do incidente em poucas frases")
    convergencias: list[str] = Field(
        description="Pontos em que as perspectivas concordam",
    )
    divergencias: list[str] = Field(
        description="Pontos em que as perspectivas discordam ou faltam dados",
    )
    proximos_passos: list[str] = Field(
        description="Somente verificacoes de leitura, sem acao corretiva",
    )


class Diagnostico(BaseModel):
    status: Literal["completo", "parcial", "inconclusivo"]
    analises: list[Analise]
    consolidacao: Consolidacao | None = None


# 5. Triagem: rota escolhida e recomendacao de runbook

Rota = Literal["investigar", "ruido", "humano"]


class Roteamento(BaseModel):
    """O que o roteador produz. A rota final pode virar "humano" se a confianca
    ficar abaixo do limiar configurado."""

    destino: Rota = Field(
        description=(
            "investigar: falha real com servico identificado; ruido: alerta "
            "informativo, de teste ou instavel; humano: ambiguo, varios servicos "
            "ou sem informacao suficiente"
        ),
    )
    confianca: float = Field(ge=0, le=1, description="Confianca na escolha, de 0 a 1")
    justificativa: str = Field(description="Por que esta rota, em uma ou duas frases")


class Runbook(BaseModel):
    id: str
    titulo: str
    quando_usar: str
    passos: list[str]
    risco: Literal["baixo", "medio", "alto"]


class EscolhaRunbook(BaseModel):
    """O que o modelo produz na recomendacao."""

    runbook_id: str | None = Field(
        description="id de um runbook do catalogo, ou null se nenhum se aplica",
    )
    justificativa: str = Field(
        description="Relacao entre o diagnostico e o runbook escolhido",
    )
    confianca: float = Field(ge=0, le=1)


class Recomendacao(BaseModel):
    runbook: Runbook | None
    justificativa: str
    confianca: float


class Tratativa(BaseModel):
    rota: Rota = Field(description="Rota efetivamente executada")
    roteamento: Roteamento = Field(description="Decisao original do roteador")
    motivo: str
    diagnostico: Diagnostico | None = None
    recomendacao: Recomendacao | None = None


# 6. Registro e respostas da API

StatusProcessamento = Literal["processando", "concluido", "falha", "resolvido"]


class RegistroIncidente(BaseModel):
    incidente: Incidente
    processamento: StatusProcessamento
    tratativa: Tratativa | None = None
    erro: str | None = None
    recebido_em: datetime
    atualizado_em: datetime


class ItemRecebido(BaseModel):
    id: str | None
    fingerprint: str
    acao: Literal["processando", "duplicado", "resolvido", "ignorado"]


class RespostaWebhook(BaseModel):
    recebidos: list[ItemRecebido]
