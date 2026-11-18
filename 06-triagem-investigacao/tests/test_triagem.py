import asyncio
from datetime import UTC, datetime

import pytest
from conftest import ModeloFalso, criar_triagem

from contratos import Incidente


def incidente(servico: str | None = "checkout-api") -> Incidente:
    return Incidente(
        id="teste",
        fingerprint="fp",
        origem="generico",
        status="firing",
        titulo="Alerta de teste",
        descricao="descricao",
        servico=servico,
        inicio=datetime(2026, 11, 16, 14, 10, tzinfo=UTC),
    )


def triar(modelo, fontes, servico="checkout-api", limiar=0.7, disparos=0):
    triagem = criar_triagem(modelo, fontes, limiar)
    return asyncio.run(triagem.executar(incidente(servico), disparos))


def test_investigar_traz_diagnostico_e_runbook_do_catalogo(fontes):
    modelo = ModeloFalso(destino="investigar")

    tratativa = triar(modelo, fontes)

    assert tratativa.rota == "investigar"
    assert tratativa.diagnostico.status == "completo"
    assert tratativa.recomendacao.runbook.id == "rb-pool-conexoes"
    assert tratativa.recomendacao.runbook.passos
    # O catalogo inteiro vai no prompt, para o modelo escolher entre ids reais.
    assert "rb-reverter-deploy" in modelo.prompts["EscolhaRunbook"][0]


@pytest.mark.parametrize("destino", ["ruido", "humano"])
def test_ruido_e_humano_nao_investigam(fontes, destino):
    modelo = ModeloFalso(destino=destino)

    tratativa = triar(modelo, fontes)

    assert tratativa.rota == destino
    assert tratativa.diagnostico is None
    assert tratativa.recomendacao is None
    assert modelo.prompts["AnaliseModelo"] == []
    assert modelo.prompts["EscolhaRunbook"] == []


@pytest.mark.parametrize("destino", ["investigar", "ruido"])
def test_confianca_abaixo_do_limiar_vira_humano(fontes, destino):
    modelo = ModeloFalso(destino=destino, confianca=0.5)

    tratativa = triar(modelo, fontes, limiar=0.7)

    assert tratativa.rota == "humano"
    assert tratativa.roteamento.destino == destino
    assert "abaixo do limiar" in tratativa.motivo
    assert modelo.prompts["AnaliseModelo"] == []


def test_confianca_no_limiar_segue_a_rota(fontes):
    tratativa = triar(ModeloFalso(destino="ruido", confianca=0.7), fontes, limiar=0.7)
    assert tratativa.rota == "ruido"


def test_disparos_anteriores_chegam_ao_roteador(fontes):
    modelo = ModeloFalso()
    triar(modelo, fontes, disparos=4)
    assert "na ultima hora: 4" in modelo.prompts["Roteamento"][0]


def test_runbook_fora_do_catalogo_e_descartado(fontes):
    tratativa = triar(ModeloFalso(runbook_id="rb-inventado"), fontes)

    assert tratativa.recomendacao.runbook is None
    assert tratativa.recomendacao.confianca == 0
    assert "fora do catalogo" in tratativa.recomendacao.justificativa


def test_nenhum_runbook_aplicavel(fontes):
    tratativa = triar(ModeloFalso(runbook_id=None), fontes)

    assert tratativa.recomendacao.runbook is None
    assert tratativa.recomendacao.justificativa == "runbook de teste"


def test_falha_na_recomendacao_preserva_o_diagnostico(fontes):
    tratativa = triar(ModeloFalso(falhar_recomendacao=True), fontes)

    assert tratativa.diagnostico.status == "completo"
    assert tratativa.recomendacao.runbook is None
    assert tratativa.recomendacao.justificativa == (
        "recomendacao indisponivel (RuntimeError)"
    )


def test_inconclusivo_nao_pede_recomendacao(fontes):
    modelo = ModeloFalso()

    tratativa = triar(modelo, fontes, servico="busca-api")

    assert tratativa.diagnostico.status == "inconclusivo"
    assert tratativa.recomendacao is None
    assert modelo.prompts["EscolhaRunbook"] == []
