import httpx

from smart_home_common.registration_client import ServiceInfo, register_with_retry

CAPABILITIES = ["converse"]
CATALOG = [
    {
        "id": "converse",
        "name": "Converse",
        "description": "Interprets a natural-language request and orchestrates the home agents to fulfil it",
        "tags": ["assistant", "natural language", "orchestrate"],
        "examples": ["apague as luzes da sala", "estou indo dormir", "o que está acontecendo na casa?"],
    }
]


def register_with_bfa(bfa_url: str, port: int, version: str = "0.1.0", max_attempts: int = 10) -> dict:
    service = ServiceInfo(
        name="orchestrator", port=port, capabilities=CAPABILITIES, protocol="http", version=version, catalog=CATALOG
    )
    with httpx.Client(timeout=5.0) as client:
        return register_with_retry(client, bfa_url, service, kind="agents", max_attempts=max_attempts)
