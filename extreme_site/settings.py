import sys
import os
from pathlib import Path
from urllib.parse import urlparse

# =========================================================
# DEPLOYMENT CONFIG FOR RENDER
# =========================================================
if os.environ.get("RENDER"):
    DEBUG = False
    ALLOWED_HOSTS = ["thetravelwild.onrender.com", "www.thetravelwild.com", "thetravelwild.com"]
    CSRF_TRUSTED_ORIGINS = [
        "https://thetravelwild.onrender.com",
        "https://www.thetravelwild.com",
        "https://thetravelwild.com",
    ]
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_SSL_REDIRECT = True

# Garantiza que Django vea el directorio base del proyecto
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))
sys.path.append(str(BASE_DIR / "directory"))  # 👈 añadido para importar context_processors correctamente

# Carga de variables de entorno (.env en la raíz del proyecto)
try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs):
        return None

# BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# Seguridad / Debug
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'django-insecure-CHANGE-ME')
DEBUG = os.getenv("DEBUG", "1") in ("1", "true", "True")
ALLOWED_HOSTS = [h.strip() for h in os.getenv("ALLOWED_HOSTS", "127.0.0.1,localhost").split(',') if h.strip()]

# Apps
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'widget_tweaks',
    'dal',
    'dal_select2',
    # Terceros
    'cities_light',
    # App propia
    'directory',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'extreme_site.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'directory.context_processors.global_activities',
                'directory.context_processors.school_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'extreme_site.wsgi.application'

# Base de datos (DATABASE_URL o variables separadas)

def _db_from_env():
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        parsed = urlparse(db_url)
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": (parsed.path or "").lstrip("/"),
            "USER": parsed.username or "",
            "PASSWORD": parsed.password or "",
            "HOST": parsed.hostname or "",
            "PORT": str(parsed.port or "") if parsed.port else "",
        }
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DB_NAME", "extreme_db"),
        "USER": os.getenv("DB_USER", "extreme_app"),
        "PASSWORD": os.getenv("DB_PASSWORD", ""),
        "HOST": os.getenv("DB_HOST", "127.0.0.1"),
        "PORT": os.getenv("DB_PORT", "5432"),
    }

DATABASES = {"default": _db_from_env()}

# Password validators
AUTH_PASSWORD_VALIDATORS = [
    { 'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator' },
    { 'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator' },
    { 'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator' },
    { 'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator' },
]

# Internacionalización
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Europe/Lisbon'
USE_I18N = True
USE_TZ = True

# Static & Media
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_ROOT = BASE_DIR / "media"
MEDIA_URL = "/media/"

# Default PK
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

AUTH_USER_MODEL = 'auth.User'

# =========================================================
# EMAIL SETTINGS - DEVELOPMENT / TEST MODE
# =========================================================
if DEBUG:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
    DEFAULT_FROM_EMAIL = "The Travel Wild <noreply@thetravelwild.com>"
else:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
    EMAIL_PORT = int(os.getenv("EMAIL_PORT", 587))
    EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
    EMAIL_USE_TLS = True
    DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "The Travel Wild <noreply@thetravelwild.com>")


