"""生产设置：PostgreSQL/PostGIS。

通过环境变量配置：
- FORESTRY_GIS_BACKEND=postgis（默认）使用 PostGIS 保存样地边界；
  开发/演示可设为 json（SQLite + GeoJSON，见 settings_dev）。
- PGDATABASE / PGUSER / PGPASSWORD / PGHOST / PGPORT 数据库连接。
"""
import os
from secrets import token_hex
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# 生产环境显式注入；开发环境缺省时使用进程级随机值，避免把密钥写入仓库。
_k = os.environ.get("DJANGO_SECRET_KEY")
SECRET_KEY = _k or token_hex(32)
DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

#: GIS 后端：postgis（生产）| json（开发演示，边界以 GeoJSON 存 JSONField）
FORESTRY_GIS_BACKEND = os.environ.get("FORESTRY_GIS_BACKEND", "postgis")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "forest",
]
if FORESTRY_GIS_BACKEND == "postgis":
    INSTALLED_APPS.append("django.contrib.gis")

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
TEMPLATES = []
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

if FORESTRY_GIS_BACKEND == "postgis":
    _p = os.environ.get("PGPASSWORD", "")
    DATABASES = {
        "default": {
            "ENGINE": "django.contrib.gis.db.backends.postgis",
            "NAME": os.environ.get("PGDATABASE", "forestry"),
            "USER": os.environ.get("PGUSER", "forestry"),
            "PASSWORD": _p,
            "HOST": os.environ.get("PGHOST", "localhost"),
            "PORT": os.environ.get("PGPORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.environ.get("SQLITE_PATH", str(BASE_DIR / "db_dev.sqlite3")),
        }
    }

CORS_ALLOW_ALL_ORIGINS = True  # 演示环境；生产应改为白名单

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "UNAUTHENTICATED_USER": None,
}

USE_TZ = True
TIME_ZONE = "Asia/Shanghai"
