from pathlib import Path
import os
import sys

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_first(*names, default=None):
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip() != "":
            return value.strip()
    return default


IS_TESTING = "test" in sys.argv or env_bool("DJANGO_TEST", False)
APP_ENV = env_first("APP_ENV", "DJANGO_ENV", default="").lower()
if IS_TESTING:
    APP_ENV = "test"
elif not APP_ENV:
    if env_bool("RENDER", False) or env_bool("REQUIRE_PRODUCTION_SETTINGS", False):
        APP_ENV = "production"
    else:
        APP_ENV = "local"

if APP_ENV == "local" and env_bool("DJANGO_LOAD_DOTENV", True):
    load_dotenv(BASE_DIR / ".env", override=False)

REQUIRE_PRODUCTION_SETTINGS = False if APP_ENV == "test" else (
    APP_ENV == "production" or env_bool("REQUIRE_PRODUCTION_SETTINGS", env_bool("RENDER", False))
)
DEBUG = env_bool("DEBUG", APP_ENV != "production")

SECRET_KEY = os.getenv("SECRET_KEY", "").strip()
if not SECRET_KEY:
    if REQUIRE_PRODUCTION_SETTINGS:
        raise ImproperlyConfigured("SECRET_KEY es obligatoria en producción.")
    SECRET_KEY = "dev-secret-only-for-local-development"
elif REQUIRE_PRODUCTION_SETTINGS and len(SECRET_KEY) < 32:
    raise ImproperlyConfigured("SECRET_KEY debe tener al menos 32 caracteres en producción.")

ALLOWED_HOSTS = [
    h.strip() for h in os.getenv(
        "ALLOWED_HOSTS",
        ".onrender.com,fogonylena.com,www.fogonylena.com,localhost,127.0.0.1",
    ).split(",") if h.strip()
]

CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.getenv(
        "CSRF_TRUSTED_ORIGINS",
        "https://*.onrender.com,https://fogonylena.com,https://www.fogonylena.com",
    ).split(",") if o.strip()
]

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_BROWSER_XSS_FILTER = True
    X_FRAME_OPTIONS = "DENY"

EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.office365.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "cartera@fogonylena.net")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_FROM_NAME = os.getenv("EMAIL_FROM_NAME", "Cartera Fogón & Leña")
DEFAULT_FROM_EMAIL = f"{EMAIL_FROM_NAME} <{EMAIL_HOST_USER}>"

USE_S3_MEDIA = env_bool("USE_S3_MEDIA", False)
COMPROBANTE_MAX_UPLOAD_SIZE = int(os.getenv("COMPROBANTE_MAX_UPLOAD_SIZE", str(10 * 1024 * 1024)))

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "django_filters",
    "rest_framework.authtoken",
    "cartera",
]
if USE_S3_MEDIA:
    INSTALLED_APPS.append("storages")

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "carterapro.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "cartera" / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {
        "context_processors": [
            "django.template.context_processors.debug",
            "django.template.context_processors.request",
            "django.contrib.auth.context_processors.auth",
            "django.contrib.messages.context_processors.messages",
        ],
        "builtins": ["cartera.templatetags.formatting"],
    },
}]

WSGI_APPLICATION = "carterapro.wsgi.application"
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if REQUIRE_PRODUCTION_SETTINGS and not DATABASE_URL:
    raise ImproperlyConfigured("DATABASE_URL es obligatoria en producción.")
if APP_ENV == "test":
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
elif DATABASE_URL:
    database_url_uses_postgres = DATABASE_URL.lower().startswith(("postgres://", "postgresql://"))
    DATABASES = {"default": dj_database_url.config(conn_max_age=600, ssl_require=database_url_uses_postgres)}
else:
    pg_name = env_first("POSTGRES_DB", "DB_NAME")
    db_engine = env_first("DB_ENGINE", "DATABASE_ENGINE", "DJANGO_DB_ENGINE", default="").lower()
    db_port = env_first("DB_PORT", default="")
    db_vars_are_postgres = (
        bool(env_first("POSTGRES_DB"))
        or "postgres" in db_engine
        or (bool(env_first("DB_NAME")) and not db_engine and db_port != "3306")
    )
    if not db_vars_are_postgres:
        pg_name = None
    if pg_name:
        DATABASES = {"default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": pg_name,
            "USER": env_first("POSTGRES_USER", "DB_USER", default=""),
            "PASSWORD": env_first("POSTGRES_PASSWORD", "DB_PASSWORD", default=""),
            "HOST": env_first("POSTGRES_HOST", "DB_HOST", default="localhost"),
            "PORT": env_first("POSTGRES_PORT", "DB_PORT", default="5432"),
            "CONN_MAX_AGE": 60,
            "OPTIONS": {"sslmode": "prefer"},
        }}
    else:
        DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
if APP_ENV == "test":
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

LANGUAGE_CODE = "es"
TIME_ZONE = "America/Bogota"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
if USE_S3_MEDIA:
    AWS_STORAGE_BUCKET_NAME = os.getenv("AWS_STORAGE_BUCKET_NAME")
    AWS_S3_REGION_NAME = os.getenv("AWS_S3_REGION_NAME")
    AWS_S3_SIGNATURE_VERSION = "s3v4"
    AWS_S3_FILE_OVERWRITE = False
    AWS_DEFAULT_ACL = None
    AWS_S3_ENDPOINT_URL = os.getenv("AWS_S3_ENDPOINT_URL")
    AWS_QUERYSTRING_AUTH = True
    AWS_QUERYSTRING_EXPIRE = int(os.getenv("AWS_QUERYSTRING_EXPIRE", "3600"))
    if REQUIRE_PRODUCTION_SETTINGS and not AWS_STORAGE_BUCKET_NAME:
        raise ImproperlyConfigured("AWS_STORAGE_BUCKET_NAME es obligatoria cuando USE_S3_MEDIA=true.")
    STORAGES["default"] = {"BACKEND": "carterapro.storage_backends.MediaStorage"}

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.OrderingFilter",
        "rest_framework.filters.SearchFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 100,
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
SITE_URL = os.getenv("SITE_URL", "http://127.0.0.1:8000")
