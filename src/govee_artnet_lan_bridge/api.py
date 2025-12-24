"""HTTP API server for device and mapping management."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import uvicorn

from .config import Config, ManualDevice
from .devices import DeviceStateUpdate, DeviceStore
from .logging import get_logger, redact_mapping
from .metrics import (
    METRICS_CONTENT_TYPE,
    latest_metrics,
    observe_request,
)


def _build_auth_dependency(config: Config) -> Callable[[Request], None]:
    async def _auth_guard(request: Request) -> None:
        if not config.api_key and not config.api_bearer_token:
            return
        api_key_header = request.headers.get("X-API-Key")
        auth_header = request.headers.get("Authorization")
        if config.api_key and api_key_header == config.api_key:
            return
        if config.api_key and auth_header and auth_header.lower().startswith("apikey "):
            if auth_header.split(" ", 1)[1] == config.api_key:
                return
        if config.api_bearer_token and auth_header and auth_header.startswith("Bearer "):
            if auth_header.split(" ", 1)[1] == config.api_bearer_token:
                return
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return _auth_guard


class DeviceCreate(BaseModel):
    """Payload for creating a manual device."""

    id: str
    ip: str
    model: Optional[str] = None
    description: Optional[str] = None
    capabilities: Optional[Any] = None
    enabled: bool = True


class DeviceUpdate(BaseModel):
    """Partial update payload for a device."""

    ip: Optional[str] = None
    model: Optional[str] = None
    description: Optional[str] = None
    capabilities: Optional[Any] = None
    enabled: Optional[bool] = None


class DeviceOut(BaseModel):
    """Device response model."""

    id: str
    ip: Optional[str]
    model: Optional[str]
    description: Optional[str]
    capabilities: Optional[Any]
    manual: bool
    discovered: bool
    configured: bool
    enabled: bool
    stale: bool
    offline: bool
    last_seen: Optional[str]
    first_seen: Optional[str]
    created_at: str
    updated_at: str


class MappingCreate(BaseModel):
    """Payload for creating a mapping."""

    device_id: str
    universe: int = Field(ge=0)
    channel: int = Field(gt=0)
    length: int = Field(gt=0)
    allow_overlap: bool = False


class MappingUpdate(BaseModel):
    """Partial update payload for a mapping."""

    device_id: Optional[str] = None
    universe: Optional[int] = Field(default=None, ge=0)
    channel: Optional[int] = Field(default=None, gt=0)
    length: Optional[int] = Field(default=None, gt=0)
    allow_overlap: bool = False


class MappingOut(BaseModel):
    """Mapping response model."""

    id: int
    device_id: str
    universe: int
    channel: int
    length: int
    created_at: str
    updated_at: str


class TestAction(BaseModel):
    """Test payload to enqueue to a device."""

    payload: Any


def create_app(config: Config, store: DeviceStore) -> FastAPI:
    """Create and configure a FastAPI application."""

    logger = get_logger("govee.api")
    request_logger = get_logger("govee.api.middleware")
    auth_dependency = _build_auth_dependency(config)
    app = FastAPI(
        title="Govee Artnet LAN Bridge API",
        docs_url="/docs" if config.api_docs else None,
        redoc_url="/redoc" if config.api_docs else None,
        openapi_url="/openapi.json" if config.api_docs else None,
    )

    @app.middleware("http")
    async def _logging_middleware(request: Request, call_next: Callable[..., Any]) -> JSONResponse:
        start = time.perf_counter()
        path_template = getattr(request.scope.get("route"), "path", request.url.path)
        redacted_headers = redact_mapping(dict(request.headers))
        try:
            response = await call_next(request)
        except HTTPException as exc:
            duration_seconds = time.perf_counter() - start
            observe_request(request.method, path_template, exc.status_code, duration_seconds)
            request_logger.warning(
                "API error",
                extra={
                    "method": request.method,
                    "path": path_template,
                    "status": exc.status_code,
                    "duration_ms": round(duration_seconds * 1000, 2),
                    "client": request.client.host if request.client else None,
                    "headers": redacted_headers,
                },
            )
            raise
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Unhandled API error")
            raise HTTPException(status_code=500, detail="Internal server error") from exc
        duration_seconds = time.perf_counter() - start
        observe_request(
            request.method,
            path_template,
            response.status_code,
            duration_seconds,
        )
        request_logger.info(
            "Handled request",
            extra={
                "method": request.method,
                "path": path_template,
                "status": response.status_code,
                "duration_ms": round(duration_seconds * 1000, 2),
                "client": request.client.host if request.client else None,
                "headers": redacted_headers,
            },
        )
        return response

    @app.exception_handler(HTTPException)
    async def _http_exc_handler(request: Request, exc: HTTPException) -> JSONResponse:
        request_logger.warning(
            "API error",
            extra={"path": request.url.path, "status": exc.status_code, "detail": exc.detail},
        )
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        request_logger.warning(
            "Validation error",
            extra={"path": request.url.path, "errors": exc.errors()},
        )
        return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content={"detail": exc.errors()})

    @app.get("/health", dependencies=[Depends(auth_dependency)])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/status", dependencies=[Depends(auth_dependency)])
    async def status() -> dict[str, int]:
        return dict(await store.stats())

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(content=latest_metrics(), media_type=METRICS_CONTENT_TYPE)

    @app.get("/devices", dependencies=[Depends(auth_dependency)], response_model=list[DeviceOut])
    async def list_devices() -> list[DeviceOut]:
        rows = await store.devices()
        return [DeviceOut(**row.__dict__) for row in rows]

    @app.post("/devices", dependencies=[Depends(auth_dependency)], response_model=DeviceOut, status_code=status.HTTP_201_CREATED)
    async def create_device(payload: DeviceCreate) -> DeviceOut:
        manual = ManualDevice(
            id=payload.id,
            ip=payload.ip,
            model=payload.model,
            description=payload.description,
            capabilities=payload.capabilities,
        )
        device = await store.create_manual_device(manual)
        if payload.enabled is not None and not payload.enabled:
            device = await store.update_device(device.id, enabled=False) or device
        return DeviceOut(**device.__dict__)

    @app.get("/devices/{device_id}", dependencies=[Depends(auth_dependency)], response_model=DeviceOut)
    async def get_device(device_id: str) -> DeviceOut:
        device = await store.device(device_id)
        if not device:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
        return DeviceOut(**device.__dict__)

    @app.patch("/devices/{device_id}", dependencies=[Depends(auth_dependency)], response_model=DeviceOut)
    async def update_device(device_id: str, payload: DeviceUpdate) -> DeviceOut:
        updated = await store.update_device(
            device_id,
            ip=payload.ip,
            model=payload.model,
            description=payload.description,
            capabilities=payload.capabilities,
            enabled=payload.enabled,
        )
        if not updated:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
        return DeviceOut(**updated.__dict__)

    @app.post("/devices/{device_id}/test", dependencies=[Depends(auth_dependency)], status_code=status.HTTP_202_ACCEPTED)
    async def test_device(device_id: str, payload: TestAction) -> dict[str, str]:
        device = await store.device(device_id)
        if not device:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
        try:
            update = DeviceStateUpdate(device_id=device_id, payload=payload.payload)
            await store.enqueue_state(update)
        except Exception as exc:  # pragma: no cover - defensive
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"status": "queued"}

    @app.get("/mappings", dependencies=[Depends(auth_dependency)], response_model=list[MappingOut])
    async def list_mappings() -> list[MappingOut]:
        rows = await store.mapping_rows()
        return [MappingOut(**row.__dict__) for row in rows]

    @app.post("/mappings", dependencies=[Depends(auth_dependency)], response_model=MappingOut, status_code=status.HTTP_201_CREATED)
    async def create_mapping(payload: MappingCreate) -> MappingOut:
        try:
            row = await store.create_mapping(
                device_id=payload.device_id,
                universe=payload.universe,
                channel=payload.channel,
                length=payload.length,
                allow_overlap=payload.allow_overlap,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return MappingOut(**row.__dict__)

    @app.get("/mappings/{mapping_id}", dependencies=[Depends(auth_dependency)], response_model=MappingOut)
    async def get_mapping(mapping_id: int) -> MappingOut:
        row = await store.mapping_by_id(mapping_id)
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mapping not found")
        return MappingOut(**row.__dict__)

    @app.put("/mappings/{mapping_id}", dependencies=[Depends(auth_dependency)], response_model=MappingOut)
    async def update_mapping(mapping_id: int, payload: MappingUpdate) -> MappingOut:
        try:
            row = await store.update_mapping(
                mapping_id,
                device_id=payload.device_id,
                universe=payload.universe,
                channel=payload.channel,
                length=payload.length,
                allow_overlap=payload.allow_overlap,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mapping not found")
        return MappingOut(**row.__dict__)

    @app.delete("/mappings/{mapping_id}", dependencies=[Depends(auth_dependency)], status_code=status.HTTP_204_NO_CONTENT)
    async def delete_mapping(mapping_id: int) -> None:
        deleted = await store.delete_mapping(mapping_id)
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mapping not found")
        return None

    return app


class ApiService:
    """Lifecycle wrapper for the FastAPI/uvicorn server."""

    def __init__(self, config: Config, store: DeviceStore) -> None:
        self.config = config
        self.store = store
        self.logger = get_logger("govee.api")
        self._server: Optional[uvicorn.Server] = None
        self._server_task: Optional[Any] = None

    async def start(self) -> None:
        if self._server:
            return
        app = create_app(self.config, self.store)
        uvicorn_config = uvicorn.Config(
            app,
            host="0.0.0.0",
            port=self.config.api_port,
            log_config=None,
            loop="asyncio",
        )
        self._server = uvicorn.Server(config=uvicorn_config)
        self._server_task = asyncio.create_task(self._server.serve())
        self.logger.info("API server starting", extra={"port": self.config.api_port})

    async def stop(self) -> None:
        if not self._server:
            return
        self.logger.info("Stopping API server")
        self._server.should_exit = True
        if self._server_task:
            await self._server_task
        self._server = None
        self._server_task = None
