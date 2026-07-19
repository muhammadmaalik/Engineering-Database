"""Device identities and signed peer-to-peer request authentication."""

from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import stat
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode

from cryptography.fernet import Fernet
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from . import paths

AUTH_DIR_NAME = "peer_auth"
CONNECTION_KEY_PREFIX = "OC1-"
PAIRING_TTL = 120
MAX_CLOCK_SKEW = 120
DEFAULT_SCOPES = ("sync:read", "sync:write")

DEVICE_HEADER = "X-MB-Device-ID"
TIMESTAMP_HEADER = "X-MB-Timestamp"
NONCE_HEADER = "X-MB-Nonce"
HASH_HEADER = "X-MB-Content-SHA256"
SIGNATURE_HEADER = "X-MB-Signature"


class PeerAuthError(ValueError):
    """Authentication, trust, or pairing validation failed."""


@dataclass(frozen=True)
class DeviceIdentity:
    device_id: str
    name: str
    private_key: Ed25519PrivateKey

    @property
    def public_key_bytes(self) -> bytes:
        return self.private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )

    @property
    def public_key_b64(self) -> str:
        return _b64(self.public_key_bytes)


@dataclass(frozen=True)
class TrustedPeer:
    device_id: str
    name: str
    public_key: str
    scopes: tuple[str, ...]
    added_at: int


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.chmod(temp_name, mode)
        except OSError:
            pass
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(value: bytes, protect: bool) -> bytes | None:
    if os.name != "nt":
        return None
    try:
        source_buffer = ctypes.create_string_buffer(value)
        source = _DATA_BLOB(len(value), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
        output = _DATA_BLOB()
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        blob_pointer = ctypes.POINTER(_DATA_BLOB)
        if protect:
            function = crypt32.CryptProtectData
            function.argtypes = [
                blob_pointer,
                ctypes.c_wchar_p,
                blob_pointer,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_ulong,
                blob_pointer,
            ]
            function.restype = ctypes.c_int
            ok = function(ctypes.byref(source), "Motherbrain peer identity", None, None, None, 0, ctypes.byref(output))
        else:
            function = crypt32.CryptUnprotectData
            function.argtypes = [
                blob_pointer,
                ctypes.POINTER(ctypes.c_wchar_p),
                blob_pointer,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_ulong,
                blob_pointer,
            ]
            function.restype = ctypes.c_int
            ok = function(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(output))
        if not ok:
            return None
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            kernel32.LocalFree.argtypes = [ctypes.c_void_p]
            kernel32.LocalFree.restype = ctypes.c_void_p
            kernel32.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))
    except (AttributeError, OSError, ValueError):
        return None


