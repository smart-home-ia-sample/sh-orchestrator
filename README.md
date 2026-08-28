# sh-orchestrator

LangGraph orchestrator: interprets a command, discovers agents via the BFA, dispatches A2A tasks, validates the result, streams AG-UI.

Part of the **Smart Home AI** system — architecture, the full `docker compose`
stack and the end-to-end tests live in `sh-infra`.

## Run the tests
```
pip install -r requirements-dev.txt   # needs sh-common from the registry
pytest
```

## Build the image
```
docker build -t sh-orchestrator .
```
