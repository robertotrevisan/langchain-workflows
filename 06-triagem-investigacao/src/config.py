"""Configuracao lida do ambiente na inicializacao do app, nunca no import."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent

CHAVE_POR_PROVIDER = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google_genai": "GOOGLE_API_KEY",
}


class ConfiguracaoInvalida(Exception):
    pass


@dataclass(frozen=True)
class Config:
    modelo: str
    modelo_roteador: str
    timeout: float
    max_retries: int
    janela: str
    limiar_confianca: float
    cenarios_dir: Path
    catalogo_runbooks: Path
    webhook_token: str | None


def _validar_modelo(variavel: str, modelo: str) -> None:
    provider = modelo.split(":", 1)[0] if ":" in modelo else None
    if provider not in CHAVE_POR_PROVIDER:
        raise ConfiguracaoInvalida(
            f"{variavel} deve ter o formato provider:modelo, com provider em "
            f"{sorted(CHAVE_POR_PROVIDER)}. Recebido: {modelo!r}"
        )
    chave = CHAVE_POR_PROVIDER[provider]
    if not os.getenv(chave):
        raise ConfiguracaoInvalida(f"Configure {chave} no .env para usar {modelo}.")


def carregar_config() -> Config:
    load_dotenv(RAIZ / ".env")

    modelo = os.getenv("MODELO", "openai:gpt-5.6-luna")
    _validar_modelo("MODELO", modelo)
    # O roteador faz uma chamada curta por alerta; pode usar um modelo mais barato.
    modelo_roteador = os.getenv("MODELO_ROTEADOR", modelo)
    _validar_modelo("MODELO_ROTEADOR", modelo_roteador)

    cenarios_dir = Path(os.getenv("CENARIOS_DIR", RAIZ / "cenarios"))
    if not cenarios_dir.is_dir():
        raise ConfiguracaoInvalida(f"CENARIOS_DIR nao existe: {cenarios_dir}")

    catalogo = Path(os.getenv("CATALOGO_RUNBOOKS", RAIZ / "runbooks/catalogo.json"))
    if not catalogo.is_file():
        raise ConfiguracaoInvalida(f"CATALOGO_RUNBOOKS nao existe: {catalogo}")

    limiar = float(os.getenv("LIMIAR_CONFIANCA", "0.7"))
    if not 0 <= limiar <= 1:
        raise ConfiguracaoInvalida(f"LIMIAR_CONFIANCA deve estar entre 0 e 1: {limiar}")

    return Config(
        modelo=modelo,
        modelo_roteador=modelo_roteador,
        timeout=float(os.getenv("MODELO_TIMEOUT", "30")),
        max_retries=int(os.getenv("MODELO_MAX_RETRIES", "2")),
        janela=os.getenv("JANELA_INVESTIGACAO", "30m"),
        limiar_confianca=limiar,
        cenarios_dir=cenarios_dir,
        catalogo_runbooks=catalogo,
        webhook_token=os.getenv("WEBHOOK_TOKEN") or None,
    )