class IdentityStore:
    """Persistent identity, trust store, and replay database outside the vault."""

    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root is not None else paths.MOTHERBRAIN_DIR / AUTH_DIR_NAME
        self.identity_path = self.root / "identity.json"
        self.trusted_path = self.root / "trusted_peers.json"
        self.replay_path = self.root / "replay.sqlite3"
        self.fallback_key_path = self.root / ".identity.key"
        self.root.mkdir(parents=True, exist_ok=True)

    def load_or_create_identity(self, name: str | None = None) -> DeviceIdentity:
        if self.identity_path.exists():
            return self.load_identity()
        private_key = Ed25519PrivateKey.generate()
        identity = DeviceIdentity(
            device_id=uuid.uuid4().hex,
            name=(name or os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "device")[:128],
            private_key=private_key,
        )
        raw = private_key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        protected, protection = self._protect(raw)
        record = {
            "version": 1,
            "device_id": identity.device_id,
            "name": identity.name,
            "public_key": identity.public_key_b64,
            "private_key": _b64(protected),
            "protection": protection,
        }
        _atomic_write(self.identity_path, (json.dumps(record, sort_keys=True, indent=2) + "\n").encode())
        return identity

    def load_identity(self) -> DeviceIdentity:
        try:
            record = json.loads(self.identity_path.read_text(encoding="utf-8"))
            raw = self._unprotect(_unb64(record["private_key"]), record["protection"])
            private_key = Ed25519PrivateKey.from_private_bytes(raw)
            identity = DeviceIdentity(str(record["device_id"]), str(record["name"]), private_key)
            if not hmac.compare_digest(identity.public_key_b64, str(record["public_key"])):
                raise PeerAuthError("identity public key mismatch")
            return identity
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            if isinstance(exc, PeerAuthError):
                raise
            raise PeerAuthError("could not load device identity") from exc

    def rename_identity(self, name: str) -> DeviceIdentity:
        clean = (name or "").strip()[:128]
        if not clean:
            raise ValueError("device name is required")
        identity = self.load_or_create_identity()
        record = json.loads(self.identity_path.read_text(encoding="utf-8"))
        record["name"] = clean
        _atomic_write(self.identity_path, (json.dumps(record, sort_keys=True, indent=2) + "\n").encode())
        return DeviceIdentity(identity.device_id, clean, identity.private_key)

    def _fallback_key(self) -> bytes:
        if self.fallback_key_path.exists():
            key = self.fallback_key_path.read_bytes()
        else:
            key = Fernet.generate_key()
            _atomic_write(self.fallback_key_path, key)
        try:
            os.chmod(self.fallback_key_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        return key

    def _protect(self, raw: bytes) -> tuple[bytes, str]:
        protected = _dpapi(raw, True)
        if protected is not None:
            return protected, "dpapi"
        return Fernet(self._fallback_key()).encrypt(raw), "fernet"

    def _unprotect(self, protected: bytes, protection: str) -> bytes:
        if protection == "dpapi":
            raw = _dpapi(protected, False)
            if raw is None:
                raise PeerAuthError("Windows DPAPI could not decrypt the identity")
            return raw
        if protection == "fernet":
            return Fernet(self._fallback_key()).decrypt(protected)
        raise PeerAuthError("unsupported identity protection")

    def list_trusted_peers(self) -> dict[str, TrustedPeer]:
        if not self.trusted_path.exists():
            return {}
        try:
            raw = json.loads(self.trusted_path.read_text(encoding="utf-8"))
            return {
                device_id: TrustedPeer(
                    device_id=device_id,
                    name=str(peer.get("name") or device_id),
                    public_key=str(peer["public_key"]),
                    scopes=tuple(str(scope) for scope in peer.get("scopes", [])),
                    added_at=int(peer.get("added_at", 0)),
                )
                for device_id, peer in raw.items()
            }
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise PeerAuthError("could not load trusted peers") from exc

    def trust_peer(
        self,
        device_id: str,
        public_key: str,
        name: str = "",
        scopes: Iterable[str] = DEFAULT_SCOPES,
    ) -> TrustedPeer:
        _validate_public_key(public_key)
        peers = self.list_trusted_peers()
        peer = TrustedPeer(
            device_id=device_id,
            name=(name or device_id)[:128],
            public_key=public_key,
            scopes=tuple(sorted(set(scopes))),
            added_at=int(time.time()),
        )
        peers[device_id] = peer
        self._save_peers(peers)
        return peer

    def revoke_peer(self, device_id: str) -> bool:
        peers = self.list_trusted_peers()
        removed = peers.pop(device_id, None) is not None
        if removed:
            self._save_peers(peers)
        return removed

    def set_peer_scopes(self, device_id: str, scopes: Iterable[str]) -> TrustedPeer:
        peers = self.list_trusted_peers()
        if device_id not in peers:
            raise PeerAuthError("unknown peer")
        old = peers[device_id]
        peer = TrustedPeer(old.device_id, old.name, old.public_key, tuple(sorted(set(scopes))), old.added_at)
        peers[device_id] = peer
        self._save_peers(peers)
        return peer

    def _save_peers(self, peers: dict[str, TrustedPeer]) -> None:
        payload = {
            device_id: {
                "name": peer.name,
                "public_key": peer.public_key,
                "scopes": list(peer.scopes),
                "added_at": peer.added_at,
            }
            for device_id, peer in peers.items()
        }
        _atomic_write(self.trusted_path, (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode())

    def consume_nonce(self, device_id: str, nonce: str, timestamp: int, now: int | None = None) -> None:
        now = int(time.time()) if now is None else int(now)
        if abs(now - timestamp) > MAX_CLOCK_SKEW:
            raise PeerAuthError("request timestamp outside allowed clock skew")
        try:
            nonce_bytes = bytes.fromhex(nonce)
        except ValueError as exc:
            raise PeerAuthError("invalid nonce") from exc
        if len(nonce_bytes) != 16:
            raise PeerAuthError("nonce must be 128 bits")
        with sqlite3.connect(self.replay_path, timeout=10) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS nonces "
                "(device_id TEXT NOT NULL, nonce TEXT NOT NULL, seen_at INTEGER NOT NULL, "
                "PRIMARY KEY(device_id, nonce))"
            )
            db.execute("DELETE FROM nonces WHERE seen_at < ?", (now - MAX_CLOCK_SKEW * 2,))
            try:
                db.execute("INSERT INTO nonces VALUES (?, ?, ?)", (device_id, nonce, now))
                db.commit()
            except sqlite3.IntegrityError as exc:
                raise PeerAuthError("replayed request") from exc


def _validate_public_key(value: str) -> None:
    try:
        Ed25519PublicKey.from_public_bytes(_unb64(value))
    except (ValueError, TypeError) as exc:
        raise PeerAuthError("invalid Ed25519 public key") from exc


def canonical_query(query: str) -> str:
    return urlencode(sorted(parse_qsl(query, keep_blank_values=True)), doseq=True)


def body_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def canonical_request(method: str, path: str, query: str, body_digest: str, timestamp: int, nonce: str) -> bytes:
    return "\n".join(
        (method.upper(), path or "/", canonical_query(query), body_digest.lower(), str(timestamp), nonce.lower())
    ).encode("utf-8")


def sign_request(
    identity: DeviceIdentity,
    method: str,
    path: str,
    query: str = "",
    body: bytes = b"",
    *,
    timestamp: int | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    timestamp = int(time.time()) if timestamp is None else int(timestamp)
    nonce = nonce or secrets.token_hex(16)
    digest = body_hash(body)
    signature = identity.private_key.sign(canonical_request(method, path, query, digest, timestamp, nonce))
    return {
        DEVICE_HEADER: identity.device_id,
        TIMESTAMP_HEADER: str(timestamp),
        NONCE_HEADER: nonce,
        HASH_HEADER: digest,
        SIGNATURE_HEADER: _b64(signature),
    }


def verify_request(
    store: IdentityStore,
    method: str,
    path: str,
    query: str,
    body: bytes,
    headers: Any,
    required_scope: str | None = None,
    *,
    now: int | None = None,
) -> TrustedPeer:
    try:
        device_id = str(headers[DEVICE_HEADER])
        timestamp = int(headers[TIMESTAMP_HEADER])
        nonce = str(headers[NONCE_HEADER])
        supplied_hash = str(headers[HASH_HEADER])
        signature = _unb64(headers[SIGNATURE_HEADER])
    except (KeyError, TypeError, ValueError) as exc:
        raise PeerAuthError("missing or invalid authentication headers") from exc
    digest = body_hash(body)
    if not hmac.compare_digest(digest, supplied_hash.lower()):
        raise PeerAuthError("request body hash mismatch")
    peer = store.list_trusted_peers().get(device_id)
    if peer is None:
        raise PeerAuthError("untrusted peer")
    if required_scope and required_scope not in peer.scopes:
        raise PeerAuthError(f"peer lacks required scope: {required_scope}")
    try:
        Ed25519PublicKey.from_public_bytes(_unb64(peer.public_key)).verify(
            signature,
            canonical_request(method, path, query, digest, timestamp, nonce),
        )
    except (InvalidSignature, ValueError) as exc:
        raise PeerAuthError("invalid request signature") from exc
    store.consume_nonce(device_id, nonce, timestamp, now)
    return peer


def canonical_response(status: int, body_digest: str, timestamp: int, request_nonce: str) -> bytes:
    return "\n".join((str(status), body_digest.lower(), str(timestamp), request_nonce.lower())).encode()


def sign_response(
    identity: DeviceIdentity,
    status: int,
    body: bytes,
    request_nonce: str,
    *,
    timestamp: int | None = None,
) -> dict[str, str]:
    timestamp = int(time.time()) if timestamp is None else int(timestamp)
    digest = body_hash(body)
    signature = identity.private_key.sign(canonical_response(status, digest, timestamp, request_nonce))
    return {
        DEVICE_HEADER: identity.device_id,
        TIMESTAMP_HEADER: str(timestamp),
        NONCE_HEADER: request_nonce,
        HASH_HEADER: digest,
        SIGNATURE_HEADER: _b64(signature),
    }


def verify_response(
    peer: TrustedPeer,
    status: int,
    body: bytes,
    request_nonce: str,
    headers: Any,
    *,
    now: int | None = None,
) -> None:
    now = int(time.time()) if now is None else int(now)
    try:
        timestamp = int(headers[TIMESTAMP_HEADER])
        nonce = headers[NONCE_HEADER]
        digest = headers[HASH_HEADER]
        signature = _unb64(headers[SIGNATURE_HEADER])
    except (KeyError, TypeError, ValueError) as exc:
        raise PeerAuthError("missing or invalid signed response headers") from exc
    if headers.get(DEVICE_HEADER) != peer.device_id or nonce != request_nonce:
        raise PeerAuthError("response peer or nonce mismatch")
    if abs(now - timestamp) > MAX_CLOCK_SKEW or not hmac.compare_digest(digest.lower(), body_hash(body)):
        raise PeerAuthError("invalid response timestamp or body hash")
    try:
        Ed25519PublicKey.from_public_bytes(_unb64(peer.public_key)).verify(
            signature, canonical_response(status, digest, timestamp, nonce)
        )
    except (InvalidSignature, ValueError) as exc:
        raise PeerAuthError("invalid response signature") from exc


def create_pairing_bundle(identity: DeviceIdentity, *, now: int | None = None) -> dict[str, Any]:
    now = int(time.time()) if now is None else int(now)
    payload: dict[str, Any] = {
        "version": 1,
        "device_id": identity.device_id,
        "name": identity.name,
        "public_key": identity.public_key_b64,
        "pairing_key": _b64(secrets.token_bytes(32)),
        "issued_at": now,
        "expires_at": now + PAIRING_TTL,
    }
    packed = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["signature"] = _b64(identity.private_key.sign(packed))
    payload["sas"] = pairing_sas(payload)
    return payload


def pairing_sas(bundle: dict[str, Any]) -> str:
    material = "|".join(
        str(bundle[key]) for key in ("device_id", "public_key", "pairing_key", "expires_at")
    ).encode()
    return f"{int.from_bytes(hashlib.sha256(material).digest()[:4], 'big') % 100_000_000:08d}"


def confirm_pairing(
    store: IdentityStore,
    bundle: dict[str, Any],
    sas: str,
    scopes: Iterable[str] = DEFAULT_SCOPES,
    *,
    now: int | None = None,
) -> TrustedPeer:
    now = int(time.time()) if now is None else int(now)
    try:
        issued_at = int(bundle["issued_at"])
        expires_at = int(bundle["expires_at"])
        public_key = str(bundle["public_key"])
        signature = _unb64(str(bundle["signature"]))
        signed = {key: value for key, value in bundle.items() if key not in {"signature", "sas"}}
        packed = json.dumps(signed, sort_keys=True, separators=(",", ":")).encode()
        Ed25519PublicKey.from_public_bytes(_unb64(public_key)).verify(signature, packed)
    except (InvalidSignature, KeyError, TypeError, ValueError) as exc:
        raise PeerAuthError("invalid pairing bundle") from exc
    if expires_at - issued_at != PAIRING_TTL or now < issued_at - MAX_CLOCK_SKEW or now > expires_at:
        raise PeerAuthError("pairing bundle expired")
    if not hmac.compare_digest(str(sas), pairing_sas(bundle)):
        raise PeerAuthError("pairing SAS does not match")
    return store.trust_peer(str(bundle["device_id"]), public_key, str(bundle.get("name") or ""), scopes)


def revoke_peer(store: IdentityStore, device_id: str) -> bool:
    return store.revoke_peer(device_id)


def set_peer_scopes(store: IdentityStore, device_id: str, scopes: Iterable[str]) -> TrustedPeer:
    return store.set_peer_scopes(device_id, scopes)


def _signed_identity_payload(identity: DeviceIdentity, payload: dict[str, Any]) -> dict[str, Any]:
    signed = dict(payload)
    packed = json.dumps(signed, sort_keys=True, separators=(",", ":")).encode()
    signed["signature"] = _b64(identity.private_key.sign(packed))
    return signed


def _verify_identity_payload(payload: dict[str, Any], public_key: str) -> None:
    try:
        signature = _unb64(str(payload["signature"]))
        unsigned = {key: value for key, value in payload.items() if key != "signature"}
        packed = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
        Ed25519PublicKey.from_public_bytes(_unb64(public_key)).verify(signature, packed)
    except (InvalidSignature, KeyError, TypeError, ValueError) as exc:
        raise PeerAuthError("invalid pairing identity signature") from exc


def encode_connection_key(
    identity: DeviceIdentity,
    server_url: str,
    *,
    now: int | None = None,
) -> tuple[str, dict[str, Any]]:
    """Create a signed, two-minute host invitation suitable for text or QR."""
    now = int(time.time()) if now is None else int(now)
    payload = _signed_identity_payload(
        identity,
        {
            "version": 1,
            "session_id": secrets.token_hex(12),
            "secret": _b64(secrets.token_bytes(24)),
            "server_url": server_url.rstrip("/"),
            "host_device_id": identity.device_id,
            "host_name": identity.name,
            "host_public_key": identity.public_key_b64,
            "issued_at": now,
            "expires_at": now + PAIRING_TTL,
        },
    )
    packed = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return CONNECTION_KEY_PREFIX + _b64(packed), payload


def decode_connection_key(key: str, *, now: int | None = None) -> dict[str, Any]:
    now = int(time.time()) if now is None else int(now)
    if not str(key).startswith(CONNECTION_KEY_PREFIX):
        raise PeerAuthError("invalid connection key prefix")
    try:
        payload = json.loads(_unb64(str(key)[len(CONNECTION_KEY_PREFIX):]).decode("utf-8"))
        _verify_identity_payload(payload, str(payload["host_public_key"]))
        issued_at = int(payload["issued_at"])
        expires_at = int(payload["expires_at"])
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, PeerAuthError):
            raise
        raise PeerAuthError("invalid connection key") from exc
    if expires_at - issued_at != PAIRING_TTL or now < issued_at - MAX_CLOCK_SKEW or now > expires_at:
        raise PeerAuthError("connection key expired")
    return payload


def create_join_request(
    identity: DeviceIdentity,
    invitation: dict[str, Any],
) -> dict[str, Any]:
    return _signed_identity_payload(
        identity,
        {
            "session_id": invitation["session_id"],
            "secret": invitation["secret"],
            "guest_device_id": identity.device_id,
            "guest_name": identity.name,
            "guest_public_key": identity.public_key_b64,
            "expires_at": invitation["expires_at"],
        },
    )


def pairing_verification_code(invitation: dict[str, Any], guest_public_key: str) -> str:
    material = "|".join(
        (
            str(invitation["session_id"]),
            str(invitation["secret"]),
            str(invitation["host_public_key"]),
            str(guest_public_key),
        )
    ).encode()
    return f"{int.from_bytes(hashlib.sha256(material).digest()[:4], 'big') % 100_000_000:08d}"


class PairingSessionStore:
    """Atomic two-party confirmation state used by the sync server."""

    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root is not None else paths.MOTHERBRAIN_DIR / AUTH_DIR_NAME
        self.path = self.root / "pairing_sessions.json"

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self, sessions: dict[str, dict[str, Any]]) -> None:
        _atomic_write(self.path, (json.dumps(sessions, sort_keys=True, indent=2) + "\n").encode())

    def open(self, invitation: dict[str, Any]) -> None:
        sessions = self._load()
        sessions[str(invitation["session_id"])] = {
            "invitation": invitation,
            "guest": None,
            "host_confirmed": False,
            "guest_confirmed": False,
            "created_at": int(time.time()),
        }
        self._save(sessions)

    def join(self, request: dict[str, Any], *, now: int | None = None) -> dict[str, Any]:
        now = int(time.time()) if now is None else int(now)
        sessions = self._load()
        session = sessions.get(str(request.get("session_id")))
        if not session:
            raise PeerAuthError("pairing window not found")
        invitation = session["invitation"]
        if now > int(invitation["expires_at"]):
            raise PeerAuthError("pairing window expired")
        if not hmac.compare_digest(str(request.get("secret", "")), str(invitation["secret"])):
            raise PeerAuthError("invalid connection key")
        _verify_identity_payload(request, str(request.get("guest_public_key", "")))
        session["guest"] = request
        session["sas"] = pairing_verification_code(invitation, str(request["guest_public_key"]))
        sessions[str(request["session_id"])] = session
        self._save(sessions)
        return self.public_status(str(request["session_id"]), str(request["secret"]), now=now)

    def public_status(self, session_id: str, secret: str, *, now: int | None = None) -> dict[str, Any]:
        now = int(time.time()) if now is None else int(now)
        session = self._load().get(session_id)
        if not session or not hmac.compare_digest(str(session["invitation"]["secret"]), str(secret)):
            raise PeerAuthError("pairing window not found")
        if now > int(session["invitation"]["expires_at"]):
            raise PeerAuthError("pairing window expired")
        invitation = session["invitation"]
        return {
            "session_id": session_id,
            "expires_at": invitation["expires_at"],
            "sas": session.get("sas"),
            "guest_name": (session.get("guest") or {}).get("guest_name"),
            "host_confirmed": bool(session.get("host_confirmed")),
            "guest_confirmed": bool(session.get("guest_confirmed")),
            "complete": bool(session.get("host_confirmed") and session.get("guest_confirmed")),
            "host": {
                "device_id": invitation["host_device_id"],
                "name": invitation["host_name"],
                "public_key": invitation["host_public_key"],
            },
        }

    def confirm(
        self,
        session_id: str,
        secret: str,
        side: str,
        sas: str,
        *,
        now: int | None = None,
    ) -> dict[str, Any]:
        sessions = self._load()
        session = sessions.get(session_id)
        if not session:
            raise PeerAuthError("pairing window not found")
        status = self.public_status(session_id, secret, now=now)
        if not session.get("guest") or not status.get("sas"):
            raise PeerAuthError("the other device has not joined")
        if not hmac.compare_digest(str(status["sas"]), str(sas)):
            raise PeerAuthError("verification code does not match")
        if side not in {"host", "guest"}:
            raise PeerAuthError("pairing confirmation side must be host or guest")
        session[f"{side}_confirmed"] = True
        sessions[session_id] = session
        self._save(sessions)
        return self.public_status(session_id, secret, now=now)

    def completed_guest(self, session_id: str, secret: str) -> dict[str, Any] | None:
        sessions = self._load()
        session = sessions.get(session_id)
        if not session or not hmac.compare_digest(str(session["invitation"]["secret"]), str(secret)):
            raise PeerAuthError("pairing window not found")
        if session.get("host_confirmed") and session.get("guest_confirmed"):
            return dict(session["guest"])
        return None
