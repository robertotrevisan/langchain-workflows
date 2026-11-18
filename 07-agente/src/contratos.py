from datetime import datetime
from typing import Annotated, Any, Literal

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


# 3. Dados devolvidos pelo ambiente simulado


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


# 4. Conclusao do agente


class Desfecho(BaseModel):
    """Resposta final do agente em cada ciclo. O codigo confere o status contra
    as mensagens antes de aceita-lo (ver agente.validar_desfecho)."""

    status: Literal["resolvido", "escalado", "sem_acao"] = Field(
        description=(
            "resolvido: uma acao foi executada e uma consulta posterior mostrou "
            "o servico saudavel. escalado: precisa de um humano. sem_acao: "
            "nenhuma acao e necessaria, como alerta falso ou ja normalizado."
        )
    )
    resumo: str = Field(description="O que foi observado e feito, em poucas frases")
    causa_provavel: str | None = Field(
        default=None, description="Hipotese de causa, marcada como inferencia"
    )
    verificacao: str = Field(
        default="",
        description="Evidencia consultada depois da ultima acao que sustenta o status",
    )


# 5. Auditoria e aprovacao

TipoEvento = Literal[
    "recebido",
    "raciocinio",
    "consulta",
    "acao",
    "resultado",
    "aprovacao_solicitada",
    "aprovacao",
    "desfecho",
    "reentrada",
    "suprimido",
    "resolvido",
    "limite",
    "falha",
]


class EventoTimeline(BaseModel):
    momento: datetime
    tipo: TipoEvento
    detalhe: str
    dados: dict[str, Any] = Field(default_factory=dict)


class AcaoPendente(BaseModel):
    interrupcao_id: str = Field(description="Id da pausa do agente a retomar")
    ferramenta: str
    argumentos: dict[str, Any]


class PedidoAprovacao(BaseModel):
    """A decisao vale para todas as acoes pendentes do incidente."""

    decisao: Literal["aprovar", "rejeitar"]
    motivo: str | None = Field(default=None, max_length=500)


# 6. Registro e respostas da API

StatusProcessamento = Literal[
    "processando",
    "aguardando_aprovacao",
    "aguardando_confirmacao",
    "escalado",
    "encerrado",
    "resolvido",
    "falha",
]


class RegistroIncidente(BaseModel):
    incidente: Incidente
    processamento: StatusProcessamento
    desfecho: Desfecho | None = None
    pendentes: list[AcaoPendente] = Field(default_factory=list)
    ciclos: int = Field(default=1, description="1 + numero de reentradas")
    timeline: list[EventoTimeline] = Field(default_factory=list)
    erro: str | None = None
    recebido_em: datetime
    atualizado_em: datetime


class ItemRecebido(BaseModel):
    id: str | None
    fingerprint: str
    acao: Literal[
        "processando",
        "duplicado",
        "reentrada",
        "suprimido",
        "escalado",
        "resolvido",
        "ignorado",
    ]


class RespostaWebhook(BaseModel):
    recebidos: list[ItemRecebido]
