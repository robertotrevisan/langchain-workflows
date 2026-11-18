"""Configuracao lida do ambiente na inicializacao do app, nunca no import."""

import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent

CHAVE_POR_PROVIDER = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google_genai": "GOOGLE_API_KEY",
}
ACOES_CONHECIDAS = ("reiniciar_servico", "escalar_replicas", "reverter_deploy")


class ConfiguracaoInvalida(Exception):
    pass


@dataclass(frozen=True)
class Config:
    modelo: str
    timeout: float
    max_retries: int
    janela: str
    cenarios_dir: Path
    webhook_token: str | None
    aprovacao_token: str | None
    max_chamadas_modelo: int
    max_acoes: int
    max_ciclos: int
    acoes_com_aprovacao: tuple[str, ...]
    janela_supressao: timedelta


def _inteiro_positivo(variavel: str, padrao: str) -> int:
    valor = int(os.getenv(variavel, padrao))
    if valor < 1:
        raise ConfiguracaoInvalida(f"{variavel} deve ser maior que zero: {valor}")
    return valor


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

    # Vazio desliga a aprovacao: o agente executa todas as acoes sozinho.
    brutas = os.getenv("ACOES_COM_APROVACAO", "reverter_deploy")
    acoes = tuple(a.strip() for a in brutas.split(",") if a.strip())
    desconhecidas = set(acoes) - set(ACOES_CONHECIDAS)
    if desconhecidas:
        raise ConfiguracaoInvalida(
            f"ACOES_COM_APROVACAO tem acoes desconhecidas: {sorted(desconhecidas)}. "
            f"Validas: {list(ACOES_CONHECIDAS)}"
        )

    webhook_token = os.getenv("WEBHOOK_TOKEN") or None
    aprovacao_token = os.getenv("APROVACAO_TOKEN") or None
    if aprovacao_token is not None and aprovacao_token == webhook_token:
        raise ConfiguracaoInvalida(
            "APROVACAO_TOKEN deve ser diferente de WEBHOOK_TOKEN"
        )

    return Config(
        modelo=modelo,
        timeout=float(os.getenv("MODELO_TIMEOUT", "60")),
        max_retries=int(os.getenv("MODELO_MAX_RETRIES", "2")),
        janela=os.getenv("JANELA_INVESTIGACAO", "30m"),
        cenarios_dir=cenarios_dir,
        webhook_token=webhook_token,
        aprovacao_token=aprovacao_token,
        max_chamadas_modelo=_inteiro_positivo("MAX_CHAMADAS_MODELO", "15"),
        max_acoes=_inteiro_positivo("MAX_ACOES", "3"),
        max_ciclos=_inteiro_positivo("MAX_CICLOS", "3"),
        acoes_com_aprovacao=acoes,
        janela_supressao=timedelta(
            minutes=_inteiro_positivo("JANELA_SUPRESSAO_MIN", "10")
        ),
    )
