from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

MAX_DESCRICAO = 4000
MAX_SERVICO = 100
MAX_LABELS = 30
MAX_CHAVE_LABEL = 100
MAX_VALOR_LABEL = 300
MAX_ALERTAS_POR_POST = 100

# Labels entram no prompt: quantidade e tamanho limitados para conter custo.
Labels = Annotated[
    dict[
        Annotated[str, StringConstraints(max_length=MAX_CHAVE_LABEL)],
        Annotated[str, StringConstraints(max_length=MAX_VALOR_LABEL)],
    ],
    Field(max_length=MAX_LABELS),
]

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
        max_length=MAX_SERVICO,
        description="Servico afetado; sem servico nao ha fonte para consultar",
    )
    severidade: Severidade = "warning"
    labels: Labels = Field(default_factory=dict)
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
    servico: str | None = Field(default=None, max_length=MAX_SERVICO)
    severidade: Severidade = "warning"
    labels: Labels = Field(default_factory=dict)
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
    alerts: list[AlertaAlertmanager] = Field(
        min_length=1, max_length=MAX_ALERTAS_POR_POST
    )


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


# 5. Registro e respostas da API

StatusProcessamento = Literal["processando", "concluido", "falha", "resolvido"]


class RegistroIncidente(BaseModel):
    incidente: Incidente
    processamento: StatusProcessamento
    diagnostico: Diagnostico | None = None
    erro: str | None = None
    recebido_em: datetime
    atualizado_em: datetime


class ItemRecebido(BaseModel):
    id: str | None
    fingerprint: str
    acao: Literal["processando", "duplicado", "resolvido", "ignorado"]


class RespostaWebhook(BaseModel):
    recebidos: list[ItemRecebido]
