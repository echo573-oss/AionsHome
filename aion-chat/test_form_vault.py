"""表单资料与安全凭据离线测试；不读写真实 data，非 Windows 可运行。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

import form_vault
from routes import form_vault as form_vault_routes


class FormVaultStoreTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path_patch = patch.object(form_vault, "VAULT_PATH", Path(temp.name) / "form_vault.dpapi")
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        # 独立 fake 只验证存储接口，不作为 Windows DPAPI 安全性证明。
        self.enc_patch = patch.object(form_vault, "_seal", lambda plain: b"mock-cipher:" + plain[::-1])
        self.dec_patch = patch.object(form_vault, "_unseal", lambda cipher: cipher[len(b"mock-cipher:"):][::-1])
        self.enc_patch.start()
        self.dec_patch.start()
        self.addCleanup(self.enc_patch.stop)
        self.addCleanup(self.dec_patch.stop)

    def test_profile_and_login_are_persisted_without_cleartext(self):
        profile = form_vault.save_profile(
            "日常收件", {"full_name": "Test Person", "city": "Hangzhou"},
        )
        login = form_vault.save_login(
            "站点账户", "https://Example.Com/", "sample@example.com", "VerySecret!123",
        )
        stored = form_vault.VAULT_PATH.read_bytes()
        self.assertNotIn(b"VerySecret!123", stored)
        self.assertNotIn(b"Test Person", stored)
        self.assertTrue(stored.startswith(b"mock-cipher:"))
        records = form_vault.list_records()
        self.assertEqual(records["profiles"][0]["id"], profile["id"])
        self.assertEqual(records["logins"][0]["site"], "https://example.com")
        self.assertNotIn("password", json.dumps(records))
        self.assertNotIn("VerySecret!123", json.dumps(records))
        self.assertNotIn("password", json.dumps(login))

    def test_profile_update_keeps_id(self):
        first = form_vault.save_profile("旧", {"phone": "13000000000"})
        next_ = form_vault.save_profile(
            "新", {"phone": "13100000000"}, record_id=first["id"],
        )
        self.assertEqual(first["id"], next_["id"])
        self.assertEqual(form_vault.list_records()["profiles"][0]["fields"]["phone"], "13100000000")

    def test_unrecognized_fields_rejected(self):
        with self.assertRaises(form_vault.FormVaultError):
            form_vault.save_profile("bad", {"api_key": "must-not-store"})

    def test_duplicate_login_rejected_and_secret_not_returned(self):
        form_vault.save_login("one", "https://example.com", "alice", "secret1")
        with self.assertRaises(form_vault.FormVaultError):
            form_vault.save_login("two", "https://example.com", "alice", "secret2")
        self.assertEqual(len(form_vault.list_records()["logins"]), 1)

    def test_login_for_fill_requires_matching_https_hostname(self):
        record = form_vault.save_login("bank", "https://example.com", "alice", "secret1")
        self.assertEqual(
            form_vault.login_for_fill(record["id"], "https://example.com/account/login"),
            ("alice", "secret1"),
        )
        for site in (
            "http://example.com/login",
            "https://sub.example.com/login",
            "https://another-example.com/",
            "https://example.com.evil.org/",
        ):
            with self.subTest(site=site):
                with self.assertRaises(form_vault.FormVaultError):
                    form_vault.login_for_fill(record["id"], site)

    def test_invalid_site_origin_rejected(self):
        for site in (
            "http://example.com", "https://example.com/path/login",
            "https://user:pass@example.com", "https://example.com:8443",
            "https://example.com?redirect=evil", "file:///etc/passwd",
        ):
            with self.subTest(site=site):
                with self.assertRaises(form_vault.FormVaultError):
                    form_vault.save_login("test", site, "alice", "secret")

    def test_delete_login_and_profile(self):
        profile = form_vault.save_profile("p", {"city": "city"})
        login = form_vault.save_login("l", "https://example.com", "user", "pw")
        form_vault.delete_record("login", login["id"])
        form_vault.delete_record("profile", profile["id"])
        self.assertFalse(form_vault.list_records()["profiles"])
        self.assertFalse(form_vault.list_records()["logins"])

    def test_corrupted_file_does_not_reset(self):
        form_vault.VAULT_PATH.write_bytes(b"broken")
        def failed(_):
            raise form_vault.FormVaultError("corrupted")
        with patch.object(form_vault, "_unseal", failed):
            with self.assertRaises(form_vault.FormVaultError):
                form_vault.save_profile("new", {"email": "a@example.com"})
        self.assertEqual(form_vault.VAULT_PATH.read_bytes(), b"broken")


def fake_request(host="localhost:8080", client="127.0.0.1", origin=None, method="GET"):
    pairs = [(b"host", host.encode("ascii"))]
    if origin is not None:
        pairs.append((b"origin", origin.encode("ascii")))
    scope = {
        "type": "http",
        "method": method, "scheme": "http",
        "path": "/api/form-vault/records", "query_string": b"",
        "headers": pairs, "client": (client, 3000),
        "server": ("127.0.0.1", 8080),
    }
    return Request(scope)


class FormVaultNetworkTests(unittest.TestCase):
    def test_read_allowed_from_loopback_only(self):
        form_vault_routes.require_local(fake_request())
        for client in ("192.168.1.20", "100.100.100.25", "203.0.113.20"):
            with self.subTest(client=client):
                with self.assertRaises(HTTPException):
                    form_vault_routes.require_local(fake_request(client=client))

    def test_dns_rebinding_hostname_rejected(self):
        with self.assertRaises(HTTPException):
            form_vault_routes.require_local(fake_request(host="evil.example.com:8080"))

    def test_cross_origin_writes_rejected(self):
        for origin in (None, "https://evil.test", "http://localhost:1234"):
            with self.subTest(origin=origin):
                with self.assertRaises(HTTPException):
                    form_vault_routes.require_local(
                        fake_request(origin=origin, method="POST"),
                        mutation=True,
                    )
        form_vault_routes.require_local(
            fake_request(origin="http://localhost:8080", method="POST"), mutation=True
        )

    def test_password_is_never_returned_by_api(self):
        with tempfile.TemporaryDirectory() as d:
            with (
                patch.object(form_vault, "VAULT_PATH", Path(d) / "vault.dpapi"),
                patch.object(form_vault, "_seal", lambda raw: b"mock:" + raw[::-1]),
                patch.object(form_vault, "_unseal", lambda raw: raw[5:][::-1]),
                patch.object(form_vault, "supported", return_value=True),
            ):
                req = fake_request(origin="http://localhost:8080", method="POST")
                body = form_vault_routes.LoginBody(
                    label="Personal", site="https://example.com",
                    username="alice", password="secret_passphrase_987",
                )
                posted = form_vault_routes.add_login(req, body)
                self.assertNotIn("secret_passphrase_987", posted.body.decode())
                fetched = form_vault_routes.records(fake_request())
                self.assertNotIn("secret_passphrase_987", fetched.body.decode())


@unittest.skipUnless(sys.platform == "win32" and form_vault.supported(),
                     "Windows DPAPI integration only")
class WindowsDpapiSmokeTests(unittest.TestCase):
    def test_dpapi_round_trip_and_no_cleartext(self):
        secret = b"secrets-are-encrypted" + bytes(range(32))
        encrypted = form_vault._seal(secret)
        self.assertNotIn(secret, encrypted)
        self.assertEqual(form_vault._unseal(encrypted), secret)


if __name__ == "__main__":
    unittest.main()
