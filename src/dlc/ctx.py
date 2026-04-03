"""
Shared DLC runtime context.

Populated by app.py (via setup() at startup and before_request hook).
Read by dlc/*.py Blueprint modules.

This module exists to break circular imports: dlc/*.py cannot import
from app.py, so shared state is passed through this neutral module.
"""
from __future__ import annotations
from pathlib import Path

_ctx: dict = {
    "DATA_DIR": None,
    "USER_DATA_DIR": None,
    "redis_client": None,
    "celery": None,
    "yaml": None,
    "ruamel": None,
}


def setup(data_dir, user_data_dir, redis_client, celery, yaml_lib, ruamel) -> None:
    """Populate the shared context. Called by app.py before_request."""
    _ctx["DATA_DIR"] = data_dir
    _ctx["USER_DATA_DIR"] = user_data_dir
    _ctx["redis_client"] = redis_client
    _ctx["celery"] = celery
    _ctx["yaml"] = yaml_lib
    _ctx["ruamel"] = ruamel


def data_dir() -> Path:
    return _ctx["DATA_DIR"]

def user_data_dir() -> Path:
    return _ctx["USER_DATA_DIR"]

def redis_client():
    return _ctx["redis_client"]

def celery():
    return _ctx["celery"]

def yaml_lib():
    return _ctx["yaml"]

def ruamel():
    return _ctx["ruamel"]
