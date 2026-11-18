"""Personal records: authenticated ciphertext; local key protected by Windows DPAPI."""

from __future__ import annotations

import ctypes
import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class ProtectedStorageUnavailable(Exception):
    """Never fall back to plaintext storage."""


class KeyProtector(Protocol):
    def protect(self, data: bytes) -> bytes: ...

    def unprotect(self, data: bytes) -> bytes: ...


class _Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]


class WindowsDPAPI:
    """Current Windows user, no interactive UI and no machine-wide key scope."""

    def _apply(self, data: bytes, decrypt: bool) -> bytes:
        if os.name != "nt":
            raise ProtectedStorageUnavailable("Personal storage requires Windows DPAPI.")
        buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        source = _Blob(len(data), buffer)
        output = _Blob()
        try:
            crypt = ctypes.WinDLL("crypt32", use_last_error=True)
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
            function.argtypes = [
                ctypes.POINTER(_Blob),
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_ulong,
                ctypes.POINTER(_Blob),
            ]
            function.restype = ctypes.c_int
            kernel.LocalFree.argtypes = [ctypes.c_void_p]
            kernel.LocalFree.restype = ctypes.c_void_p
            if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
                raise ProtectedStorageUnavailable("Windows protected key is unavailable.")
            try:
                return ctypes.string_at(output.data, output.size)
            finally:
                kernel.LocalFree(output.data)
        except (OSError, AttributeError) as exc:
            raise ProtectedStorageUnavailable("Windows protected key is unavailable.") from exc

    def protect(self, data: bytes) -> bytes:
        return self._apply(data, False)

    def unprotect(self, data: bytes) -> bytes:
        return self._apply(data, True)


class PersonalStore:
    """Separate database: only UUID, version and ciphertext are visible at rest."""

    def __init__(self, root: Path, protector: KeyProtector | None = None) -> None:
        self.root = root
        self.protector = protector or WindowsDPAPI()
        self._lock = threading.RLock()
        root.mkdir(parents=True, exist_ok=True)
        key_path = root / "personal.key.dpapi"
        try:
            if not key_path.exists():
                if (root / "personal.sqlite3").exists():
                    raise ProtectedStorageUnavailable(
                        "Existing personal storage has no protected key."
                    )
                protected = self.protector.protect(AESGCM.generate_key(bit_length=256))
                try:
                    with key_path.open("xb") as stream:
                        stream.write(protected)
                except FileExistsError:
                    pass
            key = self.protector.unprotect(key_path.read_bytes())
            if len(key) != 32:
                raise ProtectedStorageUnavailable("Invalid protected personal key.")
            self._cipher = AESGCM(key)
            with self._connect() as conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS personal_record "
                    "(id TEXT PRIMARY KEY, ciphertext BLOB NOT NULL) STRICT"
                )
        except ProtectedStorageUnavailable:
            raise
        except Exception as exc:
            raise ProtectedStorageUnavailable("Cannot unlock personal storage.") from exc

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.root / "personal.sqlite3")

    def put(self, record_id: str, payload: dict[str, Any]) -> None:
        UUID(record_id)
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("Personal record exceeds storage limit.")
        nonce = os.urandom(12)
        ciphertext = nonce + self._cipher.encrypt(nonce, raw, record_id.encode("ascii"))
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO personal_record VALUES (?, ?) ON CONFLICT(id) "
                "DO UPDATE SET ciphertext=excluded.ciphertext",
                (record_id, ciphertext),
            )

    def get(self, record_id: str) -> dict[str, Any] | None:
        UUID(record_id)
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT ciphertext FROM personal_record WHERE id=?", (record_id,)
            ).fetchone()
        if row is None:
            return None
        try:
            data = bytes(row[0])
            payload: object = json.loads(
                self._cipher.decrypt(data[:12], data[12:], record_id.encode("ascii"))
            )
            if not isinstance(payload, dict):
                raise ValueError("Invalid record")
            return payload
        except Exception as exc:
            raise ProtectedStorageUnavailable("Personal record authentication failed.") from exc
