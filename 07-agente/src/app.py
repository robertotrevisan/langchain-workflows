import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from functools import partial

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from pydantic import ValidationError

from agente import Agente, Ciclo, criar_modelo
from ambiente import AmbienteSimulado
from autenticacao import (
    verificar_token,
    verificar_token_aprovacao,
    verificar_token_leitura,
)
from config import carregar_config
from contratos import (
    EventoGenerico,
    Incidente,
    ItemRecebido,
    PedidoAprovacao,
    RegistroIncidente,
    RespostaWebhook,
    WebhookAlertmanager,
)
from registro import Registro, TransicaoInvalida
from webhooks import de_alertmanager, de_cloudwatch, de_generico, ler_sns

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("aplicacao")


def criar_app(
    agente: Agente | None = None,
    token: str | None = None,
    registro: Registro | None = None,
    token_aprovacao: str | None = None,
) -> FastAPI:
    """Sem agente, monta tudo a partir do ambiente na inicializacao. Os testes
    injetam um agente com modelo falso e, opcionalmente, token e registro."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if agente is None:
            config = carregar_config()
            app.state.agente = Agente(
                criar_modelo(config),
                AmbienteSimulado(config.cenarios_dir),
                janela=config.janela,
                max_chamadas_modelo=config.max_chamadas_modelo,
                max_acoes=config.max_acoes,
                acoes_com_aprovacao=config.acoes_com_aprovacao,
            )
            app.state.registro = Registro(
                config.max_ciclos,
                config.janela_supressao,
                config.carencia_reentrada,
            )
            app.state.token = config.webhook_token
            app.state.token_aprovacao = config.aprovacao_token
            logger.info(
                "modelo=%s aprovacao=%s max_acoes=%d max_ciclos=%d",
                config.modelo,
                list(config.acoes_com_aprovacao),
                config.max_acoes,
                config.max_ciclos,
            )
        else:
            app.state.agente = agente
            app.state.registro = registro or Registro()
            app.state.token = token
            app.state.token_aprovacao = token_aprovacao
        if app.state.token is None:
            logger.warning("WEBHOOK_TOKEN ausente: endpoints sem autenticacao")
        elif app.state.token_aprovacao is None:
            logger.warning("APROVACAO_TOKEN ausente: aprovacao de acoes desligada")
        yield

    app = FastAPI(
        title="Agente de AIOps",
        description=(
            "Recebe alertas por webhook, investiga, age em um ambiente simulado e "
            "verifica a resolucao, com limites e aprovacao humana."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    async def executar(
        id: str, etapa: str, ciclo_do_agente: Callable[[], Awaitable[Ciclo]]
    ) -> None:
        """Roda um ciclo do agente em background e registra o resultado."""
        registro: Registro = app.state.registro
        logger.info("incidente=%s %s iniciado", id, etapa)
        try:
            ciclo = await ciclo_do_agente()
        except Exception as erro:  # o background nao tem para quem propagar
            logger.exception("incidente=%s %s falhou", id, etapa)
            # Detalhes ficam no log; a API expoe so o tipo do erro.
            registro.falhar(id, type(erro).__name__)
            return
        registro.registrar_ciclo(id, ciclo)
        logger.info(
            "incidente=%s %s terminou: %s %s",
            id,
            etapa,
            ciclo.situacao,
            ciclo.desfecho.status if ciclo.desfecho else "",
        )

    def receber(
        incidentes: list[Incidente], tarefas: BackgroundTasks
    ) -> RespostaWebhook:
        registro: Registro = app.state.registro
        agente: Agente = app.state.agente
        itens = []
        for incidente in incidentes:
            item = registro.receber(incidente)
            logger.info(
                "fingerprint=%s origem=%s status=%s acao=%s id=%s",
                incidente.fingerprint,
                incidente.origem,
                incidente.status,
                item.acao,
                item.id,
            )
            if item.acao == "processando":
                tarefas.add_task(
                    executar,
                    incidente.id,
                    "ciclo 1",
                    partial(agente.iniciar, incidente),
                )
            elif item.acao == "reentrada":
                aberto = registro.obter(item.id)
                tarefas.add_task(
                    executar,
                    item.id,
                    f"ciclo {aberto.ciclos}",
                    partial(
                        agente.reentrar, aberto.incidente, incidente, aberto.ciclos
                    ),
                )
            itens.append(item)
        return RespostaWebhook(recebidos=itens)

    @app.post(
        "/webhooks/alertmanager",
        status_code=202,
        dependencies=[Depends(verificar_token)],
    )
    async def webhook_alertmanager(
        payload: WebhookAlertmanager, tarefas: BackgroundTasks
    ) -> RespostaWebhook:
        return receber(de_alertmanager(payload), tarefas)

    @app.post(
        "/webhooks/cloudwatch",
        status_code=202,
        dependencies=[Depends(verificar_token)],
    )
    async def webhook_cloudwatch(
        request: Request, tarefas: BackgroundTasks
    ) -> RespostaWebhook:
        # O SNS envia Content-Type text/plain, entao o corpo e lido cru.
        try:
            mensagem = ler_sns(await request.body())
        except ValidationError as erro:
            raise HTTPException(422, f"mensagem SNS invalida: {erro}") from erro

        if mensagem.Type == "SubscriptionConfirmation":
            # Ambiente ficticio: registra a URL em vez de acessa-la.
            logger.warning(
                "SNS pediu confirmacao de inscricao: %s", mensagem.SubscribeURL
            )
            return RespostaWebhook(recebidos=[])
        if mensagem.Type == "UnsubscribeConfirmation":
            return RespostaWebhook(recebidos=[])

        try:
            incidente = de_cloudwatch(mensagem)
        except (ValidationError, ValueError) as erro:
            raise HTTPException(422, f"alarme CloudWatch invalido: {erro}") from erro
        if incidente is None:
            # INSUFFICIENT_DATA: sem incidente; o MessageId identifica o evento.
            item = ItemRecebido(
                id=None, fingerprint=mensagem.MessageId, acao="ignorado"
            )
            return RespostaWebhook(recebidos=[item])
        return receber([incidente], tarefas)

    @app.post(
        "/webhooks/generico",
        status_code=202,
        dependencies=[Depends(verificar_token)],
    )
    async def webhook_generico(
        evento: EventoGenerico, tarefas: BackgroundTasks
    ) -> RespostaWebhook:
        return receber([de_generico(evento)], tarefas)

    @app.get("/incidentes/{id}", dependencies=[Depends(verificar_token_leitura)])
    async def obter_incidente(id: str) -> RegistroIncidente:
        registro = app.state.registro.obter(id)
        if registro is None:
            raise HTTPException(404, "incidente nao encontrado")
        return registro

    @app.post(
        "/incidentes/{id}/aprovacao",
        status_code=202,
        dependencies=[Depends(verificar_token_aprovacao)],
    )
    async def aprovar(
        id: str, pedido: PedidoAprovacao, tarefas: BackgroundTasks
    ) -> RegistroIncidente:
        registro: Registro = app.state.registro
        aprovar = pedido.decisao == "aprovar"
        try:
            pendentes = registro.iniciar_decisao(
                id, aprovar, pedido.motivo, pedido.aprovador
            )
        except KeyError as erro:
            raise HTTPException(404, "incidente nao encontrado") from erro
        except TransicaoInvalida as erro:
            raise HTTPException(409, str(erro)) from erro
        incidente = registro.obter(id).incidente
        tarefas.add_task(
            executar,
            id,
            pedido.decisao,
            partial(
                app.state.agente.decidir,
                incidente,
                pendentes,
                aprovar,
                pedido.motivo,
            ),
        )
        return registro.obter(id)

    return app


app = criar_app()
