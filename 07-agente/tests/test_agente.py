import asyncio

import pytest
from conftest import (
    SERVICO,
    ModeloRoteirizado,
    agir,
    concluir,
    consultar,
    criar_agente,
)
from langchain_core.messages import ToolMessage

from agente import Agente, validar_desfecho
from contratos import Desfecho, EventoGenerico
from webhooks import de_generico


def incidente(servico: str | None = SERVICO):
    return de_generico(EventoGenerico(titulo="CheckoutHighErrorRate", servico=servico))


def rodar(coro):
    return asyncio.run(coro)


async def mensagens(agente: Agente, inc) -> list:
    estado = await agente.grafo.aget_state({"configurable": {"thread_id": inc.id}})
    return estado.values["messages"]


def ferramentas(agente, inc, status=None) -> list[ToolMessage]:
    return [
        m
        for m in rodar(mensagens(agente, inc))
        if isinstance(m, ToolMessage)
        and m.name != "Desfecho"
        and (status is None or m.status == status)
    ]


# 1. Loop: observar, agir, verificar


def test_resolve_revertendo_o_deploy(ambiente):
    roteiro = [
        consultar(),
        consultar("consultar_deploys"),
        agir("reverter_deploy"),
        consultar(),
        concluir("resolvido"),
    ]
    agente = criar_agente(ambiente, roteiro)
    inc = incidente()

    ciclo = rodar(agente.iniciar(inc))

    assert ciclo.situacao == "concluido"
    assert ciclo.desfecho.status == "resolvido"
    assert ambiente.consultar_estado(SERVICO)["versao"] == "v2.13.4"
    tipos = [e.tipo for e in ciclo.eventos]
    assert tipos.count("consulta") == 3
    assert tipos.count("acao") == 1
    acao = next(e for e in ciclo.eventos if e.tipo == "acao")
    assert "hipotese de teste" in acao.detalhe


def test_sem_servico_escala_sem_chamar_o_modelo(ambiente):
    modelo = ModeloRoteirizado()
    agente = Agente(modelo, ambiente)
    ciclo = rodar(agente.iniciar(incidente(servico=None)))
    assert ciclo.desfecho.status == "escalado"
    assert modelo.chamadas == 0


def test_erro_da_fonte_chega_ao_modelo_como_erro(ambiente):
    roteiro = [
        consultar("consultar_logs", servico="pagamentos-worker"),
        concluir("escalado"),
    ]
    agente = criar_agente(ambiente, roteiro)
    inc = incidente("pagamentos-worker")
    rodar(agente.iniciar(inc))
    [resultado] = ferramentas(agente, inc)
    assert resultado.status == "error"
    assert "FonteIndisponivel" in resultado.content


# 2. Aprovacao humana


def test_acao_destrutiva_pausa_ate_aprovacao(ambiente):
    roteiro = [agir("reverter_deploy"), consultar(), concluir("resolvido")]
    agente = criar_agente(ambiente, roteiro, acoes_com_aprovacao=("reverter_deploy",))
    inc = incidente()

    ciclo = rodar(agente.iniciar(inc))
    assert ciclo.situacao == "aguardando_aprovacao"
    [pendente] = ciclo.pendentes
    assert pendente.ferramenta == "reverter_deploy"
    assert pendente.argumentos["servico"] == SERVICO
    assert ambiente.consultar_estado(SERVICO)["versao"] == "v2.14.0"  # nada feito

    ciclo = rodar(agente.decidir(inc, ciclo.pendentes, aprovar=True, motivo=None))
    assert ciclo.situacao == "concluido"
    assert ciclo.desfecho.status == "resolvido"
    assert ambiente.consultar_estado(SERVICO)["versao"] == "v2.13.4"


def test_rejeicao_nao_executa_e_avisa_o_modelo(ambiente):
    roteiro = [agir("reverter_deploy"), concluir("escalado")]
    agente = criar_agente(ambiente, roteiro, acoes_com_aprovacao=("reverter_deploy",))
    inc = incidente()
    ciclo = rodar(agente.iniciar(inc))

    ciclo = rodar(
        agente.decidir(inc, ciclo.pendentes, aprovar=False, motivo="janela de freeze")
    )

    assert ciclo.desfecho.status == "escalado"
    assert ambiente.consultar_estado(SERVICO)["versao"] == "v2.14.0"
    [rejeitada] = ferramentas(agente, inc)
    assert rejeitada.status == "error"
    assert "janela de freeze" in rejeitada.content


def test_acao_sem_aprovacao_configurada_executa_direto(ambiente):
    roteiro = [agir("reiniciar_servico"), consultar(), concluir("resolvido")]
    agente = criar_agente(ambiente, roteiro, acoes_com_aprovacao=("reverter_deploy",))
    ciclo = rodar(agente.iniciar(incidente()))
    assert ciclo.situacao == "concluido"


def test_acao_de_aprovacao_desconhecida_e_recusada(ambiente):
    with pytest.raises(ValueError, match="desconhecidas"):
        Agente(ModeloRoteirizado(), ambiente, acoes_com_aprovacao=("apagar_banco",))


