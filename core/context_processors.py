from django.conf import settings
from django.contrib.staticfiles import finders
from django.templatetags.static import static


def branding(request):
    """School name, subtitle and logo for every template. The logo shows only if the file exists."""
    logo_url = static(settings.SITE_LOGO_STATIC) if finders.find(settings.SITE_LOGO_STATIC) else ""
    return {
        "brand_name": settings.SITE_NAME,
        "brand_subtitle": settings.SITE_SUBTITLE,
        "brand_short_name": settings.SITE_SHORT_NAME,
        "brand_logo_url": logo_url,
    }
