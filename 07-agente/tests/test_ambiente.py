import pytest

from ambiente import AcaoInvalida, FonteIndisponivel


def taxa_erro(ambiente, servico="checkout-api") -> float:
    metricas = ambiente.consultar_metricas(servico, "30m")
    serie = next(m for m in metricas if m.nome == "taxa_erro_5xx")
    return serie.pontos[-1].valor


def test_comeca_degradado(ambiente):
    assert taxa_erro(ambiente) == 18.1


def test_reverter_resolve_e_atualiza_deploys(ambiente):
    ambiente.reverter_deploy("checkout-api")
    assert taxa_erro(ambiente) < 1
    assert taxa_erro(ambiente) < 1  # continua saudavel
    estado = ambiente.consultar_estado("checkout-api")
    assert estado["versao"] == "v2.13.4"
    assert estado["versao_anterior"] is None
    deploys = ambiente.consultar_deploys("checkout-api", "30m")
    assert [d.status for d in deploys if d.versao == "v2.14.0"] == ["revertido"]
    assert deploys[-1].autor == "agente"


def test_reverter_duas_vezes_e_invalido(ambiente):
    ambiente.reverter_deploy("checkout-api")
    with pytest.raises(AcaoInvalida, match="versao anterior"):
        ambiente.reverter_deploy("checkout-api")


def test_reiniciar_melhora_e_recai(ambiente):
    ambiente.reiniciar_servico("checkout-api")
    assert taxa_erro(ambiente) < 1  # primeira verificacao: parece resolvido
    assert taxa_erro(ambiente) == 18.1  # a transacao longa esgota o pool de novo


def test_escalar_sem_efeito_mas_muda_replicas(ambiente):
    ambiente.escalar_replicas("checkout-api", 8)
    assert taxa_erro(ambiente) == 18.1
    assert ambiente.consultar_estado("checkout-api")["replicas"] == 8


@pytest.mark.parametrize("replicas", [0, 4, 99])
def test_escalar_invalido(ambiente, replicas):
    with pytest.raises(AcaoInvalida):
        ambiente.escalar_replicas("checkout-api", replicas)


def test_estado_nao_revela_saude(ambiente):
    assert "saude" not in ambiente.consultar_estado("checkout-api")


def test_fonte_indisponivel_no_cenario(ambiente):
    with pytest.raises(FonteIndisponivel, match="logs"):
        ambiente.consultar_logs("pagamentos-worker", "30m")


@pytest.mark.parametrize("servico", ["inexistente", "../segredo"])
def test_servico_sem_cenario_ou_invalido(ambiente, servico):
    with pytest.raises(FonteIndisponivel):
        ambiente.consultar_metricas(servico, "30m")


def test_estado_isolado_por_instancia(ambiente):
    from ambiente import AmbienteSimulado

    ambiente.reverter_deploy("checkout-api")
    outro = AmbienteSimulado(ambiente.diretorio)
    assert taxa_erro(outro) == 18.1
