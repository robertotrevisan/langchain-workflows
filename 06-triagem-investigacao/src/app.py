import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from pydantic import ValidationError

from autenticacao import verificar_token
from config import carregar_config
from contratos import (
    EventoGenerico,
    Incidente,
    ItemRecebido,
    RegistroIncidente,
    RespostaWebhook,
    WebhookAlertmanager,
)
from fontes import FonteCenarios
from registro import Registro
from runbooks import carregar_catalogo
from triagem import Triagem
from webhooks import de_alertmanager, de_cloudwatch, de_generico, ler_sns
from workflow import Investigacao, criar_modelo

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("aplicacao")


def criar_app(triagem: Triagem | None = None, token: str | None = None) -> FastAPI:
    """Sem triagem, monta tudo a partir do ambiente na inicializacao.
    Os testes injetam uma triagem com modelo falso e, opcionalmente, o token."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if triagem is None:
            config = carregar_config()
            modelo = criar_modelo(config)
            investigacao = Investigacao(
                modelo, FonteCenarios(config.cenarios_dir), config.janela
            )
            app.state.triagem = Triagem(
                criar_modelo(config, config.modelo_roteador),
                modelo,
                investigacao,
                carregar_catalogo(config.catalogo_runbooks),
                config.limiar_confianca,
            )
            logger.info(
                "modelo=%s roteador=%s limiar=%.2f",
                config.modelo,
                config.modelo_roteador,
                config.limiar_confianca,
            )
            app.state.token = config.webhook_token
        else:
            app.state.triagem = triagem
            app.state.token = token
        if app.state.token is None:
            logger.warning("WEBHOOK_TOKEN ausente: endpoints sem autenticacao")
        app.state.registro = Registro()
        yield

    app = FastAPI(
        title="Triagem e Investigacao de Incidentes",
        description=(
            "Recebe alertas por webhook, decide se investiga, descarta como ruido "
            "ou escala para humano, e recomenda um runbook. Nao executa acoes."
        ),
        version="0.1.0",
        lifespan=lifespan,
        dependencies=[Depends(verificar_token)],
    )

    async def triar(incidente: Incidente) -> None:
        registro: Registro = app.state.registro
        logger.info("incidente=%s triagem iniciada", incidente.id)
        try:
            tratativa = await app.state.triagem.executar(
                incidente, registro.disparos_anteriores(incidente)
            )
        except Exception as erro:  # o background nao tem para quem propagar
            logger.exception("incidente=%s triagem falhou", incidente.id)
            # Detalhes ficam no log; a API expoe so o tipo do erro.
            registro.falhar(incidente.id, type(erro).__name__)
            return
        registro.concluir(incidente.id, tratativa)
        logger.info("incidente=%s rota=%s", incidente.id, tratativa.rota)

    def receber(
        incidentes: list[Incidente], tarefas: BackgroundTasks
    ) -> RespostaWebhook:
        registro: Registro = app.state.registro
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
                tarefas.add_task(triar, incidente)
            itens.append(item)
        return RespostaWebhook(recebidos=itens)

    @app.post("/webhooks/alertmanager", status_code=202)
    async def webhook_alertmanager(
        payload: WebhookAlertmanager, tarefas: BackgroundTasks
    ) -> RespostaWebhook:
        return receber(de_alertmanager(payload), tarefas)

    @app.post("/webhooks/cloudwatch", status_code=202)
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

    @app.post("/webhooks/generico", status_code=202)
    async def webhook_generico(
        evento: EventoGenerico, tarefas: BackgroundTasks
    ) -> RespostaWebhook:
        return receber([de_generico(evento)], tarefas)

    @app.get("/incidentes/{id}")
    async def obter_incidente(id: str) -> RegistroIncidente:
        registro = app.state.registro.obter(id)
        if registro is None:
            raise HTTPException(404, "incidente nao encontrado")
        return registro

    return app


app = criar_app()
