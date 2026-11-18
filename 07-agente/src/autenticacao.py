"""Autenticacao dos endpoints por token compartilhado.

Aceita `Authorization: Bearer <token>` (Alertmanager: `http_config.authorization`)
ou Basic auth com o token como senha (SNS: credenciais na URL da inscricao,
`https://usuario:<token>@host/webhooks/cloudwatch`). Sem WEBHOOK_TOKEN configurado,
nao ha autenticacao: use somente em ambiente local.

A aprovacao de acoes usa outro token (APROVACAO_TOKEN), e o token do webhook nao
vale nela: quem configura a origem do alerta nao deve poder aprovar uma acao.
"""

import base64
import binascii
import hmac

from fastapi import HTTPException, Request


def _token_recebido(cabecalho: str) -> str:
    tipo, _, valor = cabecalho.partition(" ")
    if tipo.lower() == "bearer":
        return valor.strip()
    if tipo.lower() == "basic":
        try:
            credenciais = base64.b64decode(valor.strip(), validate=True).decode()
        except (binascii.Error, UnicodeDecodeError):
            return ""
        return credenciais.partition(":")[2]
    return ""


def _confere(request: Request, *esperados: str) -> bool:
    recebido = _token_recebido(request.headers.get("authorization", "")).encode()
    # Compara com todos, sem atalho, para nao revelar qual token existe.
    resultados = [hmac.compare_digest(recebido, e.encode()) for e in esperados]
    return any(resultados)


def _negar() -> HTTPException:
    return HTTPException(
        401, "token invalido ou ausente", headers={"WWW-Authenticate": "Bearer"}
    )


def verificar_token(request: Request) -> None:
    """Webhooks: somente o token do webhook."""
    esperado: str | None = request.app.state.token
    if esperado is not None and not _confere(request, esperado):
        raise _negar()


def verificar_token_leitura(request: Request) -> None:
    """Consulta de incidentes: o token do webhook ou o de aprovacao."""
    tokens = [
        t for t in (request.app.state.token, request.app.state.token_aprovacao) if t
    ]
    if tokens and not _confere(request, *tokens):
        raise _negar()


def verificar_token_aprovacao(request: Request) -> None:
    """Aprovacao: somente APROVACAO_TOKEN. Sem ele, so fica aberta no modo local
    (sem nenhum token); com WEBHOOK_TOKEN e sem APROVACAO_TOKEN, fica fechada."""
    esperado: str | None = request.app.state.token_aprovacao
    if esperado is None:
        if request.app.state.token is None:
            return
        raise HTTPException(503, "configure APROVACAO_TOKEN para aprovar acoes")
    if not _confere(request, esperado):
        raise _negar()
