import json

from conftest import ler_entrada

from contratos import MAX_DESCRICAO, EventoGenerico, WebhookAlertmanager
from webhooks import de_alertmanager, de_cloudwatch, de_generico, ler_sns


def test_alertmanager_agrupado_vira_um_incidente_por_alerta():
    payload = WebhookAlertmanager.model_validate(ler_entrada("alertmanager.json"))

    incidentes = de_alertmanager(payload)

    assert [i.servico for i in incidentes] == ["checkout-api", "pagamentos-worker"]
    checkout = incidentes[0]
    assert checkout.fingerprint == "a1b2c3d4e5f60718"
    assert checkout.titulo == "CheckoutHighErrorRate"
    assert checkout.severidade == "critical"
    assert checkout.status == "firing"
    assert "18%" in checkout.descricao
    assert incidentes[0].id != incidentes[1].id


def test_alertmanager_resolvido():
    payload = WebhookAlertmanager.model_validate(
        ler_entrada("alertmanager-resolvido.json")
    )
    [incidente] = de_alertmanager(payload)
    assert incidente.status == "resolved"


def test_alertmanager_descricao_longa_e_truncada():
    dados = ler_entrada("alertmanager.json")
    dados["alerts"][0]["annotations"]["description"] = "x" * (MAX_DESCRICAO + 500)

    [incidente, _] = de_alertmanager(WebhookAlertmanager.model_validate(dados))

    assert len(incidente.descricao) == MAX_DESCRICAO
    assert incidente.descricao.endswith("[truncado]")


def test_cloudwatch_alarme_via_sns():
    mensagem = ler_sns(json.dumps(ler_entrada("cloudwatch-alarme.json")).encode())

    incidente = de_cloudwatch(mensagem)

    assert incidente.origem == "cloudwatch"
    assert incidente.status == "firing"
    assert incidente.servico == "checkout-api"
    assert incidente.severidade == "critical"
    assert incidente.fingerprint.startswith("arn:aws:cloudwatch:")
    assert incidente.labels["metrica"] == "HTTPCode_Target_5XX_Count"


def test_cloudwatch_ok_vira_resolved_e_insufficient_data_e_ignorado():
    entrada = ler_entrada("cloudwatch-alarme.json")
    alarme = json.loads(entrada["Message"])

    alarme["NewStateValue"] = "OK"
    entrada["Message"] = json.dumps(alarme)
    assert de_cloudwatch(ler_sns(json.dumps(entrada).encode())).status == "resolved"

    alarme["NewStateValue"] = "INSUFFICIENT_DATA"
    entrada["Message"] = json.dumps(alarme)
    assert de_cloudwatch(ler_sns(json.dumps(entrada).encode())) is None


def test_generico_deriva_fingerprint_estavel():
    evento = EventoGenerico.model_validate(ler_entrada("generico.json"))

    primeiro, segundo = de_generico(evento), de_generico(evento)

    assert primeiro.fingerprint == segundo.fingerprint
    assert primeiro.id != segundo.id
