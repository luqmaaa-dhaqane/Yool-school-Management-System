from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView

from core.views import health

admin.site.site_header = settings.SITE_NAME
admin.site.site_title = settings.SITE_SHORT_NAME
admin.site.index_title = settings.SITE_SUBTITLE

urlpatterns = [
    path("", RedirectView.as_view(url="/admin/", permanent=False)),
    path("health/", health, name="health"),
    path("admin/", admin.site.urls),
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
