"""Hardened HTTPS web companion for phone access over WireGuard.

Security model:
  - Bind to config ``web.host`` (default WireGuard LAN ``10.0.0.1``), never ``0.0.0.0``
  - Bearer / cookie token required on every request
  - HTTPS with self-signed certs under ``~/.motherbrain/certs/``
  - Rate limiting + security headers
  - Filesystem tools off by default (``web.allow_tools: false``)

Run via ``python web_companion.py`` (see that entrypoint for ``--dev``).
"""

from __future__ import annotations

import hmac
import json
import secrets
import ssl
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import context as companion_context
from . import flywheel
from . import inference
from . import paths
from . import sync as mb_sync
from . import vault_index
from .tools import extract_final_text, run_with_tools

# In-memory chat sessions: session_id -> {project_id, history, updated}
_SESSIONS: dict[str, dict[str, Any]] = {}
_SESSIONS_LOCK = threading.Lock()

# Rate limit: client_key -> list[timestamps]
_RATE: dict[str, list[float]] = {}
_RATE_LOCK = threading.Lock()
_RATE_WINDOW_S = 60.0
_RATE_MAX = 60

COOKIE_NAME = "mb_token"
DEFAULT_CERT = paths.CERTS_DIR / "web_cert.pem"
DEFAULT_KEY = paths.CERTS_DIR / "web_key.pem"

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cache-Control": "no-store",
}


