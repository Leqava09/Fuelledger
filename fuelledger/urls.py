from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.shortcuts import redirect
from django.urls import include, path
from ledger.admin import site_admin_site


def _admin_home(request):
    return redirect("hq-dashboard")


admin.site.index = _admin_home

urlpatterns = [
    path("grappelli/", include("grappelli.urls")),
    path("_nested_admin/", include("nested_admin.urls")),
    path("admin/", admin.site.urls),
    path("site-admin/", site_admin_site.urls),
    path("", include("ledger.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)