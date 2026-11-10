"""Isolated GIS tests. Never load .env or import production settings."""

import os
import sys
from pathlib import Path
from core.security import security_settings

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = 'meal-drop-isolated-test-key-never-use-for-production-1234567890'
DEBUG = False
ALLOWED_HOSTS = ['testserver', 'localhost', '127.0.0.1']
globals().update(security_settings({}, debug=True))
# Existing domain tests do not share rate budgets. Security tests explicitly
# enable them and reset the isolated local store between test cases.
RATE_LIMIT_ENABLED = False
INSTALLED_APPS = [
    'django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes',
    'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles',
    'django.contrib.gis', 'rest_framework', 'rest_framework_simplejwt.token_blacklist',
    'users', 'vendors', 'products', 'orders', 'riders', 'multivender',
]
MIDDLEWARE = [
    'core.middleware.TrustedProxyMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'core.middleware.RequestGuardMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
]
ROOT_URLCONF = 'food_delivery.urls'
TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [BASE_DIR / 'templates'], 'APP_DIRS': True,
    'OPTIONS': {'context_processors': [
        'django.template.context_processors.request',
        'django.contrib.auth.context_processors.auth',
        'django.contrib.messages.context_processors.messages',
    ]},
}]
AUTH_USER_MODEL = 'users.CustomUser'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
TIME_ZONE = 'Asia/Colombo'
OTP_SMS_BYPASS_NUMBERS = {'0721992125', '0774610536'}
USE_TZ = True
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
MEDIA_ROOT = BASE_DIR / '.test-media'
MEDIA_URL = '/media/'
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.IsAuthenticated'],
    'DEFAULT_THROTTLE_CLASSES': ['core.throttling.APIRateLimitThrottle'],
    'EXCEPTION_HANDLER': 'core.throttling.api_exception_handler',
    'NUM_PROXIES': 0,
}
SIMPLE_JWT = {
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
}
MONGO_URI = None
MONGO_DATABASE = 'MealDrop'
MONGO_ROUTE_COLLECTION = 'multivendor_route_states'
GOOGLE_MAPS_API_KEY = ''

# A separately supplied test URL can exercise PostGIS. DATABASE_URL is ignored.
if os.environ.get('MEALDROP_TEST_DATABASE_URL'):
    import dj_database_url
    DATABASES = {'default': dj_database_url.parse(
        os.environ['MEALDROP_TEST_DATABASE_URL'], engine='django.contrib.gis.db.backends.postgis',
    )}
else:
    DATABASES = {'default': {'ENGINE': 'django.contrib.gis.db.backends.spatialite', 'NAME': ':memory:'}}

# On Linux the system GIS libraries are discovered by Django. Windows callers
# supply SpatiaLite; pyogrio can provide GDAL and GEOS for local verification.
_dll_handles = []
if sys.platform == 'win32':
    import ctypes
    import pyogrio

    _gdal_root = Path(pyogrio.__file__).resolve().parent.parent / 'pyogrio.libs'
    _dll_handles.append(os.add_dll_directory(str(_gdal_root)))
    GDAL_LIBRARY_PATH = os.environ.get('GDAL_LIBRARY_PATH') or str(next(_gdal_root.glob('gdal-*.dll')))
    GEOS_LIBRARY_PATH = os.environ.get('GEOS_LIBRARY_PATH') or str(next(_gdal_root.glob('geos_c-*.dll')))
    if os.environ.get('SPATIALITE_LIBRARY_PATH'):
        SPATIALITE_LIBRARY_PATH = os.environ['SPATIALITE_LIBRARY_PATH']
        _dll_handles.append(os.add_dll_directory(str(Path(SPATIALITE_LIBRARY_PATH).parent)))
        _dll_handles.append(ctypes.WinDLL(SPATIALITE_LIBRARY_PATH))
elif os.environ.get('SPATIALITE_LIBRARY_PATH'):
    SPATIALITE_LIBRARY_PATH = os.environ['SPATIALITE_LIBRARY_PATH']
