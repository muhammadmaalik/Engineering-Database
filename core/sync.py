"""Client API for the Motherbrain vault sync server (:8090)."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

import requests

from . import paths


def file_sha256(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def local_inventory(vault_root: Path | None = None) -> dict[str, dict[str, Any]]:
    """Build inventory for sync roots (path -> {mtime, size, sha256})."""
    root = vault_root or paths.VAULT_ROOT
    inv: dict[str, dict[str, Any]] = {}
    for rel_root in paths.SYNC_ROOTS:
        base = root / rel_root
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            # Skip huge GGUFs even if somehow under a sync root.
            if p.suffix.lower() == ".gguf":
                continue
            rel = p.relative_to(root).as_posix()
            st = p.stat()
            inv[rel] = {
                "mtime": st.st_mtime,
                "size": st.st_size,
                "sha256": file_sha256(p),
            }
    return inv


def diff_inventories(
    local: dict[str, dict[str, Any]],
    remote: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    """Compute pull/push/conflict sets. Newer mtime wins; same mtime+hash = skip."""
    pull: list[str] = []
    push: list[str] = []
    conflicts: list[str] = []
    identical: list[str] = []

    all_paths = set(local) | set(remote)
    for rel in sorted(all_paths):
        L = local.get(rel)
        R = remote.get(rel)
        if L and not R:
            push.append(rel)
        elif R and not L:
            pull.append(rel)
        elif L and R:
            if L.get("sha256") == R.get("sha256"):
                identical.append(rel)
                continue
            lm, rm = float(L.get("mtime", 0)), float(R.get("mtime", 0))
            # Treat near-equal mtimes with different hashes as conflicts.
            if abs(lm - rm) < 1e-3:
                conflicts.append(rel)
            elif rm > lm:
                pull.append(rel)
            elif lm > rm:
                push.append(rel)
            else:
                conflicts.append(rel)
    return {
        "pull": pull,
        "push": push,
        "conflicts": conflicts,
        "identical": identical,
    }


class SyncClient:
    """HTTP client for sync_server.py."""

    def __init__(
        self,
        server_url: str | None = None,
        token: str | None = None,
        timeout: float = 60.0,
    ):
        cfg = paths.load_config()
        sync_cfg = cfg.get("sync") or {}
        self.server_url = (server_url or sync_cfg.get("server_url") or "http://10.0.0.1:8090").rstrip("/")
        self.token = token if token is not None else (sync_cfg.get("token") or "")
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def health(self) -> dict[str, Any]:
        r = requests.get(f"{self.server_url}/health", headers=self._headers(), timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def remote_manifest(self) -> dict[str, dict[str, Any]]:
        r = requests.get(f"{self.server_url}/manifest", headers=self._headers(), timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        files = data.get("files", data)
        if not isinstance(files, dict):
            raise ValueError("Invalid manifest response")
        return files

    def get_file(self, rel: str, dest: Path | None = None) -> Path:
        r = requests.get(
            f"{self.server_url}/file",
            params={"path": rel},
            headers=self._headers(),
            timeout=self.timeout,
        )
        r.raise_for_status()
        out = dest or paths.vault_abs(rel)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(r.content)
        # Best-effort mtime restore from header.
        mtime = r.headers.get("X-Vault-Mtime")
        if mtime:
            try:
                ts = float(mtime)
                import os

                os.utime(out, (ts, ts))
            except (TypeError, ValueError, OSError):
                pass
        return out

    def put_file(self, rel: str, src: Path | None = None) -> dict[str, Any]:
        path = src or paths.vault_abs(rel)
        headers = self._headers()
        headers["X-Vault-Path"] = rel
        headers["X-Vault-Mtime"] = str(path.stat().st_mtime)
        r = requests.put(
            f"{self.server_url}/file",
            params={"path": rel},
            data=path.read_bytes(),
            headers=headers,
            timeout=self.timeout,
        )
        r.raise_for_status()
        try:
            return r.json()
        except Exception:
            return {"ok": True, "path": rel}

    def pull(self, paths_list: list[str] | None = None) -> dict[str, Any]:
        """Batch pull. If paths_list is None, pull by inventory diff."""
        if paths_list is None:
            local = local_inventory()
            remote = self.remote_manifest()
            paths_list = diff_inventories(local, remote)["pull"]
        body = {"paths": paths_list}
        r = requests.post(
            f"{self.server_url}/sync/pull",
            json=body,
            headers=self._headers(),
            timeout=max(self.timeout, 120.0),
        )
        r.raise_for_status()
        data = r.json()
        written = []
        for item in data.get("files", []):
            rel = item["path"]
            content_b64 = item.get("content_b64")
            if content_b64 is None and "content" in item:
                # Server may return raw text for small files.
                dest = paths.vault_abs(rel)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(item["content"].encode("utf-8") if isinstance(item["content"], str) else item["content"])
            else:
                import base64

                dest = paths.vault_abs(rel)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(base64.b64decode(content_b64 or b""))
            mtime = item.get("mtime")
            if mtime is not None:
                try:
                    import os

                    os.utime(dest, (float(mtime), float(mtime)))
                except (TypeError, ValueError, OSError):
                    pass
            written.append(rel)
        return {"pulled": written, "count": len(written)}

    def push(self, paths_list: list[str] | None = None) -> dict[str, Any]:
        """Batch push. If paths_list is None, push by inventory diff."""
        import base64

        if paths_list is None:
            local = local_inventory()
            remote = self.remote_manifest()
            paths_list = diff_inventories(local, remote)["push"]

        files = []
        for rel in paths_list:
            path = paths.vault_abs(rel)
            if not path.is_file():
                continue
            st = path.stat()
            files.append(
                {
                    "path": rel,
                    "mtime": st.st_mtime,
                    "size": st.st_size,
                    "sha256": file_sha256(path),
                    "content_b64": base64.b64encode(path.read_bytes()).decode("ascii"),
                }
            )
        r = requests.post(
            f"{self.server_url}/sync/push",
            json={"files": files},
            headers=self._headers(),
            timeout=max(self.timeout, 120.0),
        )
        r.raise_for_status()
        return r.json()

    def sync_all(self) -> dict[str, Any]:
        """Pull then push using inventory diff (newer wins; conflicts keep both)."""
        local = local_inventory()
        remote = self.remote_manifest()
        diff = diff_inventories(local, remote)

        # Conflicts: pull remote into .conflict-<ts>, then push local as primary.
        conflict_results = []
        ts = int(time.time())
        for rel in diff["conflicts"]:
            conflict_rel = _conflict_name(rel, ts)
            try:
                self.get_file(rel, dest=paths.vault_abs(conflict_rel))
                conflict_results.append({"path": rel, "saved_as": conflict_rel})
            except Exception as e:
                conflict_results.append({"path": rel, "error": str(e)})

        pull_result = self.pull(diff["pull"]) if diff["pull"] else {"pulled": [], "count": 0}
        # After conflicts, push our local version as the canonical path.
        push_paths = list(diff["push"]) + list(diff["conflicts"])
        push_result = self.push(push_paths) if push_paths else {"pushed": [], "count": 0}

        return {
            "diff": {k: v for k, v in diff.items() if k != "identical"},
            "identical_count": len(diff["identical"]),
            "pull": pull_result,
            "push": push_result,
            "conflicts": conflict_results,
        }


def _conflict_name(rel: str, ts: int | None = None) -> str:
    ts = ts or int(time.time())
    p = Path(rel)
    return f"{p.with_suffix('')}.conflict-{ts}{p.suffix}".replace("\\", "/")


# Module-level convenience wrappers
def sync_now(**kwargs: Any) -> dict[str, Any]:
    return SyncClient(**kwargs).sync_all()
