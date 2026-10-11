"""
Django settings for the drinkshop project (drink e-commerce + delivery).
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Load variables from a .env file in the project root, if present.
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / '.env')
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Core / security
# ---------------------------------------------------------------------------
SECRET_KEY = os.environ.get(
    'DJANGO_SECRET_KEY',
    'dev-only-secret-key-change-me-before-deploying'
)

DEBUG = os.environ.get('DJANGO_DEBUG', 'True') == 'True'

ALLOWED_HOSTS = [h for h in os.environ.get('DJANGO_ALLOWED_HOSTS', '*').split(',') if h]

# Vercel serves every deployment from both the stable production alias and a
# per-deploy subdomain that changes on every push (e.g.
# radicalentdrinkshop-<hash>-<team>.vercel.app). Trust the whole *.vercel.app
# wildcard so neither of those returns 400 DisallowedHost.
if os.environ.get('VERCEL'):
    ALLOWED_HOSTS += ['.vercel.app']

CSRF_TRUSTED_ORIGINS = [
    o for o in os.environ.get('DJANGO_CSRF_TRUSTED_ORIGINS', '').split(',') if o
]
# Vercel preview deployments get a random *.vercel.app subdomain each time,
# so trust the whole domain by default unless the person has already listed
# their own origins above (e.g. a custom domain) via DJANGO_CSRF_TRUSTED_ORIGINS.
if not CSRF_TRUSTED_ORIGINS and os.environ.get('VERCEL'):
    CSRF_TRUSTED_ORIGINS = ['https://*.vercel.app']

# Vercel (like most serverless/proxy hosts) terminates TLS at the edge and
# forwards the original scheme in this header — without telling Django,
# request.is_secure() would always read False, breaking secure cookies,
# CSRF checks on POSTs, and allauth's Google OAuth callback URL scheme.
if os.environ.get('VERCEL'):
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites',

    # Media storage
    'cloudinary_storage',
    'cloudinary',

    # Auth
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.google',

    'store',
    'orders',
    'payments',
    'accounts',
    'careers',
    'vendors',
    'marketing',
    'sourcing',
    'rest_framework',
    'api',
]

SITE_ID = 1

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'allauth.account.middleware.AccountMiddleware',
    'accounts.middleware.ReferralCaptureMiddleware',
]

ROOT_URLCONF = 'drinkshop.urls'

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
                'store.context_processors.cart',
                'store.context_processors.site_settings',
            ],
        },
    },
]

WSGI_APPLICATION = 'drinkshop.wsgi.application'

# ---------------------------------------------------------------------------
# Database
# Defaults to SQLite for easy local dev. On serverless hosts like Vercel the
# filesystem is read-only/ephemeral, so SQLite can't persist there — set
# DATABASE_URL (Vercel Postgres, Neon, Supabase, etc.) and it takes over
# automatically. See README "Deploying to Vercel".
# ---------------------------------------------------------------------------
import dj_database_url

DATABASE_URL = os.environ.get('DATABASE_URL', '').strip().strip('"').strip("'")

# Vercel sometimes saves env vars with a stray leading ':' or quote —
# the resulting string starts with '://' and dj_database_url chokes on it.
# Validate before parsing.
_valid_schemes = (
    'postgres://', 'postgresql://', 'pgsql://',
    'mysql://', 'mysql2://',
    'sqlite://', 'sqlite3://',
)


def _is_valid_db_url(u):
    return bool(u) and any(u.startswith(s) for s in _valid_schemes)


if _is_valid_db_url(DATABASE_URL):
    DATABASES = {
        'default': dj_database_url.config(
            default=DATABASE_URL,
            conn_max_age=0 if os.environ.get('VERCEL') else 600,
            conn_health_checks=True,
            ssl_require=not DEBUG,
        )
    }

    # Supabase transaction pooler (port 6543) needs prepared statements
    # and server-side cursors disabled.
    try:
        if str(DATABASES['default'].get('PORT')) == '6543':
            DATABASES['default'].setdefault('OPTIONS', {})
            DATABASES['default']['OPTIONS']['prepare_threshold'] = None
            DATABASES['default']['DISABLE_SERVER_SIDE_CURSORS'] = True
    except Exception:
        pass

else:
    # DATABASE_URL missing or malformed — fall back to individual PG vars
    # if Vercel's Postgres integration set them, else SQLite.
    if os.environ.get('PGHOST') or os.environ.get('POSTGRES_HOST'):
        DATABASES = {
            'default': {
                'ENGINE': 'django.db.backends.postgresql',
                'NAME': os.environ.get('PGDATABASE', os.environ.get('POSTGRES_DATABASE', 'postgres')),
                'USER': os.environ.get('PGUSER', os.environ.get('POSTGRES_USER', 'postgres')),
                'PASSWORD': os.environ.get('PGPASSWORD', os.environ.get('POSTGRES_PASSWORD', '')),
                'HOST': os.environ.get('PGHOST', os.environ.get('POSTGRES_HOST', '')),
                'PORT': os.environ.get('PGPORT', os.environ.get('POSTGRES_PORT', '5432')),
                'CONN_MAX_AGE': 0 if os.environ.get('VERCEL') else 600,
                'OPTIONS': {'sslmode': 'require'} if not DEBUG else {},
            }
        }
    else:
        DATABASES = {
            'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': BASE_DIR / 'db.sqlite3',
            }
        }
# ---------------------------------------------------------------------------
# Password validation
# ---------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ---------------------------------------------------------------------------
# Internationalization
# ---------------------------------------------------------------------------
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Africa/Nairobi'
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static & media files
# Product images (and any other uploaded media) are stored on Cloudinary
# instead of local disk — required for hosts with an ephemeral filesystem,
# and gives free image CDN/transformations. Get credentials from
# https://cloudinary.com/console.
# ---------------------------------------------------------------------------
# WhiteNoise for static files
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']

# Vercel's runtime filesystem is read-only except /tmp
if os.environ.get('VERCEL'):
    STATIC_ROOT = '/tmp/staticfiles'
else:
    STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = '/media/'

CLOUDINARY_STORAGE = {
    'CLOUD_NAME': os.environ.get('CLOUDINARY_CLOUD_NAME', ''),
    'API_KEY': os.environ.get('CLOUDINARY_API_KEY', ''),
    'API_SECRET': os.environ.get('CLOUDINARY_API_SECRET', ''),
}

if CLOUDINARY_STORAGE['CLOUD_NAME']:
    _media_backend = 'cloudinary_storage.storage.MediaCloudinaryStorage'
else:
    _media_backend = 'django.core.files.storage.FileSystemStorage'
    MEDIA_ROOT = BASE_DIR / 'media'



STORAGES = {
    'default': {
        'BACKEND': _media_backend,
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage',
    },
}


DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ---------------------------------------------------------------------------
# Auth redirects
# ---------------------------------------------------------------------------
AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

LOGIN_URL = 'account_login'
LOGIN_REDIRECT_URL = 'store:home'
LOGOUT_REDIRECT_URL = 'store:home'

# ---------------------------------------------------------------------------
# django-allauth
# Email+password signup/login plus "Sign in with Google". Username is kept
# (not just email) since existing accounts/migrations use it; allauth will
# ask for one on signup unless you flip ACCOUNT_USERNAME_REQUIRED off.
# ---------------------------------------------------------------------------
ACCOUNT_EMAIL_VERIFICATION = os.environ.get('ACCOUNT_EMAIL_VERIFICATION', 'mandatory')  # 'none', 'optional', or 'mandatory'
ACCOUNT_LOGIN_METHODS = {'username', 'email'}
ACCOUNT_SIGNUP_FIELDS = ['email*', 'username*', 'password1*', 'password2*']
ACCOUNT_UNIQUE_EMAIL = True
SOCIALACCOUNT_LOGIN_ON_GET = True  # skip the "confirm" page, go straight to Google
SOCIALACCOUNT_AUTO_SIGNUP = True

SOCIALACCOUNT_PROVIDERS = {
    'google': {
        'APP': {
            'client_id': os.environ.get('GOOGLE_OAUTH_CLIENT_ID', ''),
            'secret': os.environ.get('GOOGLE_OAUTH_CLIENT_SECRET', ''),
            'key': '',
        },
        'SCOPE': ['profile', 'email'],
        'AUTH_PARAMS': {'access_type': 'online'},
    }
}

# ---------------------------------------------------------------------------
# Email — used for password reset, and order/payment/delivery notifications.
#
# On localhost (DJANGO_DEBUG=True, the default), email is ALWAYS printed to
# the console instead of actually sent — even if EMAIL_HOST is filled in in
# .env. This is deliberate: you don't want test orders on your laptop
# emailing real customers. Real SMTP only switches on once you deploy with
# DJANGO_DEBUG=False AND EMAIL_HOST set.
# ---------------------------------------------------------------------------
EMAIL_HOST = os.environ.get('EMAIL_HOST', '')
if EMAIL_HOST and not DEBUG:
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
    EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '587'))
    EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
    # Gmail displays app passwords with spaces for readability; strip them
    # so it doesn't matter whether you paste it with or without spaces.
    EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '').replace(' ', '')
    EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'True') == 'True'
    EMAIL_USE_SSL = os.environ.get('EMAIL_USE_SSL', 'False') == 'True'
else:
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', '')

# ---------------------------------------------------------------------------
# Shop settings
# ---------------------------------------------------------------------------
# Delivery fee in KES, charged per order (flat-rate; customize per zone later).
DELIVERY_FEE = int(os.environ.get('DELIVERY_FEE') or '150')
# Free delivery at both ends of the order-size spectrum: small orders
# (low-fuss threshold) and bulk/wholesale-size orders. Set either to 0 to
# disable that end. Everything in between pays the normal zone fee.
FREE_DELIVERY_MAX_SMALL_ORDER = int(os.environ.get('FREE_DELIVERY_MAX_SMALL_ORDER', '300'))
FREE_DELIVERY_MIN_WHOLESALE_ORDER = int(os.environ.get('FREE_DELIVERY_MIN_WHOLESALE_ORDER', '5000'))

# Default estimated delivery time (minutes) used in the "order received"
# email when the delivery address doesn't match a configured DeliveryZone
# (each zone can set its own estimated_minutes — see Store > Delivery zones).
DEFAULT_DELIVERY_ETA_MINUTES = int(os.environ.get('DEFAULT_DELIVERY_ETA_MINUTES', '60'))

# If True, alcoholic products (Product.is_alcoholic=True) cannot be sold /
# delivered via this site. Kenya's NACADA rules currently restrict online
# sale and home delivery of alcohol — keep this True unless your legal
# situation changes. See README for details.
DISALLOW_ALCOHOL_DELIVERY = os.environ.get('DISALLOW_ALCOHOL_DELIVERY', 'True') == 'True'

# Referral bonus (KES) credited to BOTH the referrer and the new signup
# when someone joins via a ?ref= link. Set to 0 to disable bonuses while
# keeping referral tracking itself active.
# Referral bonuses (KES) — two-stage payout.
#
# Stage 1 (email): paid to BOTH sides the moment the referred user
# verifies their email address. This proves the account is real
# (not a fake signup) without requiring a purchase.
#
# Stage 2 (first order): paid to BOTH sides when the referred user
# completes their first paid order.
#
# Set either to 0 to disable that stage while keeping referral tracking
# itself active. If a referred user never verifies their email, stage 1
# never pays out and stage 2 is also skipped (see
# Profile.release_first_order_bonus_if_due).

REFERRAL_EMAIL_BONUS_KES = int(os.environ.get('REFERRAL_EMAIL_BONUS_KES') or '1000')
REFERRAL_FIRST_ORDER_BONUS_KES = int(
    os.environ.get('REFERRAL_FIRST_ORDER_BONUS_KES') or '1500'
)

# Legacy — kept so any older references don't crash. Not used by the
# new two-stage payout logic.
REFERRAL_BONUS_KES = int(os.environ.get('REFERRAL_BONUS_KES') or '0')

# ---------------------------------------------------------------------------
# M-Pesa Daraja API settings (Safaricom)
# Get these from https://developer.safaricom.co.ke after creating an app.
# Leave the sandbox defaults in place for testing.
# ---------------------------------------------------------------------------
MPESA_ENV = os.environ.get('MPESA_ENV', 'sandbox')  # 'sandbox' or 'production'
MPESA_CONSUMER_KEY = os.environ.get('MPESA_CONSUMER_KEY', '')
MPESA_CONSUMER_SECRET = os.environ.get('MPESA_CONSUMER_SECRET', '')
MPESA_SHORTCODE = os.environ.get('MPESA_SHORTCODE', '174379')  # sandbox default
MPESA_PASSKEY = os.environ.get('MPESA_PASSKEY', '')
MPESA_CALLBACK_URL = os.environ.get(
    'MPESA_CALLBACK_URL', ''
)

if MPESA_ENV == 'production':
    MPESA_AUTH_URL = 'https://api.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials'
    MPESA_STK_PUSH_URL = 'https://api.safaricom.co.ke/mpesa/stkpush/v1/processrequest'
    MPESA_STK_QUERY_URL = 'https://api.safaricom.co.ke/mpesa/stkpushquery/v1/query'
    MPESA_B2C_URL = 'https://api.safaricom.co.ke/mpesa/b2c/v1/paymentrequest'
else:
    MPESA_AUTH_URL = 'https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials'
    MPESA_STK_PUSH_URL = 'https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest'
    MPESA_STK_QUERY_URL = 'https://sandbox.safaricom.co.ke/mpesa/stkpushquery/v1/query'
    MPESA_B2C_URL = 'https://sandbox.safaricom.co.ke/mpesa/b2c/v1/paymentrequest'

# --- B2C (wallet withdrawals — money OUT to a customer) ---
# A separate, more sensitive Daraja product from STK push. Requires
# Safaricom to have approved your account for B2C. See README "Wallet
# withdrawals" for the full application + setup process.
MPESA_INITIATOR_NAME = os.environ.get('MPESA_INITIATOR_NAME', '')
MPESA_INITIATOR_PASSWORD = os.environ.get('MPESA_INITIATOR_PASSWORD', '')
MPESA_B2C_SHORTCODE = os.environ.get('MPESA_B2C_SHORTCODE', MPESA_SHORTCODE)
MPESA_B2C_CERT_PATH = os.environ.get('MPESA_B2C_CERT_PATH', str(BASE_DIR / 'payments' / 'certs' / 'sandbox_cert.cer'))
MPESA_B2C_TIMEOUT_URL = os.environ.get('MPESA_B2C_TIMEOUT_URL', 'https://entrepshops.vercel.app/payments/mpesa/b2c/timeout/')
MPESA_B2C_RESULT_URL = os.environ.get('MPESA_B2C_RESULT_URL', 'https://entrepshops.vercel.app/payments/mpesa/b2c/result/')

# Minimum wallet balance a user must have to request a withdrawal.
WALLET_MIN_WITHDRAWAL_KES = int(os.environ.get('WALLET_MIN_WITHDRAWAL_KES', '1000'))

# Third-party sellers pay this much for this many days of platform access
# to list their own products (see the "vendors" app).
VENDOR_SUBSCRIPTION_FEE_KES = int(os.environ.get('VENDOR_SUBSCRIPTION_FEE_KES', '5000'))
VENDOR_SUBSCRIPTION_DAYS = int(os.environ.get('VENDOR_SUBSCRIPTION_DAYS', '30'))
# Shared secret for the /marketing/internal/process-proofs/ cron endpoint.
# Set CRON_SECRET in Vercel's environment variables to a random string.
CRON_SECRET = os.environ.get('CRON_SECRET', '')


# ---------------------------------------------------------------------------
# Shopify integration
# ---------------------------------------------------------------------------
SHOPIFY_STORE_DOMAIN = os.environ.get('SHOPIFY_STORE_DOMAIN', '')
SHOPIFY_ACCESS_TOKEN = os.environ.get('SHOPIFY_ACCESS_TOKEN', '')
SHOPIFY_API_VERSION = os.environ.get('SHOPIFY_API_VERSION', '2024-10')

# ---------------------------------------------------------------------------
# Django REST Framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 24,
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
        'rest_framework.renderers.BrowsableAPIRenderer',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.AllowAny',
    ],
}

# --- AI providers ---------------------------------------------------------
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
GEMINI_MODEL   = os.environ.get('GEMINI_MODEL', 'gemini-3.8-flash')

GROQ_API_KEY   = os.environ.get('GROQ_API_KEY', '')
GROQ_MODEL     = os.environ.get('GROQ_MODEL', 'llama-3.3-70b-versatile')
GROQ_VISION_MODEL = os.environ.get(
    'GROQ_VISION_MODEL', 'qwen/qwen3.8-27b'
)

# Marketer activation fee (KES) — paid monthly to keep generating share
# links and earning. Existing shares and withdrawals are never gated.
MARKETER_ACTIVATION_FEE_KES = int(os.environ.get('MARKETER_ACTIVATION_FEE_KES') or '1000')
MARKETER_ACTIVATION_DAYS = int(os.environ.get('MARKETER_ACTIVATION_DAYS') or '30')
# ---------------------------------------------------------------------------
# Google Gemini — AI product suggestions for vendors
# Get a free API key at https://aistudio.google.com/app/apikey
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# xAI Grok — AI product suggestions for vendors
# Get an API key at https://console.x.ai

ACCOUNT_ADAPTER = 'accounts.adapter.AccountAdapter'