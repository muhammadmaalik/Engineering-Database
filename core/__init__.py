"""Motherbrain companion core — shared by workstation and sync client."""

from . import paths
from .paths import ensure_config, load_config, save_config

__all__ = [
    "paths",
    "ensure_config",
    "load_config",
    "save_config",
]
