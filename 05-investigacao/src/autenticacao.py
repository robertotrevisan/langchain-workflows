"""Autenticacao dos endpoints por token compartilhado.

Aceita `Authorization: Bearer <token>` (Alertmanager: `http_config.authorization`)
ou Basic auth com o token como senha (SNS: credenciais na URL da inscricao,
`https://usuario:<token>@host/webhooks/cloudwatch`). Sem WEBHOOK_TOKEN configurado,
nao ha autenticacao: use somente em ambiente local.
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


def verificar_token(request: Request) -> None:
    esperado: str | None = request.app.state.token
    if esperado is None:
        return
    recebido = _token_recebido(request.headers.get("authorization", ""))
    if not hmac.compare_digest(recebido.encode(), esperado.encode()):
        raise HTTPException(
            401, "token invalido ou ausente", headers={"WWW-Authenticate": "Bearer"}
        )
