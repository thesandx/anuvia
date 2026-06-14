from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.core.config import settings
from app.core.logging import setup_logging
from app.core.router_loader import auto_register_routers
from app.schemas.common import HealthResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    yield


app = FastAPI(
    title=settings.APP_NAME,
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

auto_register_routers(app)


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/health")


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health():
    return HealthResponse(status="ok", app=settings.APP_NAME)
