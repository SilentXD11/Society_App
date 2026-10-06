from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db import dispose_engine, get_engine
from app.routers import assets, auth, money, society


@asynccontextmanager
async def lifespan(_: FastAPI):
    get_engine()
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="Society Register API", version="0.1.0", lifespan=lifespan,
                  docs_url="/docs" if s.env != "prod" else None)
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"], expose_headers=["X-Step-Up-Required"])
    for r in (auth.router, society.router, money.router, assets.router):
        app.include_router(r)

    @app.get("/health", tags=["ops"])
    async def health():
        return {"ok": True}

    return app


app = create_app()
