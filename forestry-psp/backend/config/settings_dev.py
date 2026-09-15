"""开发/演示设置：SQLite + GeoJSON 边界（无需 PostGIS 即可运行全部 API）。

生产部署请使用 config.settings（PostgreSQL/PostGIS）。
"""
import os

os.environ.setdefault("FORESTRY_GIS_BACKEND", "json")
os.environ.setdefault("DJANGO_DEBUG", "1")

from .settings import *  # noqa: F401,F403
