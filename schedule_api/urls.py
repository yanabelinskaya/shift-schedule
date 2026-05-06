"""
URL configuration for schedule_api project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.templatetags.static import static as static_url
from django.http import HttpResponse
from django.urls import include, path
from django.views.generic.base import RedirectView
from accounts.web_views import login_view
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
from rest_framework.permissions import IsAuthenticated

urlpatterns = [
    path('healthz', lambda request: HttpResponse('ok', content_type='text/plain'), name='healthz'),
    path('', login_view, name='login'),
    path('login/', login_view, name='login-alt'),
    path('favicon.ico', RedirectView.as_view(url=static_url('favicon.ico'), permanent=True), name='favicon'),
    path('', include('accounts.web_urls')),
    path('api/', include('accounts.api_urls')),
    path('api/schema/', SpectacularAPIView.as_view(permission_classes=[IsAuthenticated]), name='schema'),
    path(
        'api/docs/swagger/',
        SpectacularSwaggerView.as_view(url_name='schema', permission_classes=[IsAuthenticated]),
        name='api-swagger-ui',
    ),
    path(
        'api/docs/redoc/',
        SpectacularRedocView.as_view(url_name='schema', permission_classes=[IsAuthenticated]),
        name='redoc',
    ),
    path(
        'swagger/',
        SpectacularSwaggerView.as_view(url_name='schema', permission_classes=[IsAuthenticated]),
        name='swagger-ui',
    ),
    path('admin/', admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
