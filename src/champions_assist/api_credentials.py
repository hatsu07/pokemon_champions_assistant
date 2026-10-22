"""Windows Credential Manager storage for the user's OpenAI API key.

The credential blob is never placed into settings JSON, sqlite, diagnostic logs,
GitHub CI, or the model prompt. Only the explicitly opted-in API client reads it.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import sys
from typing import Protocol


TARGET_NAME = "PokemonChampionsAssistant/OpenAI_API_Key"
ERROR_NOT_FOUND = 1168
CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
CRED_MAX_CREDENTIAL_BLOB_SIZE = 5 * 512


class CredentialStoreError(RuntimeError):
    """Secret-safe exception without provider details or credential values."""


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", _FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


class CredentialBackend(Protocol):
    def read(self) -> str | None: ...
    def write(self, key: str) -> None: ...
    def delete(self) -> bool: ...


class WindowsCredentialBackend:
    """Win32 CredReadW/CredWriteW/CredDeleteW for one generic local credential."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise CredentialStoreError("WINDOWS_CREDENTIAL_MANAGER_UNAVAILABLE")
        self._api = ctypes.WinDLL("advapi32", use_last_error=True)
        self._api.CredReadW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(_CREDENTIALW)),
        ]
        self._api.CredReadW.restype = wintypes.BOOL
        self._api.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
        self._api.CredWriteW.restype = wintypes.BOOL
        self._api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._api.CredDeleteW.restype = wintypes.BOOL
        self._api.CredFree.argtypes = [ctypes.c_void_p]
        self._api.CredFree.restype = None

    def read(self) -> str | None:
        ptr = ctypes.POINTER(_CREDENTIALW)()
        if not self._api.CredReadW(TARGET_NAME, CRED_TYPE_GENERIC, 0, ctypes.byref(ptr)):
            if ctypes.get_last_error() == ERROR_NOT_FOUND:
                return None
            raise CredentialStoreError("CREDENTIAL_READ_FAILED")
        try:
            item = ptr.contents
            if not item.CredentialBlob or item.CredentialBlobSize < 2 or item.CredentialBlobSize % 2:
                raise CredentialStoreError("CREDENTIAL_BLOB_INVALID")
            raw = ctypes.string_at(item.CredentialBlob, item.CredentialBlobSize)
            try:
                return raw.decode("utf-16-le")
            except UnicodeError as exc:
                raise CredentialStoreError("CREDENTIAL_BLOB_INVALID") from exc
        finally:
            self._api.CredFree(ctypes.cast(ptr, ctypes.c_void_p))

    def write(self, key: str) -> None:
        blob = key.encode("utf-16-le")
        if not blob or len(blob) > CRED_MAX_CREDENTIAL_BLOB_SIZE:
            raise CredentialStoreError("CREDENTIAL_SIZE_INVALID")
        buffer = ctypes.create_string_buffer(blob)
        item = _CREDENTIALW()
        item.Type = CRED_TYPE_GENERIC
        item.TargetName = TARGET_NAME
        item.CredentialBlobSize = len(blob)
        item.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
        item.Persist = CRED_PERSIST_LOCAL_MACHINE
        item.UserName = "OpenAI"
        try:
            if not self._api.CredWriteW(ctypes.byref(item), 0):
                raise CredentialStoreError("CREDENTIAL_WRITE_FAILED")
        finally:
            ctypes.memset(ctypes.addressof(buffer), 0, ctypes.sizeof(buffer))

    def delete(self) -> bool:
        if self._api.CredDeleteW(TARGET_NAME, CRED_TYPE_GENERIC, 0):
            return True
        if ctypes.get_last_error() == ERROR_NOT_FOUND:
            return False
        raise CredentialStoreError("CREDENTIAL_DELETE_FAILED")


class ApiKeyVault:
    """Narrow testable facade; production uses the current user's OS credential vault."""

    def __init__(self, backend: CredentialBackend | None = None) -> None:
        self._backend = backend if backend is not None else WindowsCredentialBackend()

    def save(self, key: str) -> None:
        if not isinstance(key, str) or not key.strip() or key != key.strip() or any(ord(ch) < 32 for ch in key):
            raise CredentialStoreError("INVALID_API_KEY_FORMAT")
        self._backend.write(key)

    def read(self) -> str | None:
        return self._backend.read()

    def is_configured(self) -> bool:
        return self.read() is not None

    def delete(self) -> bool:
        return self._backend.delete()


def get_api_key(*, environ: dict[str, str] | None = None, vault: ApiKeyVault | None = None) -> str | None:
    """Prefer GUI-stored credential, then optional runtime environment fallback."""
    environment = os.environ if environ is None else environ
    if vault is not None:
        return vault.read() or environment.get("CHAMPIONS_ASSIST_OPENAI_API_KEY") or None
    if sys.platform == "win32" and environ is None:
        return ApiKeyVault().read() or environment.get("CHAMPIONS_ASSIST_OPENAI_API_KEY") or None
    return environment.get("CHAMPIONS_ASSIST_OPENAI_API_KEY") or None
