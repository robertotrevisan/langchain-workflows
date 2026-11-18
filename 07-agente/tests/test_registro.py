from datetime import UTC, datetime, timedelta

import pytest

from agente import Ciclo
from contratos import AcaoPendente, Desfecho, EventoGenerico, EventoTimeline
from registro import Registro, TransicaoInvalida
from webhooks import de_generico


def evento(status="firing", titulo="CheckoutHighErrorRate", fp="fp1"):
    return de_generico(
        EventoGenerico(
            fingerprint=fp, titulo=titulo, servico="checkout-api", status=status
        )
    )


def concluido(status: str, com_acao: bool = False) -> Ciclo:
    eventos = []
    if com_acao:
        eventos.append(
            EventoTimeline(
                momento=datetime.now(UTC), tipo="acao", detalhe="reiniciar_servico"
            )
        )
    return Ciclo("concluido", Desfecho(status=status, resumo="r"), [], eventos)


def pausado() -> Ciclo:
    pendente = AcaoPendente(
        interrupcao_id="i1", ferramenta="reverter_deploy", argumentos={}
    )
    return Ciclo("aguardando_aprovacao", None, [pendente], [])


def abrir(registro: Registro, ciclo: Ciclo | None = None) -> str:
    id = registro.receber(evento()).id
    if ciclo is not None:
        registro.registrar_ciclo(id, ciclo)
    return id


@pytest.mark.parametrize(
    ("status", "processamento"),
    [
        ("resolvido", "aguardando_confirmacao"),
        ("escalado", "escalado"),
        ("sem_acao", "encerrado"),
    ],
)
def test_desfecho_define_o_processamento(status, processamento):
    registro = Registro()
    id = abrir(registro, concluido(status))
    assert registro.obter(id).processamento == processamento


def test_firing_enquanto_processa_e_duplicado():
    registro = Registro()
    id = abrir(registro)
    item = registro.receber(evento())
    assert (item.acao, item.id) == ("duplicado", id)


@pytest.mark.parametrize("status", ["escalado", "sem_acao"])
def test_firing_apos_escalado_ou_encerrado_e_duplicado(status):
    registro = Registro()
    abrir(registro, concluido(status))
    assert registro.receber(evento()).acao == "duplicado"


def test_firing_apos_resolvido_pelo_agente_e_reentrada():
    registro = Registro()
    id = abrir(registro, concluido("resolvido"))
    item = registro.receber(evento())
    assert (item.acao, item.id) == ("reentrada", id)
    r = registro.obter(id)
    assert (r.processamento, r.ciclos) == ("processando", 2)
    assert r.timeline[-1].tipo == "reentrada"


def test_reentradas_alem_do_limite_escalam():
    registro = Registro(max_ciclos=2)
    id = abrir(registro, concluido("resolvido"))
    registro.receber(evento())  # ciclo 2
    registro.registrar_ciclo(id, concluido("resolvido"))
    item = registro.receber(evento())
    assert item.acao == "escalado"
    assert registro.obter(id).processamento == "escalado"
    assert registro.obter(id).timeline[-1].tipo == "limite"


def test_resolved_fecha_e_proximo_firing_abre_outro():
    registro = Registro()
    id = abrir(registro, concluido("resolvido"))
    assert registro.receber(evento(status="resolved")).acao == "resolvido"
    assert registro.obter(id).processamento == "resolvido"
    novo = registro.receber(evento())
    assert novo.acao == "processando" and novo.id != id


def test_resolved_sem_aberto_e_ignorado():
    assert Registro().receber(evento(status="resolved")).acao == "ignorado"


def test_resolved_durante_execucao_prevalece_sobre_desfecho():
    registro = Registro()
    id = abrir(registro)
    registro.receber(evento(status="resolved"))
    registro.registrar_ciclo(id, concluido("escalado"))
    r = registro.obter(id)
    assert r.processamento == "resolvido"
    assert r.desfecho.status == "escalado"


# Aprovacao


def test_pausa_registra_pendentes_e_decisao_retoma():
    registro = Registro()
    id = abrir(registro, pausado())
    r = registro.obter(id)
    assert r.processamento == "aguardando_aprovacao"
    assert [p.ferramenta for p in r.pendentes] == ["reverter_deploy"]

    pendentes = registro.iniciar_decisao(id, aprovar=True, motivo="ok do oncall")

    assert [p.interrupcao_id for p in pendentes] == ["i1"]
    assert (r.processamento, r.pendentes) == ("processando", [])
    assert r.timeline[-1].tipo == "aprovacao"
    assert "ok do oncall" in r.timeline[-1].detalhe


def test_decisao_fora_de_aprovacao_e_invalida():
    registro = Registro()
    id = abrir(registro)
    with pytest.raises(TransicaoInvalida):
        registro.iniciar_decisao(id, aprovar=True, motivo=None)


def test_segunda_decisao_e_invalida():
    registro = Registro()
    id = abrir(registro, pausado())
    registro.iniciar_decisao(id, aprovar=True, motivo=None)
    with pytest.raises(TransicaoInvalida):
        registro.iniciar_decisao(id, aprovar=False, motivo=None)


def test_resolved_descarta_aprovacao_pendente():
    registro = Registro()
    id = abrir(registro, pausado())
    registro.receber(evento(status="resolved"))
    assert registro.obter(id).pendentes == []
    with pytest.raises(TransicaoInvalida):
        registro.iniciar_decisao(id, aprovar=True, motivo=None)


def test_decisao_em_incidente_inexistente():
    with pytest.raises(KeyError):
        Registro().iniciar_decisao("nao-existe", aprovar=True, motivo=None)


# Supressao


def test_alerta_do_mesmo_servico_apos_acao_e_suprimido():
    registro = Registro()
    id = abrir(registro, concluido("resolvido", com_acao=True))
    item = registro.receber(evento(titulo="CheckoutPodRestarting", fp="fp2"))
    assert (item.acao, item.id) == ("suprimido", id)
    assert registro.obter(id).timeline[-1].tipo == "suprimido"


def test_sem_acao_recente_nao_suprime():
    registro = Registro()
    abrir(registro, concluido("resolvido"))
    item = registro.receber(evento(titulo="CheckoutPodRestarting", fp="fp2"))
    assert item.acao == "processando"


def test_acao_fora_da_janela_nao_suprime():
    registro = Registro(janela_supressao=timedelta(minutes=10))
    id = abrir(registro, concluido("resolvido", com_acao=True))
    acao = registro.obter(id).timeline[-2]
    acao.momento -= timedelta(minutes=11)
    item = registro.receber(evento(titulo="CheckoutPodRestarting", fp="fp2"))
    assert item.acao == "processando"


# Falha


def test_falha_libera_o_fingerprint():
    registro = Registro()
    id = abrir(registro)
    registro.falhar(id, "RuntimeError")
    r = registro.obter(id)
    assert (r.processamento, r.erro) == ("falha", "RuntimeError")
    assert registro.receber(evento()).acao == "processando"
