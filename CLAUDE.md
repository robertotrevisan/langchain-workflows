# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## O que e este repo

Quatro exemplos de workflows LangChain para AIOps (`01-workflow-sequencial`, `02-outputs-contratos`, `03-routing-workflow`, `04-parallel-workflow`). Eles nasceram como material didatico e agora servem de base para boilerplates de projetos reais. Comparativo, casos de uso e lacunas para producao: @docs/padroes-workflow.md

Os exemplos 01 a 04 ficam **didaticos e intactos**. A evolucao para uso real acontece nos modelos novos 05, 06 e 07 (webhook, fontes ficticias, agente), conforme `docs/adrs/001-evolucao-para-agente-aiops.md` e a secao "Roadmap" de `docs/padroes-workflow.md`. Decisoes duraveis viram ADR em `docs/adrs/` (`NNN-slug.md`), e ADR `aceito` nao se edita: revisar uma decisao significa criar um ADR novo que o supersede. Ao mudar o comportamento de um projeto, atualize o `README.md` dele e a tabela de lacunas em `docs/padroes-workflow.md`.

## Estrutura e comandos

- Nao ha manifest na raiz. Cada pasta numerada e um projeto `uv` independente, com `pyproject.toml`, `uv.lock` e `.env` proprios. Rode todos os comandos **de dentro da pasta do projeto**.
- Setup: `cp .env.example .env && uv sync`. As chaves variam por projeto: `GOOGLE_API_KEY` em 01/02, `OPENAI_API_KEY` + `ANTHROPIC_API_KEY` em 03, `OPENAI_API_KEY` em 04.
- 01 e um script: `uv run src/app.py`. Ele le `alerta.txt` pelo cwd.
- 02 a 05 sao APIs: `uv run fastapi dev src/app.py`, depois `curl --data @entrada.json ...` (02) ou `--data @entradas/<arquivo>.json` (03 a 05). Os endpoints estao nos READMEs.
- 05: o webhook do CloudWatch le o corpo cru, porque o SNS envia `Content-Type: text/plain`. A chave depende de `MODELO` (`provider:modelo`); o default e `openai:gpt-5.6-luna`.
- Os imports dentro de `src/` sao planos (`from contratos import ...`, `from workflow import ...`). Nao converta para pacote/imports relativos sem ajustar a forma de execucao.
- Lint/format: `uv run ruff check src` e `uv run ruff format src` (config em `[tool.ruff]` de cada `pyproject.toml`).
- Testes so existem a partir do 05: `uv run pytest` (com `pythonpath = ["src"]`). Eles usam `ModeloFalso` (`tests/conftest.py`) e nunca chamam API de LLM; mantenha assim. O app e testado via `criar_app(investigacao)`, com modelo injetado.
- 06: `RODAR_LLM=1 uv run pytest tests/test_roteador_llm.py -v` avalia o roteador com o modelo real (custa chamadas de API). Rode depois de mudar `SYSTEM_ROTEADOR` ou `MODELO_ROTEADOR`.
- 05 e 06 compartilham codigo por copia (ADR 001): uma correcao em `webhooks.py`, `fontes.py`, `autenticacao.py`, `workflow.py` ou `registro.py` deve ser replicada no outro projeto.
- A partir do 05, nenhum modulo de `src/` pode ter efeito colateral no import (ler env, instanciar modelo). A configuracao e lida no `lifespan`.
- Em 01, `import time` sem uso e intencional: o bloco `RunnableSequence` do README, que substitui o final de `app.py`, usa esse import. Nao remova.

## Convencoes

- Separacao de 02 a 04, a seguir em projetos novos:
  - `app.py`: so o endpoint FastAPI, fino;
  - `contratos.py`: modelos Pydantic de entrada, intermediarios e saida;
  - `workflow.py`: secoes numeradas por comentario (modelo, system prompt e chain de cada etapa), terminando em `executar_workflow(entrada) -> Resultado`.
- Codigo, identificadores, prompts, comentarios e docs em **portugues sem acentos** (ASCII).
- Os IDs de modelo (`gemini-3.8-flash`, `gpt-5.6-luna`, `gpt-5.6-sol`, `claude-sonnet-5`, `claude-haiku-4-5-20251001`) sao intencionais. Nao "corrija" nem troque sem pedido.
- Em 03, `provider`/`modelo` aparecem duplicados: na construcao do modelo e no `Resultado` de cada `tratar_*`. Ao trocar um modelo, atualize os dois lugares e a tabela do README.
- Commits: Conventional Commits em portugues (`feat: adiciona ...`).
