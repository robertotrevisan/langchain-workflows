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
    timeout: float
    max_retries: int
    janela: str
    cenarios_dir: Path


def carregar_config() -> Config:
    load_dotenv(RAIZ / ".env")

    modelo = os.getenv("MODELO", "openai:gpt-5.6-luna")
    provider = modelo.split(":", 1)[0] if ":" in modelo else None
    if provider not in CHAVE_POR_PROVIDER:
        raise ConfiguracaoInvalida(
            f"MODELO deve ter o formato provider:modelo, com provider em "
            f"{sorted(CHAVE_POR_PROVIDER)}. Recebido: {modelo!r}"
        )
    chave = CHAVE_POR_PROVIDER[provider]
    if not os.getenv(chave):
        raise ConfiguracaoInvalida(f"Configure {chave} no .env para usar {modelo}.")

    cenarios_dir = Path(os.getenv("CENARIOS_DIR", RAIZ / "cenarios"))
    if not cenarios_dir.is_dir():
        raise ConfiguracaoInvalida(f"CENARIOS_DIR nao existe: {cenarios_dir}")

    return Config(
        modelo=modelo,
        timeout=float(os.getenv("MODELO_TIMEOUT", "30")),
        max_retries=int(os.getenv("MODELO_MAX_RETRIES", "2")),
        janela=os.getenv("JANELA_INVESTIGACAO", "30m"),
        cenarios_dir=cenarios_dir,
    )
