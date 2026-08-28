import os

os.environ.setdefault("BFA_URL", "http://bfa:8000")
os.environ.setdefault("LLM_PROVIDER", "mock")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


def test_health_and_ready():
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200


def test_unrecognized_message_returns_explicit_error():
    response = client.post("/converse", json={"message": "isso não quer dizer nada"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "error"
    assert "não entendi" in body["final_response"].lower()


def test_greeting_returns_ok_without_touching_agents():
    response = client.post("/converse", json={"message": "Olá!"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["final_response"]


def test_status_request_degrades_explicitly_when_bfa_unreachable():
    # BFA_URL points at a non-existent host in this test module; a status
    # request must degrade gracefully (recovery_explain), never 500.
    response = client.post("/converse", json={"message": "o que está acontecendo na casa?"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "error"
    assert body["final_response"]  # an explicit message, not a stack trace
