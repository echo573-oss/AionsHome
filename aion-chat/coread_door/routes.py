"""Door page, classic companionship reader, and the coread URL probe."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config import BASE_DIR

from .probe import resolve_door

router = APIRouter()
STATIC = Path(__file__).with_name("static")
CLASSIC = BASE_DIR / "static" / "reading.html"
NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate"}


class DoorAssets(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if Path(path).suffix in {".js", ".css"}:
            response.headers["Cache-Control"] = "no-cache"
        return response


def _classic() -> FileResponse:
    return FileResponse(CLASSIC, headers=NO_CACHE)


def _door() -> FileResponse:
    return FileResponse(STATIC / "index.html", headers=NO_CACHE)


@router.get("/reading")
async def reading_entry(request: Request):
    if "book" in request.query_params:
        return _classic()
    return _door()


@router.get("/reading-classic")
async def reading_classic():
    return _classic()


@router.get("/api/coread-door")
async def coread_door():
    return JSONResponse(resolve_door())


def install(app):
    app.include_router(router)
    app.mount("/coread-door-assets", DoorAssets(directory=str(STATIC)), name="coread-door-assets")
