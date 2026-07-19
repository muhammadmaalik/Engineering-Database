"""Safe, resumable GGUF downloads and local imports."""

from __future__ import annotations

import hashlib
import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import requests

from . import model_catalog, models, paths

ProgressCallback = Callable[[int, int | None], None]


class DownloadCancelled(RuntimeError):
    pass


@dataclass
class DownloadJob:
    id: str
    repo_id: str
    filename: str
    revision: str
    status: str = "queued"
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    result_path: str | None = None
    error: str | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    thread: threading.Thread | None = field(default=None, repr=False)

    @property
    def progress(self) -> float | None:
        if not self.total_bytes:
            return None
        return min(1.0, self.downloaded_bytes / self.total_bytes)

    def cancel(self) -> None:
        self.cancel_event.set()

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "repo_id": self.repo_id,
            "filename": self.filename,
            "revision": self.revision,
            "status": self.status,
            "downloaded_bytes": self.downloaded_bytes,
            "total_bytes": self.total_bytes,
            "progress": self.progress,
            "result_path": self.result_path,
            "error": self.error,
        }


class DownloadManager:
    def __init__(self) -> None:
        self._jobs: dict[str, DownloadJob] = {}
        self._lock = threading.Lock()

    def start(
        self,
        *,
        repo_id: str,
        filename: str,
        revision: str,
        expected_size: int | None = None,
        expected_sha256: str | None = None,
        metadata: dict[str, Any] | None = None,
        progress: ProgressCallback | None = None,
    ) -> DownloadJob:
        job = DownloadJob(
            id=uuid.uuid4().hex,
            repo_id=repo_id,
            filename=filename,
            revision=revision,
        )
        with self._lock:
            self._jobs[job.id] = job

        def run() -> None:
            job.status = "downloading"

            def on_progress(done: int, total: int | None) -> None:
                job.downloaded_bytes = done
                job.total_bytes = total
                if progress:
                    progress(done, total)

            try:
                path = download_gguf(
                    repo_id=repo_id,
                    filename=filename,
                    revision=revision,
                    expected_size=expected_size,
                    expected_sha256=expected_sha256,
                    metadata=metadata,
                    cancel_event=job.cancel_event,
                    progress=on_progress,
                )
                job.result_path = str(path)
                job.status = "completed"
            except DownloadCancelled as exc:
                job.status = "cancelled"
                job.error = str(exc)
            except Exception as exc:
                job.status = "failed"
                job.error = str(exc)

        job.thread = threading.Thread(target=run, name=f"model-download-{job.id[:8]}", daemon=True)
        job.thread.start()
        return job

    def get(self, job_id: str) -> DownloadJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[DownloadJob]:
        with self._lock:
            return list(self._jobs.values())


DOWNLOADS = DownloadManager()


def _safe_filename(filename: str) -> str:
    value = (filename or "").replace("\\", "/").strip("/")
    if not value or value.startswith(".") or ".." in Path(value).parts:
        raise ValueError("Unsafe model filename")
    name = Path(value).name
    if not name.lower().endswith(".gguf"):
        raise ValueError("Only GGUF files can be downloaded")
    return name


def _download_url(repo_id: str, revision: str, filename: str) -> str:
    from huggingface_hub import hf_hub_url

    return hf_hub_url(repo_id=repo_id, filename=filename, revision=revision)


def _available_bytes(destination: Path) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    return int(shutil.disk_usage(destination.parent).free)


