from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

urlpatterns = [
    path("api/v1/", include(("config.api_router", "v1"), namespace="v1")),
    path("admin/", admin.site.urls),
]

if settings.API_DOCS_ENABLED:
    urlpatterns += [
        path("", SpectacularSwaggerView.as_view(url_name="schema"), name="api-docs-root"),
        path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
        path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
        path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    ]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    if "debug_toolbar" in settings.INSTALLED_APPS:
        urlpatterns.append(path("__debug__/", include("debug_toolbar.urls")))
