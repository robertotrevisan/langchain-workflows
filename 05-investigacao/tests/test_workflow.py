import asyncio
from datetime import UTC, datetime

import pytest
from conftest import ModeloFalso

from contratos import Incidente
from workflow import Investigacao


def incidente(servico: str | None) -> Incidente:
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


def executar(modelo, fontes, servico):
    return asyncio.run(Investigacao(modelo, fontes).executar(incidente(servico)))


def test_todas_as_fontes_respondem(modelo, fontes):
    diagnostico = executar(modelo, fontes, "checkout-api")

    assert diagnostico.status == "completo"
    assert [a.perspectiva for a in diagnostico.analises] == [
        "metricas",
        "logs",
        "deploys",
    ]
    assert all(a.status == "ok" for a in diagnostico.analises)
    assert diagnostico.consolidacao is not None


def test_cada_ramo_recebe_os_dados_da_sua_fonte(modelo, fontes):
    executar(modelo, fontes, "checkout-api")

    prompts = "\n".join(modelo.prompts["AnaliseModelo"])
    assert "pool_conexoes_uso" in prompts
    assert "HikariPool-1" in prompts
    assert "v2.14.0" in prompts


def test_fonte_indisponivel_gera_resultado_parcial(modelo, fontes):
    diagnostico = executar(modelo, fontes, "pagamentos-worker")

    assert diagnostico.status == "parcial"
    logs = next(a for a in diagnostico.analises if a.perspectiva == "logs")
    assert logs.status == "falha"
    assert logs.origem_falha == "fonte"
    assert "FonteIndisponivel" in logs.erro
    assert len(modelo.prompts["Consolidacao"]) == 1
    assert '"status": "falha"' in modelo.prompts["Consolidacao"][0]


@pytest.mark.parametrize("servico", [None, "busca-api"])
def test_sem_dados_e_inconclusivo_e_nao_consolida(modelo, fontes, servico):
    diagnostico = executar(modelo, fontes, servico)

    assert diagnostico.status == "inconclusivo"
    assert diagnostico.consolidacao is None
    assert modelo.prompts["AnaliseModelo"] == []
    assert modelo.prompts["Consolidacao"] == []


def test_falha_na_consolidacao_propaga(fontes):
    with pytest.raises(RuntimeError, match="modelo indisponivel"):
        executar(ModeloFalso(falhar_consolidacao=True), fontes, "checkout-api")


def test_modelo_falhando_em_todos_os_ramos_e_falha_e_nao_inconclusivo(fontes):
    with pytest.raises(RuntimeError, match="nenhuma perspectiva concluiu"):
        executar(ModeloFalso(falhar_analises=True), fontes, "checkout-api")


def test_modelo_falhando_e_fonte_indisponivel_ainda_e_falha(fontes):
    # pagamentos-worker: logs falha por fonte, os demais por modelo.
    with pytest.raises(RuntimeError, match="401"):
        executar(ModeloFalso(falhar_analises=True), fontes, "pagamentos-worker")