def _sha256(path: Path, *, cancel_event: threading.Event | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            if cancel_event and cancel_event.is_set():
                raise DownloadCancelled("Download cancelled during verification")
            chunk = handle.read(4 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def download_gguf(
    *,
    repo_id: str,
    filename: str,
    revision: str,
    expected_size: int | None = None,
    expected_sha256: str | None = None,
    metadata: dict[str, Any] | None = None,
    destination_dir: Path | None = None,
    cancel_event: threading.Event | None = None,
    progress: ProgressCallback | None = None,
    timeout: float = 60.0,
) -> Path:
    """Resume, verify, atomically install, and register one exact GGUF file."""
    repo_id = model_catalog.validate_repo_id(repo_id)
    if not revision or len(revision.strip()) < 7:
        raise ValueError("An explicit Hugging Face revision is required")
    safe_name = _safe_filename(filename)
    destination_dir = Path(destination_dir or paths.MODELS_DIR)
    destination_dir.mkdir(parents=True, exist_ok=True)
    target = destination_dir / safe_name
    partial = destination_dir / f".{safe_name}.part"

    expected_size = int(expected_size or 0) or None
    if target.is_file():
        size_ok = expected_size is None or target.stat().st_size == expected_size
        digest = _sha256(target, cancel_event=cancel_event) if size_ok else ""
        hash_ok = expected_sha256 is None or digest.lower() == expected_sha256.lower()
        if size_ok and hash_ok:
            details = dict(metadata or {})
            models.register_model(
                file_path=target,
                name=details.get("name") or safe_name,
                source="huggingface",
                repo_id=repo_id,
                revision=revision,
                file_name=filename,
                quantization=details.get("quantization") or model_catalog.infer_quantization(filename),
                size_bytes=target.stat().st_size,
                sha256=digest,
                license_name=details.get("license") or "Not declared",
                publisher=details.get("publisher") or repo_id.split("/", 1)[0],
                provenance=details.get("provenance") or "community",
                verified=bool(expected_sha256),
                metadata=details,
            )
            if progress:
                progress(target.stat().st_size, expected_size or target.stat().st_size)
            return target
    existing = partial.stat().st_size if partial.exists() else 0
    remaining = max(0, (expected_size or 0) - existing)
    reserve = max(256 * 1024 * 1024, int((expected_size or 0) * 0.03))
    if expected_size and _available_bytes(target) < remaining + reserve:
        raise OSError(
            f"Not enough disk space: need approximately {(remaining + reserve) / (1024**3):.1f} GB"
        )

    headers: dict[str, str] = {}
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if existing:
        headers["Range"] = f"bytes={existing}-"

    url = _download_url(repo_id, revision, filename)
    with requests.get(url, headers=headers, stream=True, timeout=timeout, allow_redirects=True) as response:
        if response.status_code == 416 and expected_size and existing == expected_size:
            total = expected_size
        else:
            response.raise_for_status()
            append = bool(existing and response.status_code == 206)
            if existing and not append:
                existing = 0
            content_length = int(response.headers.get("Content-Length", "0") or 0)
            total = expected_size or (existing + content_length if content_length else None)
            with partial.open("ab" if append else "wb") as handle:
                downloaded = existing
                if progress:
                    progress(downloaded, total)
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if cancel_event and cancel_event.is_set():
                        raise DownloadCancelled("Download cancelled; partial file kept for retry")
                    if not chunk:
                        continue
                    handle.write(chunk)
                    downloaded += len(chunk)
                    if progress:
                        progress(downloaded, total)
                handle.flush()
                os.fsync(handle.fileno())

    actual_size = partial.stat().st_size
    if expected_size and actual_size != expected_size:
        raise IOError(f"Size verification failed: expected {expected_size}, got {actual_size}")
    digest = _sha256(partial, cancel_event=cancel_event)
    if expected_sha256 and digest.lower() != expected_sha256.lower():
        raise IOError("SHA-256 verification failed; partial file was not activated")

    os.replace(partial, target)
    details = dict(metadata or {})
    models.register_model(
        file_path=target,
        name=details.get("name") or safe_name,
        source="huggingface",
        repo_id=repo_id,
        revision=revision,
        file_name=filename,
        quantization=details.get("quantization") or model_catalog.infer_quantization(filename),
        size_bytes=actual_size,
        sha256=digest,
        license_name=details.get("license") or "Not declared",
        publisher=details.get("publisher") or repo_id.split("/", 1)[0],
        provenance=details.get("provenance") or "community",
        verified=bool(expected_sha256),
        metadata=details,
    )
    return target


def import_local_gguf(source: Path | str, *, copy: bool = True) -> Path:
    source_path = Path(source).expanduser().resolve()
    if not source_path.is_file() or source_path.suffix.lower() != ".gguf":
        raise ValueError("Choose an existing .gguf file")
    paths.MODELS_DIR.mkdir(parents=True, exist_ok=True)
    target = paths.MODELS_DIR / source_path.name
    if copy and source_path != target.resolve():
        temp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
        shutil.copy2(source_path, temp)
        os.replace(temp, target)
    else:
        target = source_path
    models.register_model(
        file_path=target,
        name=target.name,
        source="local_import",
        file_name=target.name,
        quantization=model_catalog.infer_quantization(target.name),
        size_bytes=target.stat().st_size,
        sha256=_sha256(target),
        provenance="local",
        verified=True,
    )
    return target


def repair_model(model_id: str) -> dict[str, Any]:
    record = models.get_model(model_id)
    if not record:
        raise KeyError(model_id)
    path = Path(record["file_path"])
    status = "ready"
    digest = None
    if not path.is_file():
        status = "missing"
    elif record.get("size_bytes") and path.stat().st_size != int(record["size_bytes"]):
        status = "size_mismatch"
    elif record.get("sha256"):
        digest = _sha256(path)
        if digest.lower() != str(record["sha256"]).lower():
            status = "hash_mismatch"
    models.update_model_status(model_id, status)
    return {"id": model_id, "status": status, "sha256": digest}


def remove_model(model_id: str, *, delete_file: bool = True) -> None:
    record = models.get_model(model_id)
    if not record:
        return
    path = Path(record["file_path"])
    cfg = paths.load_config()
    if paths.active_model_path(cfg).resolve() == path.resolve():
        cfg.setdefault("inference", {})["model"] = ""
        paths.save_config(cfg)
    models.remove_model(model_id)
    if delete_file and path.is_file():
        path.unlink()

