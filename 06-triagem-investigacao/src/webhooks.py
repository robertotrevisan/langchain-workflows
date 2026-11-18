"""Adaptadores: convertem o payload de cada origem para o contrato Incidente.

Do adaptador em diante, nada no workflow depende da origem do alerta.
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime

from contratos import (
    MAX_CHAVE_LABEL,
    MAX_DESCRICAO,
    MAX_LABELS,
    MAX_SERVICO,
    MAX_VALOR_LABEL,
    AlarmeCloudWatch,
    EventoGenerico,
    Incidente,
    MensagemSns,
    Severidade,
    WebhookAlertmanager,
)

LABELS_SERVICO = ("service", "servico", "app", "job")
DIMENSOES_SERVICO = ("ServiceName", "FunctionName", "DBInstanceIdentifier", "service")


def novo_id() -> str:
    return uuid.uuid4().hex[:12]


SUFIXO_TRUNCADO = " [truncado]"


def _truncar(texto: str) -> str:
    if len(texto) <= MAX_DESCRICAO:
        return texto
    return texto[: MAX_DESCRICAO - len(SUFIXO_TRUNCADO)] + SUFIXO_TRUNCADO


def _limitar_labels(labels: dict[str, str]) -> dict[str, str]:
    """Origens externas podem mandar labels sem limite; o adaptador corta em vez
    de rejeitar o alerta."""
    return {
        chave[:MAX_CHAVE_LABEL]: valor[:MAX_VALOR_LABEL]
        for chave, valor in list(labels.items())[:MAX_LABELS]
    }


def _limitar_servico(servico: str | None) -> str | None:
    return servico[:MAX_SERVICO] if servico else servico


def _severidade(valor: str | None) -> Severidade:
    valor = (valor or "").lower()
    if valor in ("critical", "critico", "page"):
        return "critical"
    if valor in ("info", "informativo"):
        return "info"
    return "warning"


# 1. Alertmanager: um POST pode trazer varios alertas agrupados


def de_alertmanager(payload: WebhookAlertmanager) -> list[Incidente]:
    incidentes = []
    for alerta in payload.alerts:
        labels = alerta.labels
        anotacoes = alerta.annotations
        servico = next((labels[c] for c in LABELS_SERVICO if c in labels), None)
        incidentes.append(
            Incidente(
                id=novo_id(),
                fingerprint=alerta.fingerprint,
                origem="alertmanager",
                status=alerta.status,
                titulo=labels.get("alertname", "alerta sem nome")[:200],
                descricao=_truncar(
                    anotacoes.get("description") or anotacoes.get("summary") or ""
                ),
                servico=_limitar_servico(servico),
                severidade=_severidade(labels.get("severity")),
                labels=_limitar_labels(labels),
                inicio=alerta.startsAt,
            )
        )
    return incidentes


# 2. CloudWatch: o alarme chega via SNS, como string JSON em Message


def ler_sns(corpo: bytes) -> MensagemSns:
    """O SNS envia Content-Type text/plain; o corpo precisa ser lido cru."""
    return MensagemSns.model_validate_json(corpo)


def de_cloudwatch(mensagem: MensagemSns) -> Incidente | None:
    """Devolve None para INSUFFICIENT_DATA, que nao e falha nem recuperacao."""
    alarme = AlarmeCloudWatch.model_validate(json.loads(mensagem.Message))
    if alarme.NewStateValue == "INSUFFICIENT_DATA":
        return None

    dimensoes = {d.name: d.value for d in alarme.Trigger.Dimensions}
    servico = next((dimensoes[c] for c in DIMENSOES_SERVICO if c in dimensoes), None)
    nome = f"{alarme.AlarmName} {alarme.AlarmDescription or ''}".lower()
    severidade: Severidade = "critical" if "critical" in nome else "warning"

    return Incidente(
        id=novo_id(),
        fingerprint=alarme.AlarmArn,
        origem="cloudwatch",
        status="firing" if alarme.NewStateValue == "ALARM" else "resolved",
        titulo=alarme.AlarmName[:200],
        descricao=_truncar(alarme.NewStateReason),
        servico=_limitar_servico(servico),
        severidade=severidade,
        labels=_limitar_labels(
            {
                "metrica": alarme.Trigger.MetricName or "",
                "namespace": alarme.Trigger.Namespace or "",
                **dimensoes,
            }
        ),
        inicio=alarme.StateChangeTime,
    )


# 3. Generico: o cliente ja envia algo proximo do contrato


def de_generico(evento: EventoGenerico) -> Incidente:
    fingerprint = (
        evento.fingerprint
        or hashlib.sha256(
            f"{evento.titulo}|{evento.servico or ''}".encode()
        ).hexdigest()[:16]
    )
    return Incidente(
        id=novo_id(),
        fingerprint=fingerprint,
        origem="generico",
        status=evento.status,
        titulo=evento.titulo,
        descricao=evento.descricao,
        servico=evento.servico,
        severidade=evento.severidade,
        labels=evento.labels,
        inicio=evento.inicio or datetime.now(UTC),
    )
