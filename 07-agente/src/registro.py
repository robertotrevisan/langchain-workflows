"""Registro de incidentes em memoria: deduplicacao, reentrada, supressao,
aprovacao e timeline.

Maquina de estados de `processamento`:

    processando --> aguardando_aprovacao --(decisao)--> processando
    processando --> aguardando_confirmacao   (agente concluiu resolvido)
                --> escalado | encerrado     (escalado | sem_acao)
                --> falha                    (erro; libera o fingerprint)
    aguardando_confirmacao --(firing na carencia)--> duplicado, sem mudar
                           --(novo firing)--> processando (reentrada)
                           --(firing apos max_ciclos)--> escalado
    qualquer aberto --(resolved da origem)--> resolvido

A carencia de reentrada absorve o que nao e recaida: depois de uma correcao, o
alerta leva alguns minutos para limpar na origem, e a origem pode reenviar o
mesmo alerta enquanto isso (Alertmanager: group_interval, repeat_interval).

O fingerprint fica aberto ate a origem mandar resolved ou o processamento
falhar. Vive no processo: reiniciar perde o historico, e varias replicas nao
compartilham estado. Para producao, troque por Redis ou banco.
"""

from datetime import UTC, datetime, timedelta

from agente import Ciclo
from contratos import (
    AcaoPendente,
    EventoTimeline,
    Incidente,
    ItemRecebido,
    RegistroIncidente,
    TipoEvento,
)


def _agora() -> datetime:
    return datetime.now(UTC)


class TransicaoInvalida(Exception):
    """A operacao nao vale no estado atual do incidente."""


