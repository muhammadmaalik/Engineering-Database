"""Curated and community GGUF model discovery.

The curated catalog is reviewed and deterministic. Community search is opt-in
and returns metadata only; callers must explicitly choose an exact GGUF file.
No repository code is ever downloaded or executed.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

REPO_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class CatalogEntry:
    id: str
    label: str
    publisher: str
    repo_id: str | None
    filename: str | None
    quantization: str | None
    license: str
    parameter_count: str
    estimated_size_gb: float | None
    min_ram_gb: int | None
    recommended_vram_gb: int | None
    context: int
    gpu_layers: int
    status: str = "available"
    provenance: str = "curated"
    description: str = ""
    revision: str | None = None
    sha256: str | None = None

    @property
    def available(self) -> bool:
        return self.status == "available" and bool(self.repo_id and self.filename)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["available"] = self.available
        return value


CURATED_CATALOG: tuple[CatalogEntry, ...] = (
    CatalogEntry(
        id="occhialini-engineer",
        label="Occhialini Engineer",
        publisher="Occhialini",
        repo_id=None,
        filename=None,
        quantization=None,
        license="To be announced",
        parameter_count="Custom",
        estimated_size_gb=None,
        min_ram_gb=None,
        recommended_vram_gb=None,
        context=8192,
        gpu_layers=28,
        status="coming_soon",
        provenance="first_party",
        description="Project-aware engineering model. Optional download coming soon.",
    ),
    CatalogEntry(
        id="occhialini-robotics",
        label="Occhialini Robotics",
        publisher="Occhialini",
        repo_id=None,
        filename=None,
        quantization=None,
        license="To be announced",
        parameter_count="Custom",
        estimated_size_gb=None,
        min_ram_gb=None,
        recommended_vram_gb=None,
        context=8192,
        gpu_layers=28,
        status="coming_soon",
        provenance="first_party",
        description="Robotics and Isaac Sim specialist. Optional download coming soon.",
    ),
    CatalogEntry(
        id="qwen-14b",
        label="Qwen2.5 Coder 14B Instruct",
        publisher="Qwen",
        repo_id="Qwen/Qwen2.5-Coder-14B-Instruct-GGUF",
        filename="qwen2.5-coder-14b-instruct-q5_k_m.gguf",
        quantization="Q5_K_M",
        license="Apache-2.0",
        parameter_count="14B",
        estimated_size_gb=10.5,
        min_ram_gb=16,
        recommended_vram_gb=8,
        context=4096,
        gpu_layers=35,
        description="Balanced coding model for modern laptops.",
    ),
    CatalogEntry(
        id="qwen-32b",
        label="Qwen2.5 Coder 32B Instruct",
        publisher="Qwen",
        repo_id="Qwen/Qwen2.5-Coder-32B-Instruct-GGUF",
        filename="qwen2.5-coder-32b-instruct-q3_k_m.gguf",
        quantization="Q3_K_M",
        license="Apache-2.0",
        parameter_count="32B",
        estimated_size_gb=15.0,
        min_ram_gb=24,
        recommended_vram_gb=8,
        context=2048,
        gpu_layers=28,
        description="Higher quality with partial CUDA offload on 8GB GPUs.",
    ),
    CatalogEntry(
        id="gemma-9b",
        label="Gemma 2 9B Instruct",
        publisher="bartowski / Google",
        repo_id="bartowski/gemma-2-9b-it-GGUF",
        filename="gemma-2-9b-it-Q5_K_M.gguf",
        quantization="Q5_K_M",
        license="Gemma",
        parameter_count="9B",
        estimated_size_gb=6.5,
        min_ram_gb=12,
        recommended_vram_gb=6,
        context=4096,
        gpu_layers=99,
        description="Fast general-purpose local assistant.",
    ),
)


def list_curated() -> list[dict[str, Any]]:
    return [entry.to_dict() for entry in CURATED_CATALOG]


def get_curated(entry_id: str) -> CatalogEntry:
    for entry in CURATED_CATALOG:
        if entry.id == entry_id:
            return entry
    raise KeyError(f"Unknown catalog entry: {entry_id}")


def validate_repo_id(repo_id: str) -> str:
    value = (repo_id or "").strip()
    if not REPO_ID_RE.fullmatch(value):
        raise ValueError("Expected a Hugging Face repository in publisher/name form")
    return value


def search_community(query: str, *, limit: int = 24) -> list[dict[str, Any]]:
    """Search public Hugging Face repositories that advertise GGUF artifacts."""
    value = (query or "").strip()
    if len(value) < 2:
        return []
    from huggingface_hub import HfApi

    api = HfApi()
    found: list[dict[str, Any]] = []
    for model in api.list_models(
        search=value,
        filter="gguf",
        full=True,
        sort="downloads",
        direction=-1,
        limit=max(1, min(int(limit), 50)),
    ):
        repo_id = getattr(model, "id", "") or ""
        if not REPO_ID_RE.fullmatch(repo_id):
            continue
        card = getattr(model, "card_data", None)
        license_name = getattr(card, "license", None) if card else None
        found.append(
            {
                "repo_id": repo_id,
                "publisher": repo_id.split("/", 1)[0],
                "license": license_name or "Not declared",
                "downloads": int(getattr(model, "downloads", 0) or 0),
                "likes": int(getattr(model, "likes", 0) or 0),
                "gated": bool(getattr(model, "gated", False)),
                "private": bool(getattr(model, "private", False)),
                "revision": getattr(model, "sha", None),
                "provenance": "community",
            }
        )
    return found


def list_gguf_files(repo_id: str, *, revision: str | None = None) -> dict[str, Any]:
    """Return exact GGUF files and immutable revision metadata for one repo."""
    from huggingface_hub import HfApi

    repo_id = validate_repo_id(repo_id)
    info = HfApi().model_info(repo_id, revision=revision, files_metadata=True)
    files: list[dict[str, Any]] = []
    for sibling in info.siblings or []:
        name = getattr(sibling, "rfilename", "") or ""
        if not name.lower().endswith(".gguf"):
            continue
        lfs = getattr(sibling, "lfs", None)
        sha = None
        size = int(getattr(sibling, "size", 0) or 0)
        if lfs:
            sha = getattr(lfs, "sha256", None)
            size = int(getattr(lfs, "size", size) or size)
        files.append(
            {
                "filename": name,
                "size_bytes": size,
                "sha256": sha,
                "quantization": infer_quantization(name),
            }
        )
    card = getattr(info, "card_data", None)
    return {
        "repo_id": repo_id,
        "publisher": repo_id.split("/", 1)[0],
        "revision": info.sha,
        "license": (getattr(card, "license", None) if card else None) or "Not declared",
        "gated": bool(getattr(info, "gated", False)),
        "files": sorted(files, key=lambda item: item["filename"].lower()),
    }


def infer_quantization(filename: str) -> str:
    upper = filename.upper()
    for marker in (
        "IQ1_M", "IQ2_XXS", "IQ2_XS", "IQ2_M", "IQ3_XXS", "IQ3_XS",
        "IQ3_M", "IQ4_XS", "IQ4_NL", "Q2_K", "Q3_K_S", "Q3_K_M",
        "Q3_K_L", "Q4_0", "Q4_K_S", "Q4_K_M", "Q5_0", "Q5_K_S",
        "Q5_K_M", "Q6_K", "Q8_0", "F16", "BF16",
    ):
        if marker in upper:
            return marker
    return "unknown"
