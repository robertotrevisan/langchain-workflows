import json

import pytest
from conftest import agir, concluir, consultar, criar_agente, ler_entrada
from fastapi.testclient import TestClient

from app import criar_app

# O TestClient executa as background tasks antes de devolver a resposta, entao
# cada POST ja deixa o agente parado (concluido ou aguardando aprovacao).


def cliente_para(ambiente, roteiro, token=None, **opcoes) -> TestClient:
    return TestClient(criar_app(criar_agente(ambiente, roteiro, **opcoes), token))


def postar(cliente, entrada: str, rota: str = "alertmanager") -> list[dict]:
    resposta = cliente.post(f"/webhooks/{rota}", json=ler_entrada(entrada))
    assert resposta.status_code == 202, resposta.text
    return resposta.json()["recebidos"]


def tipos(registro: dict) -> list[str]:
    return [e["tipo"] for e in registro["timeline"]]


def test_fluxo_com_aprovacao_ate_resolved_da_origem(ambiente):
    roteiro = [
        consultar(),
        agir("reverter_deploy"),
        consultar(),
        concluir("resolvido", "deploy revertido"),
    ]
    with cliente_para(
        ambiente, roteiro, acoes_com_aprovacao=("reverter_deploy",)
    ) as cliente:
        [item] = postar(cliente, "alertmanager-checkout.json")
        assert item["acao"] == "processando"

        registro = cliente.get(f"/incidentes/{item['id']}").json()
        assert registro["processamento"] == "aguardando_aprovacao"
        assert registro["pendentes"][0]["ferramenta"] == "reverter_deploy"

        # Reenvio da origem enquanto aguarda humano nao dispara nada.
        assert postar(cliente, "alertmanager-checkout.json")[0]["acao"] == "duplicado"

        resposta = cliente.post(
            f"/incidentes/{item['id']}/aprovacao",
            json={"decisao": "aprovar", "aprovador": "ana", "motivo": "ok do oncall"},
        )
        assert resposta.status_code == 202

        registro = cliente.get(f"/incidentes/{item['id']}").json()
        assert registro["processamento"] == "aguardando_confirmacao"
        assert registro["desfecho"]["status"] == "resolvido"
        assert tipos(registro) == [
            "recebido",
            "consulta",
            "resultado",
            "acao",
            "aprovacao_solicitada",
            "aprovacao",
            "resultado",
            "consulta",
            "resultado",
            "desfecho",
        ]

        [fim] = postar(cliente, "alertmanager-resolvido.json")
        assert fim == {**item, "acao": "resolvido"}
        registro = cliente.get(f"/incidentes/{item['id']}").json()
        assert registro["processamento"] == "resolvido"


def test_reentrada_pelo_webhook_retoma_o_mesmo_incidente(ambiente):
    roteiro = [
        agir("reiniciar_servico"),
        consultar(),
        concluir("resolvido", "reinicio aliviou"),
        consultar(),
        agir("reverter_deploy"),
        consultar(),
        concluir("resolvido", "deploy revertido"),
    ]
    with cliente_para(ambiente, roteiro) as cliente:
        [primeiro] = postar(cliente, "alertmanager-checkout.json")
        [segundo] = postar(cliente, "alertmanager-checkout.json")

        assert segundo == {**primeiro, "acao": "reentrada"}
        registro = cliente.get(f"/incidentes/{primeiro['id']}").json()
        assert registro["ciclos"] == 2
        assert registro["desfecho"]["resumo"] == "deploy revertido"
        assert tipos(registro).count("desfecho") == 2
        assert "reentrada" in tipos(registro)
        assert ambiente.consultar_estado("checkout-api")["versao"] == "v2.13.4"


def test_alerta_causado_pela_acao_e_suprimido(ambiente):
    roteiro = [agir("reiniciar_servico"), consultar(), concluir("resolvido")]
    with cliente_para(ambiente, roteiro) as cliente:
        [checkout] = postar(cliente, "alertmanager-checkout.json")
        [efeito] = postar(cliente, "alertmanager-efeito-colateral.json")

        assert efeito["acao"] == "suprimido"
        assert efeito["id"] == checkout["id"]
        registro = cliente.get(f"/incidentes/{checkout['id']}").json()
        assert registro["timeline"][-1]["tipo"] == "suprimido"
        assert "CheckoutPodRestarting" in registro["timeline"][-1]["detalhe"]


def test_rejeicao_pela_api(ambiente):
    roteiro = [agir("reverter_deploy"), concluir("escalado", "humano vetou")]
    with cliente_para(
        ambiente, roteiro, acoes_com_aprovacao=("reverter_deploy",)
    ) as cliente:
        [item] = postar(cliente, "alertmanager-checkout.json")
        cliente.post(
            f"/incidentes/{item['id']}/aprovacao",
            json={
                "decisao": "rejeitar",
                "aprovador": "ana",
                "motivo": "freeze de fim de ano",
            },
        )
        registro = cliente.get(f"/incidentes/{item['id']}").json()
        assert registro["processamento"] == "escalado"
        assert ambiente.consultar_estado("checkout-api")["versao"] == "v2.14.0"


