import json

import pytest
from conftest import ModeloFalso, ler_entrada
from fastapi.testclient import TestClient

from app import criar_app
from workflow import Investigacao


@pytest.fixture
def cliente(fontes):
    with TestClient(criar_app(Investigacao(ModeloFalso(), fontes))) as cliente:
        yield cliente


def test_alertmanager_responde_202_e_investiga_em_background(cliente):
    resposta = cliente.post(
        "/webhooks/alertmanager", json=ler_entrada("alertmanager.json")
    )

    assert resposta.status_code == 202
    itens = resposta.json()["recebidos"]
    assert [i["acao"] for i in itens] == ["processando", "processando"]

    # O TestClient executa as background tasks antes de devolver a resposta.
    checkout = cliente.get(f"/incidentes/{itens[0]['id']}").json()
    pagamentos = cliente.get(f"/incidentes/{itens[1]['id']}").json()
    assert checkout["processamento"] == "concluido"
    assert checkout["diagnostico"]["status"] == "completo"
    assert pagamentos["diagnostico"]["status"] == "parcial"


def test_reenvio_do_mesmo_alerta_e_duplicado(cliente):
    primeiro = cliente.post(
        "/webhooks/alertmanager", json=ler_entrada("alertmanager.json")
    )
    segundo = cliente.post(
        "/webhooks/alertmanager", json=ler_entrada("alertmanager.json")
    )

    ids = [i["id"] for i in primeiro.json()["recebidos"]]
    assert [i["acao"] for i in segundo.json()["recebidos"]] == [
        "duplicado",
        "duplicado",
    ]
    assert [i["id"] for i in segundo.json()["recebidos"]] == ids


def test_resolved_fecha_o_incidente_e_novo_disparo_abre_outro(cliente):
    [primeiro, _] = cliente.post(
        "/webhooks/alertmanager", json=ler_entrada("alertmanager.json")
    ).json()["recebidos"]

    resolvido = cliente.post(
        "/webhooks/alertmanager", json=ler_entrada("alertmanager-resolvido.json")
    ).json()["recebidos"]
    assert resolvido == [{**primeiro, "acao": "resolvido"}]
    registro = cliente.get(f"/incidentes/{primeiro['id']}").json()
    assert registro["processamento"] == "resolvido"
    assert registro["diagnostico"] is not None

    [novo, _] = cliente.post(
        "/webhooks/alertmanager", json=ler_entrada("alertmanager.json")
    ).json()["recebidos"]
    assert novo["acao"] == "processando"
    assert novo["id"] != primeiro["id"]


def test_resolved_sem_incidente_aberto_e_ignorado(cliente):
    resposta = cliente.post(
        "/webhooks/alertmanager", json=ler_entrada("alertmanager-resolvido.json")
    )
    assert resposta.json()["recebidos"][0]["acao"] == "ignorado"


def test_cloudwatch_aceita_text_plain_do_sns(cliente):
    resposta = cliente.post(
        "/webhooks/cloudwatch",
        content=json.dumps(ler_entrada("cloudwatch-alarme.json")),
        headers={"Content-Type": "text/plain; charset=UTF-8"},
    )

    assert resposta.status_code == 202
    [item] = resposta.json()["recebidos"]
    assert item["acao"] == "processando"
    registro = cliente.get(f"/incidentes/{item['id']}").json()
    assert registro["incidente"]["servico"] == "checkout-api"
    assert registro["diagnostico"]["status"] == "completo"


def test_cloudwatch_confirmacao_de_inscricao_nao_cria_incidente(cliente):
    resposta = cliente.post(
        "/webhooks/cloudwatch",
        content=json.dumps(ler_entrada("cloudwatch-confirmacao.json")),
        headers={"Content-Type": "text/plain; charset=UTF-8"},
    )
    assert resposta.status_code == 202
    assert resposta.json() == {"recebidos": []}


def test_cloudwatch_corpo_invalido_e_422(cliente):
    resposta = cliente.post("/webhooks/cloudwatch", content=b"nao e json")
    assert resposta.status_code == 422


def test_generico_sem_cenario_fica_inconclusivo(cliente):
    [item] = cliente.post(
        "/webhooks/generico", json=ler_entrada("generico.json")
    ).json()["recebidos"]

    registro = cliente.get(f"/incidentes/{item['id']}").json()
    assert registro["processamento"] == "concluido"
    assert registro["diagnostico"]["status"] == "inconclusivo"


def test_falha_no_workflow_marca_falha_e_libera_novo_disparo(fontes):
    app = criar_app(Investigacao(ModeloFalso(falhar_consolidacao=True), fontes))
    with TestClient(app) as cliente:
        payload = ler_entrada("generico.json") | {"servico": "checkout-api"}
        [item] = cliente.post("/webhooks/generico", json=payload).json()["recebidos"]

        registro = cliente.get(f"/incidentes/{item['id']}").json()
        assert registro["processamento"] == "falha"
        assert "modelo indisponivel" in registro["erro"]

        [novo] = cliente.post("/webhooks/generico", json=payload).json()["recebidos"]
        assert novo["acao"] == "processando"


def test_incidente_inexistente_e_404(cliente):
    assert cliente.get("/incidentes/nao-existe").status_code == 404
