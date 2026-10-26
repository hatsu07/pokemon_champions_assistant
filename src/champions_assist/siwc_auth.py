"""Official SIWC OAuth/PKCE for a local Windows open-source app.

Tokens never reach settings JSON, logfiles, GitHub, or command line. The sole
persistent token store is Windows user-scoped DPAPI encrypted local storage.
"""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time
from urllib.parse import urlencode, urlparse, parse_qs
from urllib.request import Request, urlopen
import uuid
import webbrowser


ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
AUTHORIZE_URL = ISSUER + "/api/accounts/authorize"
TOKEN_URL = ISSUER + "/api/accounts/oauth/token"
REQUIRED_PLAN_SCOPE = "chatgpt.tokens.use.direct"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"


class SignInError(RuntimeError):
    """A stable code only, never an exception containing OAuth secrets."""


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _blob(data: bytes):
    buffer = ctypes.create_string_buffer(data)
    return _DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


def _dpapi(data: bytes, *, decrypt: bool = False) -> bytes:
    if sys.platform != "win32":
        raise SignInError("WINDOWS_DPAPI_REQUIRED")
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    method = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    input_blob, owner = _blob(data)
    output = _DATA_BLOB()
    if decrypt:
        method.argtypes = [ctypes.POINTER(_DATA_BLOB), ctypes.c_void_p,
                           ctypes.POINTER(_DATA_BLOB), ctypes.c_void_p,
                           ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DATA_BLOB)]
        method.restype = wintypes.BOOL
        ok = method(ctypes.byref(input_blob), None, None, None, None, 0, ctypes.byref(output))
    else:
        method.argtypes = [ctypes.POINTER(_DATA_BLOB), wintypes.LPCWSTR,
                           ctypes.POINTER(_DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p,
                           wintypes.DWORD, ctypes.POINTER(_DATA_BLOB)]
        method.restype = wintypes.BOOL
        # CRYPTPROTECT_UI_FORBIDDEN: never ask OS for interactive DPAPI UI.
        ok = method(ctypes.byref(input_blob), None, None, None, None, 1, ctypes.byref(output))
    if not ok:
        raise SignInError("CREDENTIAL_ENCRYPTION_FAILED")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))


