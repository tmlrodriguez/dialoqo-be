from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),

    path("access/", include("access.urls")),
    path("organizations/", include("organizations.urls")),
    path("members/", include("members.urls")),
    path("whatsapp/", include("whatsapp.urls")),
    path("auditing/", include("auditing.urls")),
]
