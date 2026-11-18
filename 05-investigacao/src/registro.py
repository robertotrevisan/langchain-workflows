"""Registro de incidentes em memoria, com deduplicacao por fingerprint.

Vive no processo: reiniciar o app perde o historico, e varias replicas nao
compartilham estado. Para producao, troque por Redis ou banco mantendo a interface.
"""

from datetime import UTC, datetime

from contratos import Diagnostico, Incidente, ItemRecebido, RegistroIncidente


def _agora() -> datetime:
    return datetime.now(UTC)


class Registro:
    def __init__(self):
        self._por_id: dict[str, RegistroIncidente] = {}
        # origem:fingerprint -> id do incidente ainda aberto (nao resolvido, sem falha)
        self._abertos: dict[str, str] = {}

    def obter(self, id: str) -> RegistroIncidente | None:
        return self._por_id.get(id)

    @staticmethod
    def _chave(incidente: Incidente) -> str:
        # Por origem: uma origem nunca fecha nem bloqueia incidente de outra.
        return f"{incidente.origem}:{incidente.fingerprint}"

    def receber(self, incidente: Incidente) -> ItemRecebido:
        """Decide o que fazer com o evento. Somente a acao "processando" exige
        disparar a investigacao."""
        chave = self._chave(incidente)
        aberto = self._abertos.get(chave)

        if incidente.status == "resolved":
            if aberto is None:
                return ItemRecebido(
                    id=None, fingerprint=incidente.fingerprint, acao="ignorado"
                )
            registro = self._por_id[aberto]
            registro.processamento = "resolvido"
            registro.atualizado_em = _agora()
            del self._abertos[chave]
            return ItemRecebido(
                id=aberto, fingerprint=incidente.fingerprint, acao="resolvido"
            )

        if aberto is not None:
            # Reenvio da origem (ex.: repeat_interval do Alertmanager).
            return ItemRecebido(
                id=aberto, fingerprint=incidente.fingerprint, acao="duplicado"
            )

        agora = _agora()
        self._por_id[incidente.id] = RegistroIncidente(
            incidente=incidente,
            processamento="processando",
            recebido_em=agora,
            atualizado_em=agora,
        )
        self._abertos[chave] = incidente.id
        return ItemRecebido(
            id=incidente.id, fingerprint=incidente.fingerprint, acao="processando"
        )

    def concluir(self, id: str, diagnostico: Diagnostico) -> None:
        registro = self._por_id[id]
        registro.diagnostico = diagnostico
        # Se o alerta resolveu durante a investigacao, o status "resolvido" vale.
        if registro.processamento == "processando":
            registro.processamento = "concluido"
        registro.atualizado_em = _agora()

    def falhar(self, id: str, erro: str) -> None:
        registro = self._por_id[id]
        registro.erro = erro
        if registro.processamento == "processando":
            registro.processamento = "falha"
        registro.atualizado_em = _agora()
        # Libera o fingerprint: um novo disparo tenta investigar de novo.
        chave = self._chave(registro.incidente)
        if self._abertos.get(chave) == id:
            del self._abertos[chave]
