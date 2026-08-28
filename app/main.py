import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes import router
from smart_home_common import AuthTokenMiddleware, configure_logging

configure_logging(service="orchestrator", level=os.environ.get("LOG_LEVEL", "INFO"))

# No self-registration: the orchestrator is the BFF's fixed conversation entry
# point, reached at ORCHESTRATOR_URL — nothing resolves it via the BFA (spec/13).
app = FastAPI(title="Smart Home AI - Orchestrator")
# Lift the user's forwarded JWT into a contextvar so call_agent / HomeMcpClient
# forward it on down the chain.
app.add_middleware(AuthTokenMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
