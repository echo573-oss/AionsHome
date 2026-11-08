"""AionsHome 本机表单资料与网站凭据存储。

整个文件采用 Windows 当前用户 DPAPI 加密。凭据不能走聊天/MCP API；
密码仅供未来受限浏览器填充模块通过进程内接口使用。
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

from config import DATA_DIR

VAULT_PATH = DATA_DIR / "form_vault.dpapi"
_ENTROPY = b"AionsHome form vault v1; local Windows user"
_HEADER = b"AIONFV1\0"
FIELDS = frozenset({
    "full_name", "given_name", "family_name", "phone", "email",
    "company", "country", "province", "city", "district", "street",
    "address2", "postal_code",
})
_LOCK = threading.RLock()


class FormVaultError(ValueError):
    pass


class FormVaultUnavailable(FormVaultError):
    pass


def supported() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import win32crypt
        return hasattr(win32crypt, "CryptProtectData")
    except ImportError:
        return False


def _win32():
    if not supported():
        raise FormVaultUnavailable("仅支持安装 pywin32 的 Windows 服务器")
    import win32crypt
    return win32crypt


def _seal(raw: bytes) -> bytes:
    win32 = _win32()
    # Windows DPAPI，作用域为启动 AionsHome 的 Windows 用户；禁止弹出系统 UI。
    return _HEADER + win32.CryptProtectData(
        raw, "AionsHome Form Vault", _ENTROPY, None, None, 0x1,
    )


def _unseal(blob: bytes) -> bytes:
    if not blob.startswith(_HEADER):
        raise FormVaultError("资料库格式不受支持")
    try:
        return _win32().CryptUnprotectData(
            blob[len(_HEADER):], _ENTROPY, None, None, 0x1,
        )[1]
    except Exception as exc:
        raise FormVaultError("资料库无法解密；请检查 Windows 登录用户与原始数据") from None


def _load() -> dict:
    if not VAULT_PATH.exists():
        return {"version": 1, "profiles": [], "logins": []}
    try:
        payload = json.loads(_unseal(VAULT_PATH.read_bytes()))
        if (not isinstance(payload, dict) or payload.get("version") != 1
            or not isinstance(payload.get("profiles"), list)
            or not isinstance(payload.get("logins"), list)):
            raise FormVaultError("资料库数据版本不匹配")
        return payload
    except FormVaultError:
        raise
    except (OSError, ValueError, TypeError):
        raise FormVaultError("资料库无法读取，请恢复备份") from None


def _save(data: dict) -> None:
    # 先完整加密，再原子替换；不保留含明文内容的临时文件。
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ciphertext = _seal(raw)
    VAULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = VAULT_PATH.with_name(VAULT_PATH.name + "." + secrets.token_hex(6) + ".tmp")
    try:
        with tmp.open("xb") as handle:
            handle.write(ciphertext)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, VAULT_PATH)
    finally:
        tmp.unlink(missing_ok=True)


def _text(value: str, maximum: int, *, name: str, required: bool = True) -> str:
    if not isinstance(value, str):
        raise FormVaultError(name + "必须是文字")
    cleaned = value.strip()
    if len(cleaned) > maximum or (required and not cleaned):
        raise FormVaultError(name + "长度无效")
    return cleaned


def _origin(site: str) -> str:
    site = _text(site, 300, name="网站地址")
    try:
        url = urlsplit(site)
        host = url.hostname
        port = url.port
    except ValueError:
        raise FormVaultError("网站地址无效") from None
    if (url.scheme.lower() != "https" or not host or not host.strip(".")
        or url.username or url.password or url.path not in ("", "/")
        or url.query or url.fragment or port is not None):
        raise FormVaultError("请填写 HTTPS 网站根地址，例如 https://example.com")
    try:
        host = host.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError:
        raise FormVaultError("域名无效") from None
    if "." not in host or len(host) > 253:
        raise FormVaultError("请填写有效的网站域名")
    return "https://" + host


def _fields(raw: dict) -> dict[str, str]:
    if not isinstance(raw, dict) or len(raw) > len(FIELDS):
        raise FormVaultError("表单字段不符合要求")
    result: dict[str, str] = {}
    for name, value in raw.items():
        if name not in FIELDS:
            raise FormVaultError("存在不支持的表单字段")
        if value:
            result[name] = _text(value, 200, name=name)
    if not result or len(json.dumps(result, ensure_ascii=False)) > 3000:
        raise FormVaultError("请填写有效的资料字段")
    return result


def _new_id() -> str:
    return "fv_" + secrets.token_urlsafe(18)


def list_records() -> dict:
    with _LOCK:
        data = _load()
        return {
            "supported": supported(),
            "profiles": [
                {"id": item["id"], "label": item["label"], "fields": dict(item["fields"])}
                for item in data["profiles"]
            ],
            "logins": [
                {"id": item["id"], "label": item["label"], "site": item["site"],
                 "username": item["username"]}
                for item in data["logins"]
            ],
        }


def save_profile(label: str, fields: dict, record_id: str | None = None) -> dict:
    label = _text(label, 64, name="资料名称")
    checked = _fields(fields)
    with _LOCK:
        data = _load()
        matches = [x for x in data["profiles"] if x["id"] == record_id] if record_id else []
        if record_id and not matches:
            raise FormVaultError("要更新的资料不存在")
        if not record_id and len(data["profiles"]) >= 30:
            raise FormVaultError("最多保存 30 套表单资料")
        item = {"id": record_id or _new_id(), "label": label, "fields": checked}
        if matches:
            data["profiles"] = [item if x["id"] == record_id else x for x in data["profiles"]]
        else:
            data["profiles"].append(item)
        _save(data)
    return item


def save_login(label: str, site: str, username: str, password: str) -> dict:
    label = _text(label, 64, name="账号名称")
    site = _origin(site)
    username = _text(username, 256, name="用户名")
    # 密码不 strip，保证空格及特殊符号保持原样。
    if not isinstance(password, str) or not (1 <= len(password) <= 4096):
        raise FormVaultError("密码长度无效")
    with _LOCK:
        data = _load()
        if len(data["logins"]) >= 100:
            raise FormVaultError("最多保存 100 个账号")
        if any(x["site"] == site and x["username"] == username for x in data["logins"]):
            raise FormVaultError("该网站与用户名已存在；请先删除旧条目再重新保存")
        item = {"id": _new_id(), "label": label, "site": site,
                "username": username, "password": password}
        data["logins"].append(item)
        _save(data)
        return {"id": item["id"], "label": label, "site": site, "username": username}


def delete_record(kind: str, record_id: str) -> None:
    collection = {"profile": "profiles", "login": "logins"}.get(kind)
    if not collection or not record_id:
        raise FormVaultError("要删除的条目无效")
    with _LOCK:
        data = _load()
        previous = len(data[collection])
        data[collection] = [x for x in data[collection] if x["id"] != record_id]
        if len(data[collection]) == previous:
            raise FormVaultError("条目不存在")
        _save(data)


def login_for_fill(record_id: str, website_url: str) -> tuple[str, str]:
    """仅供将来的本机浏览器执行模块调用；永不暴露为 HTTP / MCP 路由。"""
    try:
        target = urlsplit(website_url)
        if (target.scheme.lower() != "https" or not target.hostname
            or target.username or target.password or target.port is not None):
            raise FormVaultError("仅允许在标准 HTTPS 网站填写凭据")
    except ValueError:
        raise FormVaultError("网站地址无效") from None
    site = _origin("https://" + target.hostname)
    with _LOCK:
        data = _load()
        for login in data["logins"]:
            if login["id"] == record_id and login["site"] == site:
                return login["username"], login["password"]
    raise FormVaultError("当前网站没有匹配的已保存凭据")
