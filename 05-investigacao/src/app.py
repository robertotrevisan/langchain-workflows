import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from pydantic import ValidationError

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
from webhooks import de_alertmanager, de_cloudwatch, de_generico, ler_sns
from workflow import Investigacao, criar_modelo

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("investigacao")


def criar_app(investigacao: Investigacao | None = None) -> FastAPI:
    """Sem argumento, monta a investigacao a partir do ambiente na inicializacao.
    Os testes injetam uma investigacao com modelo falso."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if investigacao is None:
            config = carregar_config()
            app.state.investigacao = Investigacao(
                criar_modelo(config), FonteCenarios(config.cenarios_dir), config.janela
            )
            logger.info("modelo=%s cenarios=%s", config.modelo, config.cenarios_dir)
        else:
            app.state.investigacao = investigacao
        app.state.registro = Registro()
        yield

    app = FastAPI(
        title="Investigacao de Incidentes",
        description=(
            "Recebe alertas por webhook, consulta fontes de dados e devolve um "
            "diagnostico consolidado. Nao executa acoes."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    async def investigar(incidente: Incidente) -> None:
        registro: Registro = app.state.registro
        logger.info("incidente=%s investigacao iniciada", incidente.id)
        try:
            diagnostico = await app.state.investigacao.executar(incidente)
        except Exception as erro:  # o background nao tem para quem propagar
            logger.exception("incidente=%s investigacao falhou", incidente.id)
            registro.falhar(incidente.id, f"{type(erro).__name__}: {erro}")
            return
        registro.concluir(incidente.id, diagnostico)
        logger.info("incidente=%s investigacao %s", incidente.id, diagnostico.status)

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
                tarefas.add_task(investigar, incidente)
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