class Registro:
    def __init__(
        self,
        max_ciclos: int = 3,
        janela_supressao: timedelta = timedelta(minutes=10),
        carencia_reentrada: timedelta = timedelta(minutes=5),
    ):
        self.max_ciclos = max_ciclos
        self.janela_supressao = janela_supressao
        self.carencia_reentrada = carencia_reentrada
        self._por_id: dict[str, RegistroIncidente] = {}
        # origem:fingerprint -> id do incidente aberto
        self._abertos: dict[str, str] = {}

    def obter(self, id: str) -> RegistroIncidente | None:
        return self._por_id.get(id)

    @staticmethod
    def _chave(incidente: Incidente) -> str:
        # Por origem: uma origem nunca fecha nem bloqueia incidente de outra.
        return f"{incidente.origem}:{incidente.fingerprint}"

    @staticmethod
    def _evento(
        registro: RegistroIncidente, tipo: TipoEvento, detalhe: str, **dados
    ) -> None:
        agora = _agora()
        registro.timeline.append(
            EventoTimeline(momento=agora, tipo=tipo, detalhe=detalhe, dados=dados)
        )
        registro.atualizado_em = agora

    def _causador(self, incidente: Incidente) -> RegistroIncidente | None:
        """Incidente aberto que agiu no mesmo servico dentro da janela de
        supressao: um alerta novo ali provavelmente e efeito da acao."""
        if incidente.servico is None:
            return None
        limite = _agora() - self.janela_supressao
        for id in self._abertos.values():
            registro = self._por_id[id]
            if registro.incidente.servico != incidente.servico:
                continue
            if any(e.tipo == "acao" and e.momento >= limite for e in registro.timeline):
                return registro
        return None

    def _em_carencia(self, registro: RegistroIncidente) -> bool:
        """O agente concluiu resolvido ha menos que a carencia."""
        concluido_em = max(
            (e.momento for e in registro.timeline if e.tipo == "desfecho"),
            default=None,
        )
        return (
            concluido_em is not None
            and _agora() - concluido_em < self.carencia_reentrada
        )

    def receber(self, incidente: Incidente) -> ItemRecebido:
        """Decide o que fazer com o evento. As acoes "processando" e
        "reentrada" exigem disparar o agente."""
        chave = self._chave(incidente)
        aberto_id = self._abertos.get(chave)
        aberto = self._por_id.get(aberto_id) if aberto_id else None

        def item(id: str | None, acao) -> ItemRecebido:
            return ItemRecebido(id=id, fingerprint=incidente.fingerprint, acao=acao)

        if incidente.status == "resolved":
            if aberto is None:
                return item(None, "ignorado")
            aberto.processamento = "resolvido"
            aberto.pendentes = []
            self._evento(aberto, "resolvido", "a origem informou resolved")
            del self._abertos[chave]
            return item(aberto.incidente.id, "resolvido")

        if aberto is not None:
            if aberto.processamento != "aguardando_confirmacao":
                # Reenvio da origem enquanto o agente trabalha ou aguarda humano.
                return item(aberto.incidente.id, "duplicado")
            if self._em_carencia(aberto):
                # Cedo demais para ser recaida: o alerta ainda nao limpou.
                return item(aberto.incidente.id, "duplicado")
            if aberto.ciclos >= self.max_ciclos:
                aberto.processamento = "escalado"
                self._evento(
                    aberto,
                    "limite",
                    f"alerta disparou de novo apos {aberto.ciclos} ciclos; escalado",
                )
                return item(aberto.incidente.id, "escalado")
            aberto.ciclos += 1
            aberto.processamento = "processando"
            self._evento(
                aberto,
                "reentrada",
                f"alerta disparou de novo; ciclo {aberto.ciclos}",
                inicio=incidente.inicio.isoformat(),
            )
            return item(aberto.incidente.id, "reentrada")

        causador = self._causador(incidente)
        if causador is not None:
            self._evento(
                causador,
                "suprimido",
                f"alerta {incidente.titulo!r} suprimido: provavel efeito de acao "
                f"recente em {incidente.servico}",
                fingerprint=incidente.fingerprint,
                origem=incidente.origem,
            )
            return item(causador.incidente.id, "suprimido")

        agora = _agora()
        registro = RegistroIncidente(
            incidente=incidente,
            processamento="processando",
            recebido_em=agora,
            atualizado_em=agora,
        )
        self._evento(registro, "recebido", f"{incidente.origem}: {incidente.titulo}")
        self._por_id[incidente.id] = registro
        self._abertos[chave] = incidente.id
        return item(incidente.id, "processando")

    def registrar_ciclo(self, id: str, ciclo: Ciclo) -> None:
        registro = self._por_id[id]
        registro.timeline.extend(ciclo.eventos)
        registro.atualizado_em = _agora()
        resolvido = registro.processamento == "resolvido"

        if ciclo.situacao == "aguardando_aprovacao":
            for pendente in ciclo.pendentes:
                self._evento(
                    registro,
                    "aprovacao_solicitada",
                    f"{pendente.ferramenta} aguarda aprovacao",
                    argumentos=pendente.argumentos,
                )
            if not resolvido:
                # Se a origem ja resolveu, a acao pendente fica sem efeito.
                registro.pendentes = ciclo.pendentes
                registro.processamento = "aguardando_aprovacao"
            return

        desfecho = ciclo.desfecho
        registro.desfecho = desfecho
        if ciclo.situacao == "limite":
            self._evento(registro, "limite", desfecho.resumo)
        self._evento(
            registro,
            "desfecho",
            f"{desfecho.status}: {desfecho.resumo}",
            status=desfecho.status,
        )
        if resolvido:
            return
        registro.processamento = {
            "resolvido": "aguardando_confirmacao",
            "escalado": "escalado",
            "sem_acao": "encerrado",
        }[desfecho.status]

    def iniciar_decisao(
        self, id: str, aprovar: bool, motivo: str | None, aprovador: str
    ) -> list[AcaoPendente]:
        """Valida e registra a decisao humana; devolve as acoes a retomar. A
        transicao acontece aqui, antes do background, para que duas decisoes
        simultaneas nao retomem o agente duas vezes."""
        registro = self._por_id.get(id)
        if registro is None:
            raise KeyError(id)
        if registro.processamento != "aguardando_aprovacao":
            raise TransicaoInvalida(
                f"incidente em {registro.processamento}, nao aguarda aprovacao"
            )
        pendentes = registro.pendentes
        registro.pendentes = []
        registro.processamento = "processando"
        nomes = ", ".join(p.ferramenta for p in pendentes)
        self._evento(
            registro,
            "aprovacao",
            f"{'aprovado' if aprovar else 'rejeitado'} por {aprovador}: {nomes}"
            + (f" ({motivo})" if motivo else ""),
            aprovado=aprovar,
            aprovador=aprovador,
        )
        return pendentes

    def falhar(self, id: str, erro: str) -> None:
        registro = self._por_id[id]
        registro.erro = erro
        self._evento(registro, "falha", erro)
        if registro.processamento != "resolvido":
            registro.processamento = "falha"
        # Libera o fingerprint: um novo disparo abre outro incidente.
        chave = self._chave(registro.incidente)
        if self._abertos.get(chave) == id:
            del self._abertos[chave]
