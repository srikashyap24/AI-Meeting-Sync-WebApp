import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .db import init_db
from .routers import auth, calendars, teams

logging.basicConfig(level=logging.INFO)
# httpx logs full request URLs at INFO; private ICS feed URLs embed secret tokens, so keep them out of logs.
logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="MeetSync", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[s.frontend_url.rstrip("/")],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.include_router(auth.router)
    app.include_router(teams.router)
    app.include_router(calendars.router)

    @app.get("/api/health")
    def health():
        return {"ok": True}

    return app


app = create_app()
