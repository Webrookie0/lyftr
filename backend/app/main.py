import logging
from contextlib import asynccontextmanager
from time import perf_counter
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import Settings
from app.database import Database
from app.gateway import Gateway, ProvidersUnavailable, parse_simulation
from app.schemas import ChatRequest, ChatResponse, HealthResponse, HistoryResponse, Metadata

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, gateway=None) -> FastAPI:
    settings = settings or Settings()
    database = Database(settings.database_path)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.gateway = gateway or Gateway(settings)
        await database.initialize()
        yield

    application = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    application.state.database = database
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Simulate-Failure"],
    )

    @application.exception_handler(Exception)
    async def safe_error(_request: Request, error: Exception):
        logger.error("Request failed: %s", type(error).__name__)
        return JSONResponse(status_code=500, content={"detail": "internal server error"})

    @application.get("/health", response_model=HealthResponse)
    async def health(request: Request):
        try:
            await database.reachable()
        except Exception as error:
            raise HTTPException(503, detail="database unavailable") from error
        return HealthResponse(
            keys_configured=len(request.app.state.gateway.keys), cache_enabled=True
        )

    @application.post("/chat", response_model=ChatResponse)
    async def chat(
        body: ChatRequest,
        request: Request,
        x_simulate_failure: Annotated[str | None, Header()] = None,
    ):
        start = perf_counter()
        try:
            failures = parse_simulation(
                x_simulate_failure if settings.enable_failure_simulation else None,
                len(request.app.state.gateway.keys),
            )
        except ValueError as error:
            raise HTTPException(422, detail=str(error)) from error
        async with database.session_lock(body.session_id):
            stored_prompt, history = await database.session(body.session_id)
            prompt = (
                body.system_prompt
                if body.system_prompt is not None
                else stored_prompt
                if stored_prompt is not None
                else settings.default_system_prompt
            )
            messages = [
                {"role": "system", "content": prompt},
                *[{"role": item.role, "content": item.content} for item in history],
                {"role": "user", "content": body.message},
            ]
            try:
                result = await request.app.state.gateway.resolve(messages, failures)
            except ProvidersUnavailable as error:
                raise HTTPException(503, detail="all providers unavailable") from error
            metadata = Metadata(
                resolution_source=result.source,
                model=result.model,
                latency_ms=int((perf_counter() - start) * 1000),
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                key_id=result.key_id,
                fallback_attempts=result.fallback_attempts,
            )
            await database.save_turn(body.session_id, prompt, body.message, result.reply, metadata)
        return ChatResponse(session_id=body.session_id, reply=result.reply, metadata=metadata)

    @application.get(
        "/sessions/{session_id}/messages",
        response_model=HistoryResponse,
        response_model_exclude_none=True,
    )
    async def history(session_id: str):
        async with database.session_lock(session_id):
            prompt, messages = await database.session(session_id)
        if prompt is None and not messages:
            raise HTTPException(404, detail="session not found")
        return HistoryResponse(session_id=session_id, messages=messages)

    return application


app = create_app()
