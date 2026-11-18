import pytest

from fontes import FonteIndisponivel


def test_le_cenario_do_servico(fontes):
    metricas = fontes.consultar_metricas("checkout-api", "30m")
    deploys = fontes.consultar_deploys("checkout-api", "30m")

    assert "pool_conexoes_uso" in [m.nome for m in metricas]
    assert deploys[-1].versao == "v2.14.0"


def test_fonte_marcada_indisponivel_no_cenario(fontes):
    with pytest.raises(FonteIndisponivel, match="logs"):
        fontes.consultar_logs("pagamentos-worker", "30m")


def test_servico_sem_cenario(fontes):
    with pytest.raises(FonteIndisponivel, match="nenhum cenario"):
        fontes.consultar_metricas("busca-api", "30m")


@pytest.mark.parametrize("servico", ["../config", "a/b", "", "x" * 101])
def test_nome_de_servico_invalido_nao_vira_caminho(fontes, servico):
    with pytest.raises(FonteIndisponivel, match="invalido"):
        fontes.consultar_metricas(servico, "30m")
