from pathlib import Path
import os
from dotenv import load_dotenv
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')

# ---------------- Seguridad ----------------
SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret')
DEBUG = os.getenv('DEBUG', 'True') == 'True'

# Hosts permitidos (Render + tu dominio por defecto)
ALLOWED_HOSTS = [
    h.strip() for h in os.getenv(
        'ALLOWED_HOSTS',
        '.onrender.com,fogonylena.com,www.fogonylena.com,localhost,127.0.0.1'
    ).split(',') if h.strip()
]

# CSRF confiables (HTTPS + Render + tu dominio)
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.getenv(
        'CSRF_TRUSTED_ORIGINS',
        'https://*.onrender.com,https://fogonylena.com,https://www.fogonylena.com'
    ).split(',') if o.strip()
]

# Para respetar X-Forwarded-Proto detrás del proxy de Render
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Endurecimiento básico en prod
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True

# ---------------- Email (SMTP Office 365) ----------------
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = "smtp.office365.com"
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_USE_SSL = False
EMAIL_HOST_USER = "cartera@fogonylena.net"
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")  # App Password M365
EMAIL_FROM_NAME = os.getenv("EMAIL_FROM_NAME", "Cartera Fogón & Leña")
DEFAULT_FROM_EMAIL = f"{EMAIL_FROM_NAME} <{EMAIL_HOST_USER}>"

# ---------------- Feature flags ----------------
USE_S3_MEDIA = os.getenv('USE_S3_MEDIA', 'false').lower() == 'true'

# ---------------- Apps instaladas ----------------
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # API
    'rest_framework',
    'django_filters',
    'rest_framework.authtoken',

    # Única app de negocio
    'cartera',
]

if USE_S3_MEDIA:
    INSTALLED_APPS.append('storages')

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    # WhiteNoise justo después de SecurityMiddleware (sirve estáticos en prod)
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'carterapro.urls'

TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [BASE_DIR / 'cartera' / 'templates'],
    'APP_DIRS': True,
    'OPTIONS': {
        'context_processors': [
            'django.template.context_processors.debug',
            'django.template.context_processors.request',
            'django.contrib.auth.context_processors.auth',
            'django.contrib.messages.context_processors.messages',
        ],
        'builtins': ['cartera.templatetags.formatting'],
    },
}]


WSGI_APPLICATION = 'carterapro.wsgi.application'

# ---------------- Base de datos ----------------
# Prioridad:
# 1) DATABASE_URL (Render/producción)
# 2) POSTGRES_* (tu configuración actual por env)
# 3) SQLite (dev)

DATABASE_URL = os.getenv('DATABASE_URL', '').strip()

if DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.config(conn_max_age=600, ssl_require=True)
    }
else:
    pg_name = os.getenv("POSTGRES_DB")
    if pg_name:  # usa tus env POSTGRES_*
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": pg_name,
                "USER": os.getenv("POSTGRES_USER"),
                "PASSWORD": os.getenv("POSTGRES_PASSWORD"),
                "HOST": os.getenv("POSTGRES_HOST", "localhost"),
                "PORT": os.getenv("POSTGRES_PORT", "5432"),
                "CONN_MAX_AGE": 60,
                "OPTIONS": {"sslmode": "prefer"},
            }
        }
    else:  # fallback dev: SQLite
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": BASE_DIR / "db.sqlite3",
            }
        }

# ---------------- Passwords ----------------
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ---------------- i18n ----------------
LANGUAGE_CODE = 'es'
TIME_ZONE = 'America/Bogota'
USE_I18N = True
USE_TZ = True

# ---------------- Estáticos y media ----------------
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
#STATICFILES_DIRS = [BASE_DIR / 'static']

# Config de STORAGES base (estáticos con WhiteNoise en prod)
STORAGES = {
    "default": {
        # por defecto archivos subidos quedan en disco local (o S3 más abajo)
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        # WhiteNoise con manifest y compresión (solo efectivo en no-DEBUG)
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# MEDIA local por defecto
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# --- MEDIA en S3 (privado, URLs firmadas) ---
if USE_S3_MEDIA:
    AWS_STORAGE_BUCKET_NAME = os.getenv("AWS_STORAGE_BUCKET_NAME")        # ej.: imgcarterafogon
    AWS_S3_REGION_NAME = os.getenv("AWS_S3_REGION_NAME")
    AWS_S3_SIGNATURE_VERSION = "s3v4"
    AWS_S3_FILE_OVERWRITE = False
    AWS_DEFAULT_ACL = None
    AWS_S3_ENDPOINT_URL = os.getenv("AWS_S3_ENDPOINT_URL")  # normalmente vacío con AWS
    AWS_QUERYSTRING_AUTH = True
    AWS_QUERYSTRING_EXPIRE = int(os.getenv("AWS_QUERYSTRING_EXPIRE", "3600"))  # 1h

    # Storage backend propio para MEDIA
    STORAGES["default"] = {
        "BACKEND": "carterapro.storage_backends.MediaStorage",
    }

# ---------------- Autenticación ----------------
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'login'

# ---------------- DRF ----------------
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.TokenAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.OrderingFilter',
        'rest_framework.filters.SearchFilter',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 100,
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# URL pública del sitio (útil para correos, links firmados, etc.)
SITE_URL = os.getenv("SITE_URL", "http://127.0.0.1:8000")
