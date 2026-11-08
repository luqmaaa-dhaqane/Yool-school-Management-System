from django.db import connection
from django.http import JsonResponse


def health(request):
    """Liveness/readiness probe: confirms the app and database respond."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return JsonResponse({"status": "ok"})
    except Exception:
        return JsonResponse({"status": "error"}, status=503)
