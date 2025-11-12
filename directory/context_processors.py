from django.urls import reverse
from django.core.cache import cache
from .models import Activity, School

def global_activities(request):
    """
    Provee la lista de actividades para el header en todas las páginas,
    sin depender de cada view.
    Cachea 10 minutos para no golpear la DB en cada request.
    """
    key = "nav_activities_v1"
    activities = cache.get(key)
    if activities is None:
        activities = Activity.objects.only("id", "name", "slug").order_by("name")
        cache.set(key, list(activities), 600)  # 10 minutos
    # Asegurarse de que activities sea una lista para iterar y modificar
    activities = list(activities)
    for act in activities:
        try:
            act.url = reverse("sport_detail", args=[act.slug])
        except Exception:
            act.url = "#"
    return {
        # nombre claro y sin colisiones
        "nav_activities": activities,
        # compat: si tu header esperaba "activities", lo dejamos también
        "activities": activities,
    }

def school_context(request):
    current_school = None
    if request.user.is_authenticated:
        current_school = School.objects.filter(email=request.user.email).first()
    return {'current_school': current_school}