# 3. Politica de acoes


def test_acao_em_outro_servico_e_bloqueada_antes_da_aprovacao(ambiente):
    roteiro = [
        agir("reverter_deploy", servico="pagamentos-worker"),
        concluir("escalado"),
    ]
    agente = criar_agente(ambiente, roteiro, acoes_com_aprovacao=("reverter_deploy",))
    inc = incidente()

    ciclo = rodar(agente.iniciar(inc))

    assert ciclo.situacao == "concluido"  # nao pausou para aprovacao
    assert ambiente.consultar_estado("pagamentos-worker")["acoes_executadas"] == []
    [bloqueada] = ferramentas(agente, inc)
    assert bloqueada.status == "error"
    assert "servico do incidente" in bloqueada.content


def test_argumento_invalido_e_bloqueado_antes_da_aprovacao(ambiente):
    roteiro = [agir("escalar_replicas", replicas=99), concluir("escalado")]
    agente = criar_agente(ambiente, roteiro, acoes_com_aprovacao=("escalar_replicas",))
    inc = incidente()
    ciclo = rodar(agente.iniciar(inc))
    assert ciclo.situacao == "concluido"
    [bloqueada] = ferramentas(agente, inc)
    assert "argumentos invalidos" in bloqueada.content


def test_limite_de_acoes_por_incidente(ambiente):
    roteiro = [
        agir("reiniciar_servico"),
        agir("escalar_replicas", replicas=6),
        concluir("escalado"),
    ]
    agente = criar_agente(ambiente, roteiro, max_acoes=1)
    inc = incidente()
    rodar(agente.iniciar(inc))
    assert ambiente.consultar_estado(SERVICO)["acoes_executadas"] == [
        "reiniciar_servico"
    ]
    segunda = ferramentas(agente, inc)[-1]
    assert "limite de 1 acoes" in segunda.content


def test_limite_de_chamadas_ao_modelo_escala(ambiente):
    modelo = ModeloRoteirizado(roteiro=[consultar() for _ in range(10)])
    agente = Agente(modelo, ambiente, max_chamadas_modelo=3)
    ciclo = rodar(agente.iniciar(incidente()))
    assert ciclo.situacao == "limite"
    assert ciclo.desfecho.status == "escalado"
    assert modelo.chamadas == 3


# 4. Conferencia do desfecho


def test_resolvido_sem_verificacao_e_rebaixado(ambiente):
    roteiro = [agir("reverter_deploy"), concluir("resolvido")]
    agente = criar_agente(ambiente, roteiro)
    ciclo = rodar(agente.iniciar(incidente()))
    assert ciclo.desfecho.status == "escalado"
    assert "sem verificacao" in ciclo.desfecho.resumo


def test_resolvido_sem_acao_e_rebaixado():
    desfecho = Desfecho(status="resolvido", resumo="sumiu sozinho")
    assert validar_desfecho(desfecho, []).status == "escalado"


@pytest.mark.parametrize("status", ["escalado", "sem_acao"])
def test_outros_status_passam_sem_conferencia(status):
    desfecho = Desfecho(status=status, resumo="r")
    assert validar_desfecho(desfecho, []) == desfecho


# 5. Reentrada: o mesmo thread continua com o historico


def test_reentrada_continua_o_thread_depois_de_melhora_temporaria(ambiente):
    roteiro = [
        # ciclo 1: reiniciar parece resolver
        agir("reiniciar_servico"),
        consultar(),
        concluir("resolvido", "reinicio aliviou o pool"),
        # ciclo 2: o alerta voltou; reverter resolve de verdade
        consultar(),
        agir("reverter_deploy"),
        consultar(),
        concluir("resolvido", "deploy revertido"),
    ]
    agente = criar_agente(ambiente, roteiro)
    inc = incidente()

    primeiro = rodar(agente.iniciar(inc))
    assert primeiro.desfecho.status == "resolvido"

    segundo = rodar(agente.reentrar(inc, incidente(), ciclo=2))

    assert segundo.desfecho.resumo == "deploy revertido"
    historico = rodar(mensagens(agente, inc))
    assert any("disparou de novo (ciclo 2)" in str(m.content) for m in historico)
    metricas = [
        m for m in historico if getattr(m, "name", None) == "consultar_metricas"
    ]
    assert "18.1" in metricas[1].content  # recaiu antes do segundo ciclo agir
    # Eventos do segundo ciclo nao repetem os do primeiro.
    assert [e.tipo for e in segundo.eventos].count("acao") == 1


def test_limite_de_acoes_soma_os_ciclos(ambiente):
    roteiro = [
        agir("reiniciar_servico"),
        consultar(),
        concluir("resolvido"),
        agir("reverter_deploy"),
        concluir("escalado"),
    ]
    agente = criar_agente(ambiente, roteiro, max_acoes=1)
    inc = incidente()
    rodar(agente.iniciar(inc))
    rodar(agente.reentrar(inc, incidente(), ciclo=2))
    assert ambiente.consultar_estado(SERVICO)["versao"] == "v2.14.0"
