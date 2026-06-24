# Contributing

Thanks for your interest in agentic-firmenbuch! This is a `uv` workspace (Python 3.12).

## Setup

```bash
uv sync --all-packages        # install the workspace + dev deps
```

Copy `.env.example` to `.env` and fill in what you need (everything is optional for the
unit tests, which run fully on the fixtures in `tests/fixtures/`).

## The checks (these must pass — they are the CI gate)

```bash
uv run ruff check packages tests/e2e        # lint
uv run ruff format --check packages tests/e2e   # formatting
uv run mypy packages                        # types (strict)
uv run pytest --cov --cov-report=term-missing --cov-fail-under=80   # tests + coverage
```

`uv run ruff format packages tests/e2e` auto-fixes formatting.

## Conventions

- **One responsibility per module**, `snake_case`, named for what it does.
- **A `README.md` in every package**, inter-navigable (link up to the root and across to
  neighbouring pipeline stages). The root README is the master index in pipeline order 90→10.
- **Lineage:** every produced document carries the `_meta` block (uuid, content hash, lineage
  chain, timestamps) — see the Technische Spezifikation §7.
- **Tests for edge cases:** the pipeline has a lot of them (legacy/semantic XML variants,
  unknown codes, partial birth dates, Bilanz-only filings, Rumpfwirtschaftsjahr, …). Add a test
  with any behaviour change.
- **No secrets in the repo.** `.env` and `.env.*` are git-ignored (templates `*.example` are
  tracked). Never commit keys, connection strings, or tokens.

## Architecture

Start with the [root README](README.md) and the specs in [`docs/`](docs/): the Technische
Spezifikation (the HOW) and the Fachliche Spezifikation (the WHAT/WHY). The pipeline is a
deterministic ETL (`ingest → parse → consolidate → derive → present`) over the Austrian
Firmenbuch HVD, served through a multi-tenant MCP server.

## Pull requests

Keep PRs focused; run the full check suite locally first; describe the change and link any
relevant spec section. Be kind and constructive in reviews.
