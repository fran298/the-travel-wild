import uuid
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction
from cities_light.models import Country, City
from directory.models import (
    Activity, School, SchoolActivity, Subscription, Media,
    SubscriptionPlan, SubscriptionStatus, SchoolStatus, MediaKind
)

# Helper para buscar ciudad por país (code2) y query (slug o nombre)
def get_city(country_code2: str, city_query: str) -> City:
    country = Country.objects.get(code2=country_code2.upper())
    qs = City.objects.filter(country=country)
    # 1) por slug exacto
    city = qs.filter(slug=city_query).first()
    if city:
        return city
    # 2) por nombre exacto (case-insensitive)
    city = qs.filter(name__iexact=city_query).first()
    if city:
        return city
    # 3) por contains (case-insensitive) para variantes
    city = qs.filter(name__icontains=city_query).order_by('name').first()
    return city

DEMO_SCHOOLS = [
    ("PT", "Lisbon",      "Surf PT Basic",    "surf-pt-basic",    SubscriptionPlan.BASIC,  False),
    ("PT", "Cascais",     "Kite PT Premium",  "kite-pt-premium",  SubscriptionPlan.PREMIUM, True),
    ("ES", "Barcelona",   "MTB ES Basic",     "mtb-es-basic",     SubscriptionPlan.BASIC,  False),
    ("ES", "Madrid",      "Escalada ES Med",  "escalada-es-med",  SubscriptionPlan.MEDIUM, True),
    ("PT", "Porto",       "Parapente PT Med", "parapente-pt-med", SubscriptionPlan.MEDIUM, False),
    ("ES", "Baqueira",    "Heliski ES Prem",  "heliski-es-prem",  SubscriptionPlan.PREMIUM, True),
]

ACTIVITY_BY_SLUG = {
    "surf-pt-basic": "surf",
    "kite-pt-premium": "kitesurf",
    "mtb-es-basic": "mountain-bike",
    "escalada-es-med": "escalada-deportiva",
    "parapente-pt-med": "parapente",
    "heliski-es-prem": "heliski",
}


def can_activate(plan: SubscriptionPlan, verified: bool) -> bool:
    plan_value = str(plan)
    return (plan_value == 'basic') or (plan_value in ('medium', 'premium') and verified is True)


class Command(BaseCommand):
    help = "Crea datos de demo (escuelas, suscripciones, media). Idempotente y sin romper reglas."

    def handle(self, *args, **options):
        created, updated = 0, 0
        for cc2, city_name, name, slug, plan, verified in DEMO_SCHOOLS:
            city = get_city(cc2, city_name)
            if not city:
                self.stdout.write(self.style.WARNING(f"Ciudad no encontrada: {cc2}/{city_name}, saltando {slug}"))
                continue

            # Un bloque transaccional por escuela, así un fallo no rompe el resto
            with transaction.atomic():
                school, was_created = School.objects.get_or_create(
                    slug=slug,
                    defaults={
                        "id": uuid.uuid4(),
                        "country_id": city.country_id,
                        "city_id": city.id,
                        "name": name,
                        "email": None,
                        "status": SchoolStatus.DRAFT,
                        "is_verified": verified,
                        "verification_status": "pending",
                        "created_at": timezone.now(),
                        "updated_at": timezone.now(),
                    },
                )

                if not was_created:
                    # Mantén país/ciudad/nombre sincronizados si cambiaron
                    changed = False
                    if school.country_id != city.country_id:
                        school.country_id = city.country_id; changed = True
                    if school.city_id != city.id:
                        school.city_id = city.id; changed = True
                    if school.name != name:
                        school.name = name; changed = True
                    if school.is_verified != verified:
                        school.is_verified = verified; changed = True
                    if changed:
                        school.updated_at = timezone.now()
                        school.save(update_fields=["country_id","city_id","name","is_verified","updated_at"])  # no toca status aún
                    updated += 1
                else:
                    created += 1

                # Vincular actividad principal (idempotente)
                act_slug = ACTIVITY_BY_SLUG[slug]
                activity = Activity.objects.get(slug=act_slug)
                SchoolActivity.objects.get_or_create(
                    school=school,
                    activity=activity,
                    defaults={"id": uuid.uuid4()},
                )

                # Asegurar UNA suscripción ACTIVA
                sub, _ = Subscription.objects.get_or_create(
                    school=school,
                    plan=plan,
                    status=SubscriptionStatus.ACTIVE,
                    defaults={
                        "id": uuid.uuid4(),
                        "starts_at": timezone.now() - timezone.timedelta(days=1),
                        "ends_at": timezone.now() + timezone.timedelta(days=29),
                        "created_at": timezone.now(),
                        "updated_at": timezone.now(),
                    },
                )

                # Estado de publicación SIN romper trigger: solo activamos si se puede
                if can_activate(plan, verified):
                    if school.status != SchoolStatus.ACTIVE:
                        school.status = SchoolStatus.ACTIVE
                        school.updated_at = timezone.now()
                        school.save(update_fields=["status","updated_at"])
                else:
                    # Asegura que quede en draft si no cumple reglas
                    if school.status != SchoolStatus.DRAFT:
                        school.status = SchoolStatus.DRAFT
                        school.updated_at = timezone.now()
                        school.save(update_fields=["status","updated_at"])

                # Media de demo (idempotente)
                Media.objects.get_or_create(
                    school=school,
                    kind=MediaKind.IMAGE,
                    storage_key=f"demo/{slug}-1.jpg",
                    defaults={
                        "id": uuid.uuid4(),
                        "position": 1,
                        "created_at": timezone.now(),
                        "updated_at": timezone.now(),
                    },
                )

        self.stdout.write(self.style.SUCCESS(f"Escuelas creadas: {created} | actualizadas: {updated}"))