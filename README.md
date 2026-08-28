# sh-orchestrator

LangGraph orchestrator: interprets a command, discovers agents via the BFA, dispatches A2A tasks, validates the result, streams AG-UI.

Part of the **Smart Home AI** system — architecture, the full `docker compose`
stack and the end-to-end tests live in `sh-infra`.

## Run the tests
```
pip install -r requirements-dev.txt   # pulls sh-common from its public GitHub tag
pytest
```

## Build the image
```
docker build -t sh-orchestrator .     # sh-common resolved from GitHub (public)
```

`--build-arg SH_COMMON_SOURCE=local --build-context sh_common=../sh-common`
builds against a sibling checkout instead (offline / coordinated changes).

## CI

| Workflow | Runs |
| --- | --- |
| `test` | `pytest` with coverage on every PR / `main` push; fails below the `fail_under` in `pyproject.toml`, posts a coverage summary on the PR (check `test / coverage`) |
| `codeql` | CodeQL analysis (Python) on PRs, `main`, and weekly |
| `ci` | builds the Docker image on every PR; on `main` also pushes `ghcr.io/<owner>/sh-orchestrator:latest` + `:<sha>` |