def test_aprovacao_fora_de_hora_e_409_e_inexistente_404(ambiente):
    with cliente_para(ambiente, [concluir("sem_acao")]) as cliente:
        [item] = postar(cliente, "alertmanager-checkout.json")
        corpo = {"decisao": "aprovar", "aprovador": "ana"}
        assert (
            cliente.post(f"/incidentes/{item['id']}/aprovacao", json=corpo).status_code
            == 409
        )
        assert cliente.post("/incidentes/x/aprovacao", json=corpo).status_code == 404
        invalida = cliente.post(
            f"/incidentes/{item['id']}/aprovacao",
            json={"decisao": "talvez", "aprovador": "ana"},
        )
        assert invalida.status_code == 422


def test_cloudwatch_aceita_text_plain_do_sns(ambiente):
    with cliente_para(ambiente, [consultar(), concluir("escalado")]) as cliente:
        resposta = cliente.post(
            "/webhooks/cloudwatch",
            content=json.dumps(ler_entrada("cloudwatch-alarme.json")),
            headers={"Content-Type": "text/plain; charset=UTF-8"},
        )
        [item] = resposta.json()["recebidos"]
        registro = cliente.get(f"/incidentes/{item['id']}").json()
        assert registro["incidente"]["servico"] == "checkout-api"
        assert registro["processamento"] == "escalado"


def test_servico_sem_cenario_escala(ambiente):
    roteiro = [consultar(servico="busca-api"), concluir("escalado")]
    with cliente_para(ambiente, roteiro) as cliente:
        [item] = postar(cliente, "generico.json", rota="generico")
        registro = cliente.get(f"/incidentes/{item['id']}").json()
        assert registro["processamento"] == "escalado"
        resultado = next(e for e in registro["timeline"] if e["tipo"] == "resultado")
        assert "FonteIndisponivel" in resultado["detalhe"]


def test_falha_do_modelo_marca_falha_sem_vazar_e_libera_disparo(ambiente):
    roteiro = [RuntimeError("401 chave invalida"), concluir("sem_acao")]
    with cliente_para(ambiente, roteiro) as cliente:
        [item] = postar(cliente, "alertmanager-checkout.json")
        registro = cliente.get(f"/incidentes/{item['id']}")
        assert registro.json()["processamento"] == "falha"
        assert registro.json()["erro"] == "RuntimeError"
        assert "chave invalida" not in registro.text

        [novo] = postar(cliente, "alertmanager-checkout.json")
        assert novo["acao"] == "processando" and novo["id"] != item["id"]


def test_aprovacao_exige_aprovador(ambiente):
    with cliente_para(ambiente, []) as cliente:
        resposta = cliente.post("/incidentes/x/aprovacao", json={"decisao": "aprovar"})
        assert resposta.status_code == 422


@pytest.fixture
def cliente_com_tokens(ambiente):
    app = criar_app(
        criar_agente(ambiente, []), token="webhook", token_aprovacao="aprovacao"
    )
    with TestClient(app) as cliente:
        yield cliente


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_token_do_webhook_nao_aprova(cliente_com_tokens):
    c = cliente_com_tokens
    corpo = {"decisao": "aprovar", "aprovador": "ana"}
    url = "/incidentes/x/aprovacao"
    assert c.post(url, json=corpo, headers=bearer("webhook")).status_code == 401
    assert c.post(url, json=corpo, headers=bearer("aprovacao")).status_code == 404


def test_token_de_aprovacao_nao_recebe_webhook(cliente_com_tokens):
    c = cliente_com_tokens
    resposta = c.post(
        "/webhooks/generico", json={"titulo": "x"}, headers=bearer("aprovacao")
    )
    assert resposta.status_code == 401


def test_consulta_aceita_os_dois_tokens(cliente_com_tokens):
    c = cliente_com_tokens
    assert c.get("/incidentes/x").status_code == 401
    assert c.get("/incidentes/x", headers=bearer("webhook")).status_code == 404
    assert c.get("/incidentes/x", headers=bearer("aprovacao")).status_code == 404


def test_webhook_protegido_sem_token_de_aprovacao_fecha_a_aprovacao(ambiente):
    with cliente_para(ambiente, [], token="webhook") as c:
        resposta = c.post(
            "/incidentes/x/aprovacao",
            json={"decisao": "aprovar", "aprovador": "ana"},
            headers=bearer("webhook"),
        )
        assert resposta.status_code == 503


@pytest.fixture
def cliente_com_token(ambiente):
    with cliente_para(ambiente, [], token="segredo") as cliente:
        yield cliente


def test_token_protege_webhooks_e_consulta(cliente_com_token):
    c = cliente_com_token
    assert c.post("/webhooks/generico", json={"titulo": "x"}).status_code == 401
    assert c.get("/incidentes/x").status_code == 401
    autorizado = c.get("/incidentes/x", headers={"Authorization": "Bearer segredo"})
    assert autorizado.status_code == 404