class ProtectedSessionStore:
    def __init__(self, root: str | Path, *, protect=None, unprotect=None):
        self.path = Path(root) / "auth" / "chatgpt-session.dpapi"
        self._protect = protect or (lambda value: _dpapi(value))
        self._unprotect = unprotect or (lambda value: _dpapi(value, decrypt=True))

    def load(self) -> dict:
        if not self.path.exists():
            return {"host_id": "urn:uuid:" + str(uuid.uuid4()), "accounts": {}, "selected": None}
        try:
            value = json.loads(self._unprotect(self.path.read_bytes()).decode("utf-8"))
            if not isinstance(value, dict) or not str(value.get("host_id", "")).startswith("urn:uuid:"):
                raise ValueError("invalid session")
            if not isinstance(value.get("accounts"), dict):
                raise ValueError("invalid accounts")
            return value
        except (ValueError, OSError, UnicodeError) as exc:
            raise SignInError("CREDENTIAL_STORE_INVALID") from exc

    def save(self, document: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = self._protect(json.dumps(document, ensure_ascii=False).encode("utf-8"))
        fd, tmp = tempfile.mkstemp(prefix=".auth-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(encoded)
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


def _fetch_json(url: str, *, bearer: str | None = None, data: bytes | None = None,
                timeout: int = 20) -> dict:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {"auth.openai.com", "api.openai.com"}:
        raise SignInError("UNTRUSTED_OAUTH_ENDPOINT")
    headers = {"Accept": "application/json"}
    if bearer:
        headers["Authorization"] = "Bearer " + bearer
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    try:
        with urlopen(Request(url, headers=headers, data=data), timeout=timeout) as response:
            value = json.load(response)
        if not isinstance(value, dict):
            raise SignInError("INVALID_SERVER_RESPONSE")
        return value
    except SignInError:
        raise
    except Exception as exc:
        raise SignInError("OAUTH_NETWORK_ERROR") from exc


def _form(values: dict[str, str]) -> bytes:
    return urlencode(values).encode("ascii")


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _validate_id_token(id_token: str, client_id: str, nonce: str) -> dict:
    """Validate the signature, issuer, audience, expiry and current OAuth nonce."""
    try:
        import jwt
        config = _fetch_json(ISSUER + "/.well-known/openid-configuration")
        if config.get("issuer") != ISSUER:
            raise SignInError("INVALID_ISSUER")
        jwks_url = str(config["jwks_uri"])
        url = urlparse(jwks_url)
        if url.scheme != "https" or url.hostname != "auth.openai.com":
            raise SignInError("UNTRUSTED_JWKS_ENDPOINT")
        key = jwt.PyJWKClient(jwks_url).get_signing_key_from_jwt(id_token).key
        claims = jwt.decode(id_token, key, algorithms=["RS256", "ES256"],
                            audience=client_id, issuer=ISSUER, options={"require": ["exp", "sub", "iss", "aud"]})
        if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise SignInError("INVALID_NONCE")
        return claims
    except SignInError:
        raise
    except Exception as exc:
        raise SignInError("ID_TOKEN_VALIDATION_FAILED") from exc


def _wait_for_callback(redirect_path: str, timeout_seconds: int, browser_url) -> dict[str, str]:
    received: dict[str, str] = {}

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path != redirect_path:
                self.send_error(404)
                return
            params = parse_qs(parsed.query, keep_blank_values=True)
            received.update({name: values[0] for name, values in params.items() if values})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b"<html><body>Authentication returned to Pokemon Champions Assistant. You may close this tab.</body></html>")

        def log_message(self, *_args):
            pass  # Never log authorization codes or callback query strings.

    with HTTPServer(("127.0.0.1", 0), Callback) as server:
        server.timeout = timeout_seconds
        # Resolve chosen port before opening browser, then use exact URI in token exchange.
        return _receive_callback(server, received, browser_url, timeout_seconds)


def _receive_callback(server, received: dict, url_factory, timeout_seconds: int) -> dict:
    import webbrowser as browser
    target = url_factory(server.server_port)
    if not browser.open(target):
        raise SignInError("BROWSER_OPEN_FAILED")
    server.handle_request()
    if not received:
        raise SignInError("LOGIN_TIMEOUT")
    return {"callback": received, "redirect_uri": f"http://127.0.0.1:{server.server_port}/auth/callback"}


class ChatGPTSignIn:
    """One selected account with add-account support and pinned identity checks."""

    def __init__(self, store: ProtectedSessionStore, *, validator=None, fetcher=None, callback=None):
        self.store = store
        self.validate = validator or _validate_id_token
        self.fetch = fetcher or _fetch_json
        self.callback = callback or _wait_for_callback

    def status(self) -> dict:
        document = self.store.load()
        profile = document["accounts"].get(document.get("selected"))
        return {"connected": bool(profile and profile.get("access_token")),
                "plan_enabled": bool(profile and REQUIRED_PLAN_SCOPE in profile.get("scopes", [])),
                "email": "" if not profile else profile.get("email", ""),
                "accounts": len(document["accounts"])}

    def sign_in(self, *, new_account: bool = False) -> dict:
        document = self.store.load()
        self.store.save(document)  # Persist stable host ID *before* first sign-in.
        old_id = None if new_account else document.get("selected")
        old = document["accounts"].get(old_id) if old_id else None
        client_id = old_id if old else "dynamic_agent_client"
        state = secrets.token_urlsafe(30)
        nonce = secrets.token_urlsafe(30)
        verifier, challenge = _pkce()

        def auth_url(port: int) -> str:
            values = {"client_id": client_id, "ext_agent_host_id": document["host_id"],
                      "response_type": "code", "redirect_uri": f"http://127.0.0.1:{port}/auth/callback",
                      "scope": SCOPES, "resource": RESOURCE, "state": state, "nonce": nonce,
                      "code_challenge_method": "S256", "code_challenge": challenge}
            if old:
                if old.get("id_token"):
                    values["id_token_hint"] = old["id_token"]
                if old.get("email"):
                    values["login_hint"] = old["email"]
            else:
                values["agent_name_hint"] = "Pokemon Champions Assistant"
            return AUTHORIZE_URL + "?" + urlencode(values)

        result = self.callback("/auth/callback", 120, auth_url)
        params = result["callback"]
        if not secrets.compare_digest(str(params.get("state", "")), state):
            raise SignInError("INVALID_STATE")
        if "error" in params:
            raise SignInError("AUTHORIZATION_DECLINED")
        issued = params.get("client_id") or old_id
        if not issued or issued == "dynamic_agent_client" or (old_id and issued != old_id):
            raise SignInError("INVALID_ISSUED_CLIENT")
        if not params.get("code"):
            raise SignInError("AUTHORIZATION_CODE_MISSING")
        response = self.fetch(TOKEN_URL, data=_form({
            "grant_type": "authorization_code", "client_id": issued, "code": params["code"],
            "code_verifier": verifier, "redirect_uri": result["redirect_uri"], "resource": RESOURCE,
        }))
        token = str(response.get("id_token", ""))
        if not token or not response.get("access_token"):
            raise SignInError("OAUTH_TOKEN_MISSING")
        claims = self.validate(token, issued, nonce)
        if old and claims.get("sub") != old.get("subject"):
            raise SignInError("ACCOUNT_IDENTITY_MISMATCH")
        scopes = str(response.get("scope", "")).split()
        profile = {"client_id": issued, "subject": str(claims["sub"]),
                   "email": str(claims.get("email", "")), "id_token": token,
                   "access_token": response["access_token"],
                   "refresh_token": response.get("refresh_token", ""),
                   "expires_at": time.time() + int(response.get("expires_in", 3600)),
                   "scopes": scopes}
        document["accounts"][issued] = profile
        document["selected"] = issued
        self.store.save(document)
        return self.status()

    def access_token(self, *, force_refresh: bool = False) -> str:
        document = self.store.load()
        issued = document.get("selected")
        profile = document["accounts"].get(issued)
        if not profile or not profile.get("access_token"):
            raise SignInError("NOT_SIGNED_IN")
        if REQUIRED_PLAN_SCOPE not in profile.get("scopes", []):
            raise SignInError("PLAN_PERMISSION_NOT_GRANTED")
        if force_refresh or time.time() + 120 >= float(profile.get("expires_at", 0)):
            if not profile.get("refresh_token"):
                raise SignInError("REAUTHORIZATION_REQUIRED")
            response = self.fetch(TOKEN_URL, data=_form({
                "grant_type": "refresh_token", "client_id": issued,
                "refresh_token": profile["refresh_token"], "resource": RESOURCE,
            }))
            if not response.get("access_token"):
                raise SignInError("TOKEN_REFRESH_FAILED")
            profile["access_token"] = response["access_token"]
            profile["refresh_token"] = response.get("refresh_token", profile["refresh_token"])
            profile["expires_at"] = time.time() + int(response.get("expires_in", 3600))
            if "scope" in response:
                profile["scopes"] = str(response["scope"]).split()
            if REQUIRED_PLAN_SCOPE not in profile.get("scopes", []):
                raise SignInError("PLAN_PERMISSION_NOT_GRANTED")
            self.store.save(document)
        return str(profile["access_token"])

    def models(self) -> list[dict[str, str]]:
        result = self.fetch(RESOURCE + "/models", bearer=self.access_token())
        listed = result.get("models", [])
        if not isinstance(listed, list):
            raise SignInError("INVALID_MODELS_RESPONSE")
        return [{"slug": v["slug"], "display_name": v.get("display_name", v["slug"])}
                for v in listed if isinstance(v, dict) and v.get("visibility") == "list" and isinstance(v.get("slug"), str)]

    def sign_out(self) -> None:
        document = self.store.load()
        selected = document.get("selected")
        if selected in document["accounts"]:
            profile = document["accounts"][selected]
            # Keep client mapping so the next login reuses the issued client ID.
            for key in ("id_token", "access_token", "refresh_token", "expires_at"):
                profile.pop(key, None)
            profile["scopes"] = []
        self.store.save(document)
