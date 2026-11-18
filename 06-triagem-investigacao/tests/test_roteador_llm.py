"""Avaliacao do roteador com o modelo real. Custa chamadas de API, por isso so roda
com RODAR_LLM=1 e a chave do provider configurada:

    RODAR_LLM=1 uv run pytest tests/test_roteador_llm.py -v

Os destinos esperados sao o comportamento desejado, nao uma garantia: uma
falha aqui pede revisao do prompt do roteador ou do cenario.
"""

import asyncio
import os

import pytest
from conftest import ler_entrada

from contratos import EventoGenerico, WebhookAlertmanager
from webhooks import de_alertmanager, de_generico

pytestmark = pytest.mark.skipif(
    os.getenv("RODAR_LLM") != "1", reason="defina RODAR_LLM=1 para chamar o modelo"
)

CASOS = [
    (
        "checkout com 18% de 5xx",
        lambda: de_alertmanager(
            WebhookAlertmanager.model_validate(ler_entrada("alertmanager.json"))
        )[0],
        0,
        "investigar",
    ),
    (
        "watchdog",
        lambda: de_alertmanager(
            WebhookAlertmanager.model_validate(
                ler_entrada("alertmanager-watchdog.json")
            )
        )[0],
        0,
        "ruido",
    ),
    (
        "lentidao geral sem servico",
        lambda: de_generico(
            EventoGenerico.model_validate(ler_entrada("generico-ambiguo.json"))
        ),
        0,
        "humano",
    ),
]


@pytest.fixture(scope="module")
def roteador():
    from config import carregar_config
    from triagem import (
        SYSTEM_ROTEADOR,  # noqa: F401  (garante o mesmo modulo)
        Triagem,
    )
    from workflow import criar_modelo

    config = carregar_config()
    modelo = criar_modelo(config, config.modelo_roteador)
    return Triagem(modelo, modelo, investigacao=None, catalogo=[]).roteador


@pytest.mark.parametrize(
    ("nome", "criar", "disparos", "esperado"), CASOS, ids=[c[0] for c in CASOS]
)
def test_destino_esperado(roteador, nome, criar, disparos, esperado):
    from workflow import descrever_incidente

    decisao = asyncio.run(
        roteador.ainvoke(
            {
                "incidente": descrever_incidente(criar()),
                "disparos_anteriores": disparos,
            }
        )
    )
    print(f"\n{nome}: {decisao.model_dump()}")
    assert decisao.destino == esperado
