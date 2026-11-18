"""Catalogo de runbooks. A recomendacao so pode escolher um id deste catalogo."""

from pathlib import Path

from pydantic import TypeAdapter

from contratos import Runbook


def carregar_catalogo(arquivo: Path) -> list[Runbook]:
    catalogo = TypeAdapter(list[Runbook]).validate_json(arquivo.read_bytes())
    ids = [r.id for r in catalogo]
    if len(ids) != len(set(ids)):
        raise ValueError(f"ids de runbook repetidos em {arquivo}")
    return catalogo