def ensure_web_token(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Generate a random web.token on first run if missing; persist to config."""
    cfg = cfg or paths.load_config()
    web = dict(cfg.get("web") or {})
    token = (web.get("token") or "").strip()
    if not token:
        token = secrets.token_urlsafe(32)
        web["token"] = token
        cfg["web"] = web
        paths.save_config(cfg)
    return cfg


def _cert_paths(cfg: dict[str, Any]) -> tuple[Path, Path]:
    web = cfg.get("web") or {}
    cert = Path(web.get("tls_cert") or DEFAULT_CERT).expanduser()
    key = Path(web.get("tls_key") or DEFAULT_KEY).expanduser()
    if not cert.is_absolute():
        cert = paths.CERTS_DIR / cert
    if not key.is_absolute():
        key = paths.CERTS_DIR / key
    return cert, key


def ensure_tls_certs(cfg: dict[str, Any] | None = None) -> tuple[Path, Path]:
    """Create self-signed cert+key under ~/.motherbrain/certs/ if missing."""
    cfg = cfg or paths.load_config()
    paths.CERTS_DIR.mkdir(parents=True, exist_ok=True)
    cert, key = _cert_paths(cfg)
    if cert.is_file() and key.is_file():
        return cert, key

    host = str((cfg.get("web") or {}).get("host") or "10.0.0.1")
    if _gen_certs_cryptography(cert, key, host):
        return cert, key
    if _gen_certs_openssl(cert, key, host):
        return cert, key
    raise RuntimeError(
        "Could not generate TLS certs. Install the 'cryptography' package "
        "or ensure openssl is on PATH, then retry."
    )


def _gen_certs_cryptography(cert: Path, key: Path, host: str) -> bool:
    try:
        from ipaddress import ip_address

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        return False

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, f"motherbrain-{host}"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Motherbrain Local"),
        ]
    )
    san: list[x509.GeneralName] = [x509.DNSName("localhost"), x509.IPAddress(ip_address("127.0.0.1"))]
    try:
        san.append(x509.IPAddress(ip_address(host)))
    except ValueError:
        san.append(x509.DNSName(host))

    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .sign(private_key, hashes.SHA256())
    )
    key.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    try:
        key.chmod(0o600)
    except OSError:
        pass
    return True


def _gen_certs_openssl(cert: Path, key: Path, host: str) -> bool:
    try:
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-keyout",
                str(key),
                "-out",
                str(cert),
                "-days",
                "825",
                "-nodes",
                "-subj",
                f"/CN=motherbrain-{host}/O=Motherbrain Local",
                "-addext",
                f"subjectAltName=DNS:localhost,IP:127.0.0.1,IP:{host}",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        try:
            key.chmod(0o600)
        except OSError:
            pass
        return cert.is_file() and key.is_file()
    except (FileNotFoundError, subprocess.CalledProcessError, OSError):
        return False


def template_path() -> Path:
    return paths.bundle_dir() / "templates" / "web_companion.html"


def _rate_ok(client: str) -> bool:
    now = time.time()
    with _RATE_LOCK:
        bucket = [t for t in _RATE.get(client, []) if now - t < _RATE_WINDOW_S]
        if len(bucket) >= _RATE_MAX:
            _RATE[client] = bucket
            return False
        bucket.append(now)
        _RATE[client] = bucket
        return True


def _token_matches(provided: str, expected: str) -> bool:
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def _extract_token(handler: BaseHTTPRequestHandler) -> str:
    auth = handler.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    header_tok = handler.headers.get("X-Motherbrain-Token", "").strip()
    if header_tok:
        return header_tok
    cookie_hdr = handler.headers.get("Cookie", "")
    if cookie_hdr:
        jar = SimpleCookie()
        try:
            jar.load(cookie_hdr)
        except Exception:
            jar = SimpleCookie()
        if COOKIE_NAME in jar:
            return jar[COOKIE_NAME].value
    qs = parse_qs(urlparse(handler.path).query)
    if "token" in qs and qs["token"]:
        return qs["token"][0]
    return ""


def _session_get(sid: str) -> dict[str, Any]:
    with _SESSIONS_LOCK:
        sess = _SESSIONS.get(sid)
        if not sess:
            sess = {"project_id": None, "history": [], "updated": time.time()}
            _SESSIONS[sid] = sess
        return sess


def _list_projects() -> list[dict[str, str]]:
    vault_index.ensure_tables()
    db = vault_index.get_db()
    try:
        rows = db.execute("SELECT id, name FROM projects ORDER BY name").fetchall()
        return [{"id": str(r[0]), "name": str(r[1])} for r in rows]
    except Exception:
        return []
    finally:
        db.close()


def _ai_status() -> dict[str, Any]:
    cfg = paths.load_config()
    ready = False
    try:
        ready = inference.is_ready(cfg, timeout=1.0)
    except Exception:
        ready = False
    return {
        "ready": ready,
        "mode": (cfg.get("inference") or {}).get("mode", "local"),
        "url": paths.inference_base_url(cfg),
    }


def _sync_status() -> dict[str, Any]:
    try:
        health = mb_sync.SyncClient(timeout=1.5).health()
        return {"ok": True, "health": health, "server": paths.sync_server_url()}
    except Exception as e:
        return {"ok": False, "error": str(e), "server": paths.sync_server_url()}


def _run_chat(message: str, project_id: str | None, history: list[dict[str, str]]) -> str:
    cfg = paths.load_config()
    allow_tools = bool((cfg.get("web") or {}).get("allow_tools", False))
    prompt = companion_context.build_chat_prompt(
        message,
        project_id=project_id,
        history=history,
        history_limit=4,
        include_tools=allow_tools,
    )

    def _complete(p: str) -> str:
        return inference.complete(p, n_predict=512, cfg=cfg)

    if allow_tools:
        raw = run_with_tools(prompt, _complete, max_rounds=1)
        return extract_final_text(raw) or (raw or "").strip()
    return (_complete(prompt) or "").strip()


class WebCompanionHandler(BaseHTTPRequestHandler):
    server_version = "MotherbrainWeb/1.0"
    expected_token: str = ""
    dev_mode: bool = False

    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep logs short; never echo tokens.
        sys_stderr = __import__("sys").stderr
        print(f"[web] {self.address_string()} {fmt % args}", file=sys_stderr)

    def _client_key(self) -> str:
        return self.client_address[0] if self.client_address else "unknown"

    def _apply_security_headers(self) -> None:
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        if not self.dev_mode:
            self.send_header(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )

    def _send(
        self,
        code: int,
        body: bytes,
        content_type: str = "application/json",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._apply_security_headers()
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: Any, extra_headers: dict[str, str] | None = None) -> None:
        self._send(
            code,
            json.dumps(payload).encode("utf-8"),
            "application/json; charset=utf-8",
            extra_headers,
        )

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _auth_ok(self) -> bool:
        return _token_matches(_extract_token(self), self.expected_token)

    def _require_auth(self) -> bool:
        if not _rate_ok(self._client_key()):
            self._json(429, {"error": "rate_limited"})
            return False
        path = urlparse(self.path).path
        # Login page + login POST are the only unauthenticated routes.
        if path in ("/login", "/api/login") and self.command in ("GET", "POST", "HEAD"):
            return True
        if self._auth_ok():
            return True
        if path.startswith("/api/"):
            self._json(401, {"error": "unauthorized"})
        else:
            self._redirect("/login")
        return False

    def _redirect(self, location: str) -> None:
        body = b""
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self._apply_security_headers()
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self) -> None:
        self.do_GET(head_only=True)

    def do_GET(self, head_only: bool = False) -> None:
        if not self._require_auth():
            return
        path = urlparse(self.path).path

        if path in ("/", "/index.html"):
            self._serve_ui(head_only=head_only)
            return
        if path == "/login":
            self._serve_login(head_only=head_only)
            return
        if path == "/api/status":
            self._json(
                200,
                {
                    "ai": _ai_status(),
                    "sync": _sync_status(),
                    "allow_tools": bool(
                        (paths.load_config().get("web") or {}).get("allow_tools", False)
                    ),
                },
            )
            return
        if path == "/api/projects":
            self._json(200, {"projects": _list_projects()})
            return
        if path == "/health":
            # Still requires auth (no public probe).
            self._json(200, {"ok": True})
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if not self._require_auth():
            return
        path = urlparse(self.path).path
        data = self._read_json()

        if path == "/api/login":
            token = str(data.get("token") or "").strip()
            if not _token_matches(token, self.expected_token):
                self._json(401, {"error": "invalid_token"})
                return
            cookie = f"{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Strict"
            if not self.dev_mode:
                cookie += "; Secure"
            self._json(200, {"ok": True}, extra_headers={"Set-Cookie": cookie})
            return

        if path == "/api/logout":
            cookie = f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"
            self._json(200, {"ok": True}, extra_headers={"Set-Cookie": cookie})
            return

        if path == "/api/chat":
            message = str(data.get("message") or "").strip()
            if not message:
                self._json(400, {"error": "empty_message"})
                return
            sid = str(data.get("session_id") or "default")
            project_id = data.get("project_id")
            if project_id is not None:
                project_id = str(project_id).strip() or None
            sess = _session_get(sid)
            if "project_id" in data:
                sess["project_id"] = project_id
            else:
                project_id = sess.get("project_id")
            history = list(sess.get("history") or [])
            try:
                reply = _run_chat(message, project_id, history)
            except Exception as e:
                self._json(502, {"error": f"inference_failed: {e}"})
                return
            history.append({"user": message, "ai": reply})
            sess["history"] = history[-40:]
            sess["updated"] = time.time()
            try:
                flywheel.log_turn(message, reply, project_id)
            except Exception:
                pass
            self._json(
                200,
                {
                    "reply": reply,
                    "project_id": project_id,
                    "session_id": sid,
                },
            )
            return

        if path == "/api/project":
            sid = str(data.get("session_id") or "default")
            project_id = data.get("project_id")
            project_id = str(project_id).strip() if project_id else None
            sess = _session_get(sid)
            sess["project_id"] = project_id or None
            self._json(200, {"ok": True, "project_id": sess["project_id"]})
            return

        if path == "/api/sync":
            try:
                result = mb_sync.SyncClient().sync_all()
                self._json(200, {"ok": True, "result": result})
            except Exception as e:
                self._json(502, {"ok": False, "error": str(e)})
            return

        if path == "/api/clear":
            sid = str(data.get("session_id") or "default")
            sess = _session_get(sid)
            sess["history"] = []
            self._json(200, {"ok": True})
            return

        self._json(404, {"error": "not_found"})

    def _serve_ui(self, head_only: bool = False) -> None:
        path = template_path()
        if not path.is_file():
            self._json(500, {"error": f"template_missing: {path}"})
            return
        body = path.read_bytes()
        if head_only:
            self._send(200, b"", "text/html; charset=utf-8")
            return
        self._send(200, body, "text/html; charset=utf-8")

    def _serve_login(self, head_only: bool = False) -> None:
        html = LOGIN_HTML.encode("utf-8")
        if head_only:
            self._send(200, b"", "text/html; charset=utf-8")
            return
        self._send(200, html, "text/html; charset=utf-8")


LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Motherbrain Login</title>
<style>
  :root { color-scheme: dark; --bg:#121212; --fg:#e8e8e8; --accent:#50fa7b; --panel:#1e1e1e; --border:#333; }
  * { box-sizing: border-box; }
  body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         font-family: system-ui, -apple-system, Segoe UI, sans-serif; background:var(--bg); color:var(--fg); padding:24px; }
  form { width:100%; max-width:360px; background:var(--panel); border:1px solid var(--border); border-radius:12px; padding:24px; }
  h1 { margin:0 0 8px; font-size:1.25rem; color:var(--accent); }
  p { margin:0 0 16px; opacity:.7; font-size:.9rem; }
  input { width:100%; padding:14px; border-radius:8px; border:1px solid var(--border); background:#111; color:var(--fg); font-size:16px; }
  button { margin-top:12px; width:100%; padding:14px; border:0; border-radius:8px; background:var(--accent); color:#111; font-weight:700; font-size:16px; }
  .err { color:#ff6b6b; margin-top:10px; min-height:1.2em; font-size:.9rem; }
</style>
</head>
<body>
<form id="f">
  <h1>Motherbrain</h1>
  <p>Enter your companion token. Use only over WireGuard / trusted VPN.</p>
  <input id="token" type="password" autocomplete="current-password" placeholder="Token" required>
  <button type="submit">Unlock</button>
  <div class="err" id="err"></div>
</form>
<script>
document.getElementById('f').addEventListener('submit', async (e) => {
  e.preventDefault();
  const token = document.getElementById('token').value.trim();
  const err = document.getElementById('err');
  err.textContent = '';
  try {
    const r = await fetch('/api/login', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({token})
    });
    if (!r.ok) { err.textContent = 'Invalid token'; return; }
    location.href = '/';
  } catch (ex) { err.textContent = 'Network error'; }
});
</script>
</body>
</html>
"""


def make_handler(token: str, dev_mode: bool) -> type[WebCompanionHandler]:
    class BoundHandler(WebCompanionHandler):
        expected_token = token
        # copy class attrs for clarity
        pass

    BoundHandler.dev_mode = dev_mode
    BoundHandler.expected_token = token
    return BoundHandler


def run_server(
    *,
    host: str | None = None,
    port: int | None = None,
    dev: bool = False,
) -> None:
    """Start the web companion (HTTPS unless ``dev`` on localhost)."""
    paths.ensure_dirs()
    vault_index.ensure_tables()
    cfg = ensure_web_token()
    web = dict(cfg.get("web") or {})

    bind_port = int(port if port is not None else web.get("port") or 8443)
    token = str(web.get("token") or "").strip()
    if not token:
        raise RuntimeError("web.token missing after ensure_web_token()")

    if dev:
        # Force loopback for plain HTTP; ignore VPN host from config.
        if host is not None and host not in ("127.0.0.1", "localhost", "::1"):
            raise RuntimeError("--dev only allows HTTP on 127.0.0.1 / localhost")
        bind_host = "127.0.0.1"
    else:
        bind_host = host if host is not None else str(web.get("host") or "10.0.0.1")

    if bind_host in ("0.0.0.0", "::", "[::]"):
        raise RuntimeError(
            "Refusing to bind web companion to a public all-interfaces address. "
            "Set web.host to your WireGuard IP (e.g. 10.0.0.1) or 127.0.0.1."
        )

    if dev:
        scheme = "http"
        httpd = ThreadingHTTPServer((bind_host, bind_port), make_handler(token, True))
    else:
        cert, key = ensure_tls_certs(cfg)
        # Persist resolved cert paths if empty in config (paths only, not secrets in repo).
        if not (web.get("tls_cert") and web.get("tls_key")):
            web["tls_cert"] = str(cert)
            web["tls_key"] = str(key)
            cfg["web"] = web
            paths.save_config(cfg)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(certfile=str(cert), keyfile=str(key))
        httpd = ThreadingHTTPServer((bind_host, bind_port), make_handler(token, False))
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        scheme = "https"

    # Fingerprint for phone trust prompt (do not print the auth token in full).
    token_hint = token[:4] + "…" + token[-4:] if len(token) > 8 else "(set)"
    print(f"Motherbrain web companion listening on {scheme}://{bind_host}:{bind_port}", flush=True)
    print(f"Auth token hint: {token_hint}  (full token in {paths.CONFIG_PATH})", flush=True)
    print(
        "Security: VPN/LAN bind + bearer/cookie token + HTTPS"
        + (" [DEV HTTP]" if dev else ""),
        flush=True,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        httpd.server_close()
