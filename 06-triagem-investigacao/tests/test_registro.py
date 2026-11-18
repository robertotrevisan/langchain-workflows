from datetime import UTC, datetime

from contratos import Incidente
from registro import Registro


def evento(id: str, status: str = "firing", origem: str = "alertmanager") -> Incidente:
    return Incidente(
        id=id,
        fingerprint="fp",
        origem=origem,
        status=status,
        titulo="Alerta instavel",
        descricao="",
        inicio=datetime(2026, 11, 16, 14, 0, tzinfo=UTC),
    )


def test_conta_disparos_anteriores_do_mesmo_alerta():
    registro = Registro()
    for i in range(3):
        registro.receber(evento(f"i{i}"))
        registro.receber(evento(f"r{i}", status="resolved"))

    atual = evento("atual")
    registro.receber(atual)

    assert registro.disparos_anteriores(atual) == 3


def test_disparos_de_outra_origem_nao_contam():
    registro = Registro()
    registro.receber(evento("a", origem="generico"))
    registro.receber(evento("b", status="resolved", origem="generico"))

    atual = evento("atual")
    registro.receber(atual)

    assert registro.disparos_anteriores(atual) == 0
