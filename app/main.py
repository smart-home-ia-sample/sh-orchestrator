import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.registration import register_with_bfa
from app.routes import router
from smart_home_common import AuthTokenMiddleware, configure_logging, get_logger, run_registration_heartbeat

configure_logging(service="orchestrator", level=os.environ.get("LOG_LEVEL", "INFO"))
logger = get_logger(__name__)

DEFAULT_PORT = 8500


@asynccontextmanager
async def lifespan(app: FastAPI):
    bfa_url = os.environ["BFA_URL"]
    port = int(os.environ.get("PORT", str(DEFAULT_PORT)))

    register_with_bfa(bfa_url, port=port)
    logger.info("registered with BFA")

    heartbeat_task = asyncio.create_task(
        run_registration_heartbeat(
            lambda: register_with_bfa(bfa_url, port=port, max_attempts=1),
            interval_seconds=float(os.environ.get("REGISTRATION_HEARTBEAT_SECONDS", "15")),
        )
    )
    try:
        yield
    finally:
        heartbeat_task.cancel()


app = FastAPI(title="Smart Home AI - Orchestrator", lifespan=lifespan)
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