# Stripe
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_PRICE_BASIC = os.getenv("STRIPE_PRICE_BASIC", "")
STRIPE_PRICE_MEDIUM = os.getenv("STRIPE_PRICE_MEDIUM", "")
STRIPE_PRICE_PREMIUM = os.getenv("STRIPE_PRICE_PREMIUM", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

# ------------------------------------------------------------
# Stripe Environment Mode (Test / Live)
# ------------------------------------------------------------
STRIPE_LIVE_MODE = not DEBUG  # True si está en producción
STRIPE_API_BASE = "https://api.stripe.com"

# ------------------------------------------------------------
# Stripe Premium Subscription Settings
# ------------------------------------------------------------
STRIPE_PREMIUM_PRICE_ID = os.getenv("STRIPE_PREMIUM_PRICE_ID", "price_XXXXXXXXXXXX")  # ID del plan Premium en Stripe
STRIPE_SUCCESS_URL = os.getenv("STRIPE_SUCCESS_URL", "https://thetravelwild.com/pricing/checkout/success/")
STRIPE_CANCEL_URL = os.getenv("STRIPE_CANCEL_URL", "https://thetravelwild.com/pricing/checkout/cancel/")


LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/account/'
LOGOUT_REDIRECT_URL = '/'

# Seguridad adicional
SESSION_COOKIE_SECURE = False  # Cambiar a True en producción
CSRF_COOKIE_SECURE = False     # Cambiar a True en producción

ALLOWED_HOSTS = [
    '*',
    'localhost',
    '127.0.0.1',
    '934c529b5464.ngrok-free.app'
]

CSRF_TRUSTED_ORIGINS = [
    'https://934c529b5464.ngrok-free.app',
    'http://localhost',
    'http://localhost:8000',
    'https://localhost',
    'https://localhost:8000',
    'http://127.0.0.1',
    'http://127.0.0.1:8000',
    'https://127.0.0.1',
    'https://127.0.0.1:8000',
]

# Emails de soporte
ADMINS = [('Admin', os.getenv('ADMIN_EMAIL', 'admin@thetravelwild.com'))]
MANAGERS = ADMINS

# Internacionalización extendida
LANGUAGES = [
    ('en', 'English'),
    ('es', 'Español'),
    ('pt', 'Português'),
]

LOCALE_PATHS = [BASE_DIR / 'locale']

# Configuración de autenticación social (placeholders)
SOCIAL_AUTH_GOOGLE_OAUTH2_KEY = os.getenv('GOOGLE_CLIENT_ID', '')
SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET = os.getenv('GOOGLE_CLIENT_SECRET', '')
SOCIAL_AUTH_APPLE_ID_CLIENT = os.getenv('APPLE_CLIENT_ID', '')
SOCIAL_AUTH_APPLE_ID_TEAM = os.getenv('APPLE_TEAM_ID', '')
SOCIAL_AUTH_APPLE_ID_KEY = os.getenv('APPLE_KEY', '')
SOCIAL_AUTH_APPLE_ID_SECRET = os.getenv('APPLE_SECRET', '')

# =========================================================
# SESSION CONFIG
# =========================================================
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = False

# ------------------------------------------------------------
# Separar sesiones entre el sitio principal y el panel de administración
# ------------------------------------------------------------
SESSION_COOKIE_NAME = "traveler_sessionid"   # Cookie para usuarios normales
ADMIN_SESSION_COOKIE_NAME = "admin_sessionid"  # Cookie separada para el admin
SESSION_COOKIE_AGE = 60 * 60 * 24 * 7  # Sesión válida por 7 días


# ------------------------------------------------------------
# Excluir /stripe_webhook/ del middleware CSRF
# ------------------------------------------------------------
CSRF_EXEMPT_URLS = [r"^stripe_webhook/$", r"^directory/stripe_webhook/$"]

MIDDLEWARE.insert(
    MIDDLEWARE.index('django.middleware.csrf.CsrfViewMiddleware') + 1,
    'extreme_site.middleware.ConditionalCsrfMiddleware'
)

# =========================================================
# LOGGING CONFIGURATION
# =========================================================
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{asctime}] {levelname} {name}: {message}",
            "style": "{",
        },
        "simple": {
            "format": "{levelname}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        # Console output (for development)
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
        # General app log file
        "file": {
            "class": "logging.FileHandler",
            "filename": BASE_DIR / "logs" / "app.log",
            "formatter": "verbose",
        },
        # Email-specific errors
        "email_file": {
            "class": "logging.FileHandler",
            "filename": BASE_DIR / "logs" / "email_errors.log",
            "formatter": "verbose",
        },
        # Stripe-specific errors
        "stripe_file": {
            "class": "logging.FileHandler",
            "filename": BASE_DIR / "logs" / "stripe_errors.log",
            "formatter": "verbose",
        },
    },
    "loggers": {
        "django": {
            "handlers": ["console", "file"],
            "level": "INFO" if DEBUG else "WARNING",
            "propagate": True,
        },
        "django.core.mail": {
            "handlers": ["console", "email_file"],
            "level": "ERROR",
            "propagate": False,
        },
        "stripe": {
            "handlers": ["console", "stripe_file"],
            "level": "ERROR",
            "propagate": False,
        },
    },
}

# =========================================================
# STATIC FILES CONFIG FOR RENDER
# =========================================================
# Ensure directories exist
os.makedirs(STATIC_ROOT, exist_ok=True)
os.makedirs(MEDIA_ROOT, exist_ok=True)