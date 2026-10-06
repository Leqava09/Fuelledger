from django.contrib import admin
from django.urls import include, path


urlpatterns = [
    path("grappelli/", include("grappelli.urls")),
    path("_nested_admin/", include("nested_admin.urls")),
    path("admin/", admin.site.urls),
    path("", include("ledger.urls")),
]