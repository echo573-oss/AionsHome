"""本机表单资料与凭据管理接口：仅 Windows 本机浏览器可访问。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from local_http import require_local
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import form_vault

router = APIRouter(prefix="/api/form-vault", tags=["form-vault"])
_NO_CACHE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def _result(payload: dict) -> JSONResponse:
    return JSONResponse(payload, headers=_NO_CACHE)


def _ready():
    if not form_vault.supported():
        raise HTTPException(503, "仅在已安装 pywin32 的 Windows 本机开放资料库")


class ProfileBody(BaseModel):
    label: str = Field(min_length=1, max_length=64)
    fields: dict[str, str]


class LoginBody(BaseModel):
    label: str = Field(min_length=1, max_length=64)
    site: str = Field(min_length=1, max_length=300)
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=4096)


@router.get("/status")
def status(request: Request):
    require_local(request)
    return _result({"supported": form_vault.supported(), "local_only": True})


@router.get("/records")
def records(request: Request):
    require_local(request)
    _ready()
    try:
        return _result(form_vault.list_records())
    except form_vault.FormVaultError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/profiles")
def add_profile(request: Request, body: ProfileBody):
    require_local(request, mutation=True)
    _ready()
    try:
        return _result({"ok": True, "profile": form_vault.save_profile(body.label, body.fields)})
    except form_vault.FormVaultError as exc:
        raise HTTPException(422, str(exc)) from None


@router.put("/profiles/{record_id}")
def edit_profile(request: Request, record_id: str, body: ProfileBody):
    require_local(request, mutation=True)
    _ready()
    try:
        return _result({"ok": True, "profile": form_vault.save_profile(
            body.label, body.fields, record_id=record_id,
        )})
    except form_vault.FormVaultError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/logins")
def add_login(request: Request, body: LoginBody):
    require_local(request, mutation=True)
    _ready()
    try:
        # 仅回传名称、用户名和网站；永不返回密码。
        return _result({"ok": True, "login": form_vault.save_login(
            body.label, body.site, body.username, body.password,
        )})
    except form_vault.FormVaultError as exc:
        raise HTTPException(422, str(exc)) from None


@router.delete("/records/{kind}/{record_id}")
def remove_record(request: Request, kind: str, record_id: str):
    require_local(request, mutation=True)
    _ready()
    try:
        form_vault.delete_record(kind, record_id)
        return _result({"ok": True})
    except form_vault.FormVaultError as exc:
        raise HTTPException(422, str(exc)) from None
