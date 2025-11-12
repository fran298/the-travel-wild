import os
import json
import random
import logging
from decimal import Decimal

import stripe
from dal import autocomplete
from cities_light.models import Country, City

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.db import models, connection
from django.db.models import Q, Sum, Exists, OuterRef, IntegerField, Subquery
from django.http import JsonResponse, HttpResponse
from django.middleware.csrf import get_token
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import (
    Activity,
    School,
    SchoolActivity,
    SchoolStatus,
    PopularDestination,
    CityActivityImage,
    SchoolFinance,
    SchoolTransaction,
    SchoolSubscription,
    SchoolReview,
    SchoolActivitySession,
)
from .forms import SchoolSignupFormBasic, SchoolProfileCompletionForm

from django.utils.text import slugify

logger = logging.getLogger(__name__)

# ------------------------------------------------------------
# DAL Autocomplete Views
# ------------------------------------------------------------
class CountryAutocomplete(autocomplete.Select2QuerySetView):
    def get_queryset(self):
        qs = Country.objects.all()
        if self.q:
            qs = qs.filter(name__icontains=self.q)
        return qs


class CityAutocomplete(autocomplete.Select2QuerySetView):
    def get_queryset(self):
        qs = City.objects.all()
        if self.q:
            qs = qs.filter(name__icontains=self.q)
        return qs



# ------------------------------------------------------------
# Home / initial search
# ------------------------------------------------------------
def home(request):
    activity_slug = request.GET.get("activity")
    country_slug = request.GET.get("country")
    city_slug = request.GET.get("city")
    ctx = {}
    if activity_slug and country_slug and city_slug:
        return redirect(
            "sports_list",
            activity_slug=activity_slug,
            country_slug=country_slug,
            city_slug=city_slug,
        )

    ctx["activities"] = Activity.objects.order_by("name").all()
    ctx["countries"] = Country.objects.order_by("name").all()
    ctx["hint"] = "Select activity, country, and city, then search."

    popular_qs = (
        PopularDestination.objects.filter(is_active=True)
        .select_related("city")
        .order_by("-created_at")[:8]
    )
    popular = list(popular_qs)
    ctx["popular_slides"] = [popular[i:i + 4] for i in range(0, len(popular), 4)]

    return render(request, "directory/home.html", ctx)


# ------------------------------------------------------------
# Search redirect view (smart resolver)
# ------------------------------------------------------------
def search_redirect(request):
    """
    Smart redirect for search: resolves activity and destination to the best matching URL.
    Priority:
        1. City + Activity → /schools/<country>/<city>/?activity=<activity>
        2. City only → /schools/<country>/<city>/
        3. Activity only → /schools/?activity=<activity>
        4. Country only → /schools/<country>/
        5. Fallback → /
    """
    from django.urls import reverse
    from .models import Activity, School
    from cities_light.models import City, Country
    from django.utils.text import slugify
    from django.db.models import Q, Count

    activity_param = request.GET.get("activity", "").strip()
    destination_param = request.GET.get("destination", "").strip()
    activity = None
    city = None
    country = None

    # --- Resolve activity ---
    if activity_param:
        activity_slug = slugify(activity_param)
        activity = Activity.objects.filter(slug__iexact=activity_slug).first() or Activity.objects.filter(name__iexact=activity_param).first()

    # --- Resolve destination ---
    if destination_param:
        dest_slug = slugify(destination_param)
        # Try to match a city first
        cities = City.objects.filter(
            Q(slug__iexact=dest_slug)
            | Q(name__iexact=destination_param)
            | Q(name__icontains=destination_param)
        ).select_related("country")

        if cities.exists():
            if cities.count() > 1:
                cities = cities.annotate(num_schools=Count("school"))
                city = cities.order_by("-num_schools").first()
            else:
                city = cities.first()

            # ✅ fuerza obtener el país directamente de la ciudad
            country = city.country if hasattr(city, "country") and city.country else None

        else:
            # Try to match a country directly
            country = Country.objects.filter(
                Q(slug__iexact=dest_slug) | Q(name__iexact=destination_param)
            ).first()
            # If country not found, try to guess by a city name with similar pattern
            if not country:
                city_guess = City.objects.filter(name__icontains=destination_param).select_related("country").first()
                if city_guess:
                    city = city_guess
                    country = city_guess.country

    # --- Ensure country from city (forzar siempre si hay city) ---
    if city:
        try:
            country = city.country
        except Exception:
            country = None

    # --- As a last resort, infer country from an active school in that city ---
    if not country and city:
        school = School.objects.filter(city=city).select_related("country").first()
        if school:
            country = school.country

    # --- Build final redirect ---
    if activity and city and country:
        return redirect(f"/schools/{country.slug}/{city.slug}/?activity={activity.slug}")
    elif city and country:
        return redirect(f"/schools/{country.slug}/{city.slug}/")
    elif country and activity:
        return redirect(f"/schools/{country.slug}/?activity={activity.slug}")
    elif activity:
        return redirect(f"/schools/?activity={activity.slug}")
    elif country:
        return redirect(f"/schools/{country.slug}/")
    else:
        return redirect("/")


# ------------------------------------------------------------
# Helper to resolve country and city
# ------------------------------------------------------------
def _resolve_country_and_city(country_slug, city_slug_or_name):
    country = get_object_or_404(Country, slug__iexact=country_slug)

    try:
        city = City.objects.get(country=country, slug__iexact=city_slug_or_name)
        return country, city, None
    except City.DoesNotExist:
        pass

    name_qs = City.objects.filter(country=country, name__iexact=city_slug_or_name)
    if name_qs.count() == 1:
        return country, name_qs.first(), None

    suggestions = (
        City.objects.filter(country=country, name__icontains=city_slug_or_name)
        .order_by("name")[:10]
    )
    if suggestions:
        return country, None, list(suggestions)

    return country, None, []


# ------------------------------------------------------------
# Financial helper
# ------------------------------------------------------------


# ------------------------------------------------------------
# List schools by sport / city / country
# ------------------------------------------------------------
def sports_list(request, activity_slug, country_slug, city_slug):
    activity = get_object_or_404(Activity, slug=activity_slug)
    country, city, suggestions = _resolve_country_and_city(country_slug, city_slug)

    if city is None and suggestions is not None:
        return render(
            request,
            "directory/city_ambiguous.html",
            {"activity": activity, "country": country, "query": city_slug, "suggestions": suggestions},
        )

    if city.slug != city_slug:
        return redirect(
            "sports_list",
            activity_slug=activity.slug,
            country_slug=country.slug,
            city_slug=city.slug,
        )

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM activity_allowed_geo_mv WHERE activity_id=%s AND country_id=%s AND city_id=%s LIMIT 1",
            [activity.id, country.id, city.id],
        )
        allowed = cursor.fetchone()
    if not allowed:
        return render(
            request,
            "directory/not_allowed.html",
            {"activity": activity, "country": country, "city": city},
        )

    qs = (
        School.objects.filter(
            status=SchoolStatus.ACTIVE,
            city=city,
            schoolactivity__activity=activity,
        )
        .order_by("name")
        .distinct()
    )

    # Gather available levels, difficulties, and experience types from SchoolActivityVariants
    from .models import SchoolActivityVariant
    variant_qs = SchoolActivityVariant.objects.filter(
        school_activity__activity=activity,
        school_activity__school__in=qs,
        is_active=True,
    )

    # Collect unique levels, difficulties, and experiences
    available_levels = set()
    available_difficulties = set()
    available_experiences = set()
    for variant in variant_qs:
        # Levels
        levels = getattr(variant, "levels", None)
        if isinstance(levels, list):
            available_levels.update(levels)
        elif levels:
            available_levels.add(levels)
        # Difficulties
        difficulties = getattr(variant, "difficulty", None)
        if isinstance(difficulties, list):
            available_difficulties.update(difficulties)
        elif difficulties:
            available_difficulties.add(difficulties)
        # Experiences
        experiences = getattr(variant, "experience_type", None)
        if isinstance(experiences, list):
            available_experiences.update(experiences)
        elif experiences:
            available_experiences.add(experiences)

    available_levels = sorted([lvl for lvl in available_levels if lvl])
    available_difficulties = sorted([dif for dif in available_difficulties if dif])
    available_experiences = sorted([exp for exp in available_experiences if exp])

    return render(
        request,
        "directory/sports_list.html",
        {
            "activity": activity,
            "country": country,
            "city": city,
            "schools": qs,
            "available_levels": available_levels,
            "available_difficulties": available_difficulties,
            "available_experiences": available_experiences,
        },
    )


# ------------------------------------------------------------
# School detail
# ------------------------------------------------------------
def school_detail(request, country_slug, city_slug, school_slug):
    school = get_object_or_404(
        School,
        slug=school_slug,
        city__slug=city_slug,
        city__country__slug=country_slug
    )

    # Properly load all school activities with related activity, variants, and seasons and sessions
    acts = (
        SchoolActivity.objects.filter(school=school)
        .select_related("activity")
        .prefetch_related(
            "variants",
            "seasons",
            "variants__sessions",
        )
    )

    from django.db.models import Avg

    reviews = SchoolReview.objects.filter(school=school).select_related("user").order_by("-created_at")
    average_rating = reviews.aggregate(Avg("rating"))["rating__avg"] or 0
    review_count = reviews.count()

    finance = getattr(school, "finance", None)
    plan = finance.plan if finance else None

    # Get all sessions for this school's activities, grouped by activity
    sessions_qs = SchoolActivitySession.objects.filter(variant__school_activity__school=school).select_related(
        "variant", "variant__school_activity"
    )
    # Group sessions by activity
    sessions_by_activity = {}
    for session in sessions_qs:
        activity_id = session.variant.school_activity_id
        sessions_by_activity.setdefault(activity_id, []).append(session)

    activities_list = [sa.activity for sa in acts]
    return render(
        request,
        "directory/school_detail.html",
        {
            "school": school,
            "school_activities": acts,
            "plan": plan,
            "plan_rank": 0,
            "reviews": reviews,
            "average_rating": average_rating,
            "review_count": review_count,
            "sessions": sessions_by_activity,
            "activities_list": activities_list,
        },
    )


# ------------------------------------------------------------
# Create review
# ------------------------------------------------------------
@login_required(login_url="/login/")
def add_review(request, slug):
    from django.core.exceptions import ValidationError

    school = get_object_or_404(School, slug=slug)
    if request.method == "POST":
        rating = request.POST.get("rating")
        comment = request.POST.get("comment")
        user = request.user

        existing_review = SchoolReview.objects.filter(user=user, school=school).first()
        if existing_review:
            existing_review.rating = rating
            existing_review.comment = comment
            existing_review.save()
            messages.success(request, "Your review has been successfully updated.")
        else:
            try:
                review = SchoolReview(school=school, user=user, rating=rating, comment=comment)
                review.full_clean()
                review.save()
                messages.success(request, "Your review has been published successfully.")
            except ValidationError as e:
                messages.error(request, e.message)
    return redirect("school_detail", slug=slug)


# ------------------------------------------------------------
# Pricing / Plans
# ------------------------------------------------------------
def pricing(request):
    school = None
    is_premium = False
    stripe_ok = False

    if request.user.is_authenticated:
        school = School.objects.filter(email=request.user.email).select_related("finance").first()
        if school and getattr(school, "finance", None):
            finance = school.finance
            is_premium = (finance.plan == "premium")
            stripe_ok = bool(finance.stripe_account_id and finance.is_stripe_verified)

    return render(request, "directory/pricing.html", {
        "school": school,
        "is_premium": is_premium,
        "stripe_ok": stripe_ok,
    })


# -------------------------------
# Premium Checkout Flow (Stripe)
# -------------------------------


# Helper to ensure minimal School exists for the user
def _ensure_minimal_school_for_user(user):
    """Ensure there is a minimal School record for this user; create one if missing."""
    school = School.objects.filter(email=user.email).first()
    if school:
        return school
    # Create a lightweight school record so the user can subscribe now and finish setup later
    from uuid import uuid4
    default_country = Country.objects.first()
    default_city = City.objects.filter(country=default_country).first()
    school = School.objects.create(
        id=uuid4(),
        name=(user.email.split("@")[0] or "My School").replace(".", " ").title(),
        email=user.email,
        country=default_country,
        city=default_city,
        slug=f"{(user.email.split('@')[0]).replace('.', '-').lower()}-{uuid4().hex[:6]}",
        status=SchoolStatus.PENDING,
        verification_status="pending",
        is_verified=False,
    )
    return school


@login_required
def create_premium_checkout_session(request):
    """
    Starts a Stripe Billing Checkout (annual subscription €499) for Schools only.
    Ensures the user has an associated School (creating a minimal one if needed),
    prevents duplicate subscriptions, and redirects to Stripe Checkout.
    """
    if request.method not in ["POST", "GET"]:
        return HttpResponse(status=405)

    user = request.user
    school = _ensure_minimal_school_for_user(user)
    finance = school.ensure_finance()

    if finance.plan == "premium" and finance.subscription_active:
        messages.info(request, "Your school already has an active Premium plan.")
        return redirect("pricing")

    stripe.api_key = settings.STRIPE_SECRET_KEY
    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": settings.STRIPE_PREMIUM_PRICE_ID, "quantity": 1}],
            customer_email=user.email,
            success_url=request.build_absolute_uri(reverse("verify_email_code")) + "?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=request.build_absolute_uri(reverse("pricing")),
            metadata={
                "kind": "premium_subscription",
                "school_id": str(school.id),
            },
        )
        return redirect(session.url)
    except stripe.error.StripeError as e:
        messages.error(request, f"Stripe error: {getattr(e, 'user_message', str(e))}")
        return redirect("pricing")


def premium_success_view(request):
    messages.success(request, "✅ Your Premium subscription has been processed. It will be activated within a few minutes.")
    return redirect("pricing")


def premium_cancel_view(request):
    messages.info(request, "You have canceled the Premium subscription process.")
    return redirect("pricing")




# ------------------------------------------------------------
# Stripe: account verification
# ------------------------------------------------------------

def check_stripe_account_status(request, slug):
    school = get_object_or_404(School, slug=slug)
    stripe.api_key = settings.STRIPE_SECRET_KEY
    try:
        finance = school.ensure_finance()
        if not finance.stripe_account_id:
            return redirect("school_detail", slug=slug)
        account = stripe.Account.retrieve(finance.stripe_account_id)
        verified = bool(account.charges_enabled and account.details_submitted)
        if finance.is_stripe_verified != verified:
            finance.is_stripe_verified = verified
            finance.save(update_fields=["is_stripe_verified"])
    except stripe.error.StripeError:
        pass
    return redirect("school_detail", slug=slug)


# ------------------------------------------------------------
# Stripe: Express account connection and onboarding
# ------------------------------------------------------------

@login_required
def connect_stripe_account_view(request):
    """
    Creates or reconnects the Stripe Express account for the current school.
    If an account already exists, generates a new link to access the Stripe dashboard.
    """
    import stripe
    stripe.api_key = settings.STRIPE_SECRET_KEY

    school = get_object_or_404(School, email=request.user.email)
    finance = school.ensure_finance()

    try:
        # Crear cuenta si no existe
        if not finance.stripe_account_id:
            account = stripe.Account.create(
                type="express",
                country=school.country.code if hasattr(school.country, "code") else "US",
                email=school.email,
                capabilities={
                    "card_payments": {"requested": True},
                    "transfers": {"requested": True},
                },
            )
            finance.stripe_account_id = account.id
            finance.save(update_fields=["stripe_account_id"])
        else:
            account = stripe.Account.retrieve(finance.stripe_account_id)

        # Generar link de onboarding o dashboard
        if not account.details_submitted or not account.charges_enabled:
            account_link = stripe.AccountLink.create(
                account=account.id,
                refresh_url=request.build_absolute_uri(reverse("refresh_stripe_link_view")),
                return_url=request.build_absolute_uri(reverse("onboarding_complete_view")),
                type="account_onboarding",
            )
            return redirect(account_link.url)
        else:
            # Ya verificada: redirigir al dashboard de Stripe Express
            login_link = stripe.AccountLink.create(
                account=account.id,
                refresh_url=request.build_absolute_uri(reverse("refresh_stripe_link_view")),
                return_url=request.build_absolute_uri(reverse("onboarding_complete_view")),
                type="account_update",
            )
            return redirect(login_link.url)

    except stripe.error.StripeError as e:
        messages.error(request, f"Stripe error: {e.user_message}")
        return redirect("school_finance")


@login_required
def refresh_stripe_link_view(request):
    """
    Allows retrying the onboarding process if the user interrupted it.
    """
    school = get_object_or_404(School, email=request.user.email)
    finance = school.ensure_finance()
    stripe.api_key = settings.STRIPE_SECRET_KEY
    try:
        account_link = stripe.AccountLink.create(
            account=finance.stripe_account_id,
            refresh_url=request.build_absolute_uri(reverse("refresh_stripe_link_view")),
            return_url=request.build_absolute_uri(reverse("onboarding_complete_view")),
            type="account_onboarding",
        )
        return redirect(account_link.url)
    except stripe.error.StripeError as e:
        messages.error(request, f"Stripe error: {e.user_message}")
        return redirect("school_finance")


@login_required
def onboarding_complete_view(request):
    """
    Safe return page from Stripe when onboarding is completed.
    """
    school = get_object_or_404(School, email=request.user.email)
    finance = school.ensure_finance()
    stripe.api_key = settings.STRIPE_SECRET_KEY
    try:
        account = stripe.Account.retrieve(finance.stripe_account_id)
        if account.charges_enabled and account.details_submitted:
            if not finance.is_stripe_verified:
                finance.is_stripe_verified = True
                finance.save(update_fields=["is_stripe_verified"])
            messages.success(request, "✅ Stripe account successfully connected and verified!")
        else:
            messages.info(request, "Your Stripe account setup is still pending verification.")
    except stripe.error.StripeError as e:
        messages.error(request, f"Stripe error: {e.user_message}")
    return redirect("school_finance")


# ------------------------------------------------------------
# School finances view (dashboard)
# ------------------------------------------------------------


@login_required
def school_finance(request):
    school = get_object_or_404(School, email=request.user.email)
    finance = school.ensure_finance()

    # Ensure fee_percent is taken from the plan (not recalculated dynamically)
    fee_percent = getattr(finance, "fee_percent", None)
    if not fee_percent:
        # fallback if plan logic applies
        fee_percent = Decimal("20.0") if finance.plan == "premium" else Decimal("25.0")
    fee_rate = fee_percent / Decimal(100)

    # Retrieve all transactions (direct or via booking)
    transactions = SchoolTransaction.objects.filter(
        Q(school=school) | Q(booking__school=school)
    )

    # Calcular totales según estado de liberación
    gross_total = transactions.aggregate(models.Sum("amount"))["amount__sum"] or Decimal(0)
    net_total = transactions.filter(is_released=True).aggregate(models.Sum("net_amount"))["net_amount__sum"] or Decimal(0)

    # Nuevo bloque para pending_payouts
    pending_payouts = transactions.filter(
        Q(is_released=False) | Q(is_released__isnull=True)
    ).aggregate(total=Sum("net_amount"))["total"] or Decimal(0)

    context = {
        'school': school,
        'finance': finance,
        'fee_percent': fee_percent,
        'gross_total': gross_total,
        'net_total': net_total,
        'pending_payouts': pending_payouts,
        'transactions': transactions.order_by('-created_at'),
    }

    return render(request, 'directory/school_finances.html', context)


# ------------------------------------------------------------
# Stripe Webhook (updated: does not create transactions, only marks as paid)
# ------------------------------------------------------------


@csrf_exempt
@require_POST
def stripe_webhook(request):
    logger.info("⚠️ Stripe webhook received but disabled for manual payout mode.")
    return HttpResponse(status=200)


# ------------------------------------------------------------
# Create PaymentIntent (updated: funds remain on platform)
# ------------------------------------------------------------

def create_payment_intent(request, country_slug, city_slug, school_slug):
    """
    Create a Stripe PaymentIntent for a school booking.
    Supports date-only bookings (session_date) without requiring a specific time.
    After creation, the booking is marked as PAID_PENDING_RELEASE or CONFIRMED.
    """
    school = get_object_or_404(
        School,
        slug=school_slug,
        city__slug=city_slug,
        city__country__slug=country_slug
    )
    finance = school.ensure_finance()
    if not finance.is_stripe_verified or not finance.stripe_account_id:
        return JsonResponse({"error": "School Stripe account is not verified."}, status=400)

    stripe.api_key = settings.STRIPE_SECRET_KEY
    fee_rate = finance.get_fee_rate()
    fee_percent = (fee_rate * Decimal(100)).quantize(Decimal("0.01"))

    variant_id = request.POST.get("variant_id")
    participants = int(request.POST.get("participants", "1"))
    booking_id = request.POST.get("booking_id")
    session_date = request.POST.get("session_date")  # Optional: only date, no time

    # Metadata para Stripe
    metadata = {
        "school_id": str(school.id),
        "fee_percent": str(fee_percent),
        "participants": str(participants),
    }
    if variant_id:
        metadata["variant_id"] = str(variant_id)
    if booking_id:
        metadata["booking_id"] = str(booking_id)
    if session_date:
        metadata["session_date"] = session_date

    # Session_id ya no es obligatorio
    session_id = request.POST.get("session_id")
    if session_id:
        from .models import SchoolActivitySession
        session_obj = SchoolActivitySession.objects.filter(id=session_id, variant_id=variant_id).first()
        if session_obj:
            metadata["session_id"] = str(session_id)

    # Calcular monto total
    total_amount_cents = None
    if variant_id:
        from .models import SchoolActivityVariant
        variant = SchoolActivityVariant.objects.filter(
            id=variant_id, school_activity__school=school
        ).first()
        if not variant:
            return JsonResponse({"error": "Invalid variant or not associated with this school."}, status=400)
        if not hasattr(variant, "price") or variant.price is None:
            return JsonResponse({"error": "Variant has no valid price."}, status=400)
        try:
            total_amount_cents = int((variant.price * Decimal(participants) * 100).quantize(Decimal("1")))
        except Exception:
            return JsonResponse({"error": "Error calculating total amount."}, status=400)
    else:
        amount_eur_str = request.POST.get("amount")
        try:
            total_amount_cents = int((Decimal(amount_eur_str) * 100).quantize(Decimal("1")))
        except Exception:
            return JsonResponse({"error": "Invalid amount."}, status=400)

    # Actualizar reserva si ya existe (solo fecha)
    if booking_id:
        try:
            from .models import Booking
            booking = Booking.objects.filter(id=booking_id).first()
            if booking and session_date and hasattr(booking, "session_date"):
                booking.session_date = session_date
                booking.save(update_fields=["session_date"])
        except Exception:
            pass

    # Crear PaymentIntent
    try:
        intent = stripe.PaymentIntent.create(
            amount=total_amount_cents,
            currency="eur",
            payment_method_types=["card"],
            metadata=metadata,
        )
    except stripe.error.StripeError as e:
        return JsonResponse({"error": str(e)}, status=400)

    # Marcar booking como pagada pendiente de liberación (o confirmada)
    if booking_id:
        try:
            from .models import Booking, BookingStatus
            booking = Booking.objects.filter(id=booking_id).first()
            if booking:
                paid_status = getattr(BookingStatus, "PAID_PENDING_RELEASE", None) or getattr(BookingStatus, "CONFIRMED", None)
                if paid_status:
                    booking.status = paid_status
                    booking.save(update_fields=["status"])
        except Exception:
            pass

    return JsonResponse({"client_secret": intent.client_secret})


# ------------------------------------------------------------
# Marca la reserva como pagada/confirmada tras éxito en Stripe
# ------------------------------------------------------------
@login_required
@require_POST
def booking_mark_paid(request, booking_id):
    """
    Marca una reserva como pagada/confirmada cuando el pago en Stripe se completa con éxito.
    """
    from .models import Booking, BookingStatus
    stripe.api_key = settings.STRIPE_SECRET_KEY

    booking = get_object_or_404(Booking, id=booking_id, user=request.user)
    payment_intent_id = request.POST.get("payment_intent")

    if not payment_intent_id:
        return JsonResponse({"error": "Missing payment_intent"}, status=400)

    try:
        pi = stripe.PaymentIntent.retrieve(payment_intent_id)
    except Exception as e:
        return JsonResponse({"error": f"Stripe error: {e}"}, status=400)

    if pi and pi.get("status") == "succeeded":
        # Estado final confirmado
        confirmed_status = getattr(BookingStatus, "CONFIRMED", None) or getattr(BookingStatus, "PAID", None)
        if confirmed_status is None:
            confirmed_status = BookingStatus.COMPLETED

        booking.status = confirmed_status
        booking.stripe_payment_intent = pi["id"]
        booking.save(update_fields=["status", "stripe_payment_intent"])

        # --- Send booking emails ---
        from .utils import send_booking_emails
        try:
            send_booking_emails(booking)
            logger.info(f"📩 Emails sent successfully for booking {booking.id}")
        except Exception as e:
            logger.error(f"❌ Error sending booking emails for {booking.id}: {e}")

        # --- Create a SchoolTransaction record for manual payout tracking ---
        try:
            from .models import SchoolTransaction
            from decimal import Decimal
            finance = booking.school.ensure_finance()
            fee_percent = Decimal("20.00") if getattr(finance, "plan", "") == "premium" else Decimal("25.00")

            SchoolTransaction.objects.create(
                school=booking.school,
                booking=booking,
                amount=booking.amount,
                fee_percent=fee_percent,
                fee_amount=booking.amount * (fee_percent / Decimal(100)),
                net_amount=booking.amount * (Decimal(1) - (fee_percent / Decimal(100))),
                is_released=False,
            )
            logger.info(f"✅ SchoolTransaction created for booking {booking.id}")
        except Exception as e:
            logger.error(f"❌ Error creating SchoolTransaction: {e}")

        # Intentar registrar pago (si existe modelo BookingPayment)
        try:
            from .models import BookingPayment
            BookingPayment.objects.get_or_create(
                booking=booking,
                stripe_payment_intent=pi["id"],
                defaults={"amount": Decimal(pi["amount"]) / 100},
            )
        except Exception:
            pass

        return JsonResponse({"ok": True})

    return JsonResponse({"error": "Payment not succeeded"}, status=400)


# ------------------------------------------------------------
# School dashboard: update booking status (manual payout mode)
# ------------------------------------------------------------
@login_required
def school_update_booking(request, booking_id):
    """
    Permite a la escuela actualizar el estado de una reserva (completed, partial, no-show, etc).
    Tras actualizar, crea SchoolTransaction para seguimiento manual del payout
    y envía siempre los emails correspondientes.
    """
    from .models import Booking, SchoolTransaction
    from .utils import send_booking_emails, send_payout_notification
    from decimal import Decimal

    booking = get_object_or_404(Booking, id=booking_id, school__email=request.user.email)

    if request.method == "POST":
        new_status = request.POST.get("status")
        if new_status and new_status != booking.status:
            booking.status = new_status
            booking.save(update_fields=["status"])
            logger.info(f"✅ Booking {booking.id} updated to status: {new_status}")

            # --- Send booking confirmation emails ---
            try:
                send_booking_emails(booking)
                logger.info(f"📩 Booking emails sent successfully for booking {booking.id}")
            except Exception as e:
                logger.error(f"❌ Error sending booking emails for {booking.id}: {e}")

            # --- Create SchoolTransaction record for manual payout tracking ---
            try:
                finance = booking.school.ensure_finance()
                fee_percent = Decimal("20.00") if getattr(finance, "plan", "") == "premium" else Decimal("25.00")
                SchoolTransaction.objects.create(
                    school=booking.school,
                    booking=booking,
                    amount=booking.amount,
                    fee_percent=fee_percent,
                    fee_amount=booking.amount * (fee_percent / Decimal(100)),
                    net_amount=booking.amount * (Decimal(1) - (fee_percent / Decimal(100))),
                    is_released=False,
                )
                logger.info(f"💰 SchoolTransaction created for booking {booking.id}")
            except Exception as e:
                logger.error(f"❌ Error creating SchoolTransaction on update: {e}")

            # --- Send payout notification (email only, no Stripe logic) ---
            try:
                send_payout_notification(booking)
                logger.info(f"📤 Payout notification email sent for booking {booking.id}")
            except Exception as e:
                logger.error(f"❌ Error sending payout notification email for {booking.id}: {e}")

    return redirect("school_finance")


# ------------------------------------------------------------
# Variant sessions API (updated: SchoolActivitySeason support)
# ------------------------------------------------------------

@login_required
def variant_sessions_api(request, variant_id):
    """
    Devuelve las fechas disponibles para reserva en función de:
      1. Sesiones específicas futuras (SchoolActivitySession)
      2. Temporadas (SchoolActivitySeason): start_month / end_month
    """
    from datetime import date
    import calendar
    from .models import (
        SchoolActivityVariant,
        SchoolActivitySession,
        SchoolActivitySeason,
    )

    variant = get_object_or_404(SchoolActivityVariant, id=variant_id)
    now = timezone.now()

    # 1️⃣ Sesiones concretas
    sessions = (
        SchoolActivitySession.objects.filter(
            variant=variant, is_available=True, date_start__gte=now
        )
        .order_by("date_start")
    )
    if sessions.exists():
        results = []
        for s in sessions:
            try:
                date_str = s.date_start.date().isoformat()
            except Exception:
                date_str = getattr(s, "date_start", None)
                if hasattr(date_str, "isoformat"):
                    date_str = date_str.isoformat()
            results.append({"id": str(s.id), "date": date_str})
        return JsonResponse({"sessions": results, "season": None})

    # 2️⃣ Buscar temporada activa
    season = (
        SchoolActivitySeason.objects.filter(
            school_activity=variant.school_activity, is_active=True
        )
        .order_by("-created_at")
        .first()
    )

    if not season:
        return JsonResponse({"sessions": [], "season": None})

    # 3️⃣ Construir rango desde start_month / end_month
    today = timezone.now().date()
    year = today.year

    def first_day(y, m):
        return date(y, m, 1)

    def last_day(y, m):
        return date(y, m, calendar.monthrange(y, m)[1])

    start_date = None
    end_date = None

    if season.start_month and season.end_month:
        sm = int(season.start_month)
        em = int(season.end_month)

        if em >= sm:
            start_date = first_day(year, sm)
            end_date = last_day(year, em)
        else:
            # Temporada que cruza el año (ej. Oct → Apr)
            start_date = first_day(year, sm)
            end_date = last_day(year + 1, em)

    elif season.free_dates:
        # Fallback si tiene free_dates definidas
        free_list = sorted(season.free_dates)
        if free_list:
            start_date = date.fromisoformat(free_list[0])
            end_date = date.fromisoformat(free_list[-1])

    # Si no hay fechas válidas, nada que devolver
    if not (start_date and end_date):
        return JsonResponse({"sessions": [], "season": None})

    payload = {
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "season_type": season.season_type or "regular",
        "description": season.description or "",
    }

    return JsonResponse({"sessions": [], "season": payload})



# ------------------------------------------------------------
# City detail (premium activities)
# ------------------------------------------------------------
def city_detail(request, country_slug, city_slug):
    country, city, suggestions = _resolve_country_and_city(country_slug, city_slug)
    if city is None and suggestions is not None:
        return render(
            request,
            "directory/city_ambiguous.html",
            {"country": country, "query": city_slug, "suggestions": suggestions},
        )
    if city.slug != city_slug:
        return redirect("city_detail", country_slug=country.slug, city_slug=city.slug)

    # Active schools in this city
    active_schools = School.objects.filter(
        status=SchoolStatus.ACTIVE, city=city
    ).select_related("country", "city", "finance")

    has_schools = active_schools.exists()
    premium_schools = active_schools.filter(finance__plan__iexact="premium")
    basic_schools = active_schools.exclude(finance__plan__iexact="premium")

    has_premium_schools = premium_schools.exists()
    has_basic_schools = basic_schools.exists()
    show_top_schools = premium_schools.exists()
    show_explore_block = basic_schools.exists() or premium_schools.exists()

    # Related activities (only if schools exist)
    activity_ids = (
        SchoolActivity.objects.filter(school__in=active_schools)
        .values_list("activity_id", flat=True)
        .distinct()
    )
    activities_qs = Activity.objects.filter(id__in=activity_ids).order_by("name")

    activities_payload = []
    for act in activities_qs:
        act_schools = active_schools.filter(school_activities__activity=act).distinct()
        activities_payload.append({"activity": act, "schools": act_schools})

    selected_slug = request.GET.get("activity")
    if not selected_slug or not activities_qs.filter(slug=selected_slug).exists():
        selected_slug = activities_qs.first().slug if activities_qs.exists() else None
    selected_obj = activities_qs.filter(slug=selected_slug).first() if selected_slug else None

    gallery_images = []
    if selected_obj:
        gallery_images = CityActivityImage.objects.filter(
            gallery__city=city, gallery__activity=selected_obj
        ).order_by("position")[:5]

    # Add global activities for header
    activities_global = Activity.objects.order_by("name").all()

    return render(
        request,
        "directory/city_detail.html",
        {
            "country": country,
            "city": city,
            "activities_payload": activities_payload,
            "activities": activities_global,
            "selected_activity": selected_slug,
            "selected_activity_obj": selected_obj,
            "gallery_images": gallery_images,
            "has_schools": has_schools,
            "has_premium_schools": has_premium_schools,
            "has_basic_schools": has_basic_schools,
            "premium_schools": premium_schools,
            "schools": active_schools,
            "show_top_schools": show_top_schools,
            "show_explore_block": show_explore_block,
        },
    )


# ------------------------------------------------------------
# Country detail (premium activities)
# ------------------------------------------------------------
def country_view(request, country_slug):
    country = get_object_or_404(Country, slug__iexact=country_slug)

    # Active schools in this country
    active_schools = School.objects.filter(
        status=SchoolStatus.ACTIVE, country=country
    ).select_related("country", "city", "finance")

    has_schools = active_schools.exists()
    premium_schools = active_schools.filter(finance__plan__iexact="premium")
    basic_schools = active_schools.exclude(finance__plan__iexact="premium")

    has_premium_schools = premium_schools.exists()
    has_basic_schools = basic_schools.exists()
    show_top_schools = premium_schools.exists()
    show_explore_block = basic_schools.exists() or premium_schools.exists()

    # Related activities (only if schools exist)
    activity_ids = (
        SchoolActivity.objects.filter(school__in=active_schools)
        .values_list("activity_id", flat=True)
        .distinct()
    )
    activities_qs = Activity.objects.filter(id__in=activity_ids).order_by("name")

    activities_payload = []
    for act in activities_qs:
        act_schools = active_schools.filter(school_activities__activity=act).distinct()
        activities_payload.append({"activity": act, "schools": act_schools})

    selected_slug = request.GET.get("activity")
    if not selected_slug or not activities_qs.filter(slug=selected_slug).exists():
        selected_slug = activities_qs.first().slug if activities_qs.exists() else None
    selected_obj = activities_qs.filter(slug=selected_slug).first() if selected_slug else None

    gallery_images = []
    if selected_obj:
        # Obtener todas las ciudades del país
        cities_in_country = City.objects.filter(country=country)
        gallery_images = CityActivityImage.objects.filter(
            gallery__city__in=cities_in_country, gallery__activity=selected_obj
        ).order_by("position")[:5]

    # Add global activities for header
    activities_global = Activity.objects.order_by("name").all()

    # --- Dynamic Hero Image for the country ---
    # First look for a specific image: media/uploads/country/<country-slug>.{jpg|jpeg|png}
    cslug = (country.slug or "").lower()
    candidate_rels = [
        os.path.join("uploads", "country", f"{cslug}.jpg"),
        os.path.join("uploads", "country", f"{cslug}.jpeg"),
        os.path.join("uploads", "country", f"{cslug}.png"),
    ]
    hero_url = None
    for rel in candidate_rels:
        abs_path = os.path.join(settings.MEDIA_ROOT, rel)
        if os.path.exists(abs_path):
            hero_url = settings.MEDIA_URL + rel.replace(os.sep, "/")
            break

    # Fallback: generic destinations hero if exists; otherwise, static
    if not hero_url:
        fallback_rel = os.path.join("uploads", "destinations", "hero_destinations.jpg")
        if os.path.exists(os.path.join(settings.MEDIA_ROOT, fallback_rel)):
            hero_url = settings.MEDIA_URL + fallback_rel.replace(os.sep, "/")
        else:
            hero_url = static("img/hero.png")

    return render(
        request,
        "directory/country_detail.html",
        {
            "country": country,
            "activities_payload": activities_payload,
            "activities": activities_global,
            "selected_activity": selected_slug,
            "selected_activity_obj": selected_obj,
            "gallery_images": gallery_images,
            "has_schools": has_schools,
            "has_premium_schools": has_premium_schools,
            "has_basic_schools": has_basic_schools,
            "premium_schools": premium_schools,
            "schools": active_schools,
            "show_top_schools": show_top_schools,
            "show_explore_block": show_explore_block,
            "destinations_hero_img": hero_url,
        },
    )




from django.templatetags.static import static
from .models import CityExtra  # agregar al inicio si no está

def sport_detail(request, sport_slug):
    sport = get_object_or_404(Activity, slug=sport_slug)

    # Main image
    hero_image = (
        sport.image.url
        if getattr(sport, "image", None) and getattr(sport.image, "name", "")
        else static("img/placeholder-sport.jpg")
    )

    # Destinations offering this sport
    destinations = City.objects.filter(
        id__in=School.objects.filter(
            status=SchoolStatus.ACTIVE,
            school_activities__activity=sport  # corregido aquí
        ).values_list('city_id', flat=True)
    ).distinct()

    # Attach CityExtra to each destination city
    for city in destinations:
        city.extra = CityExtra.objects.filter(city=city).first()

    # Schools offering this sport
    schools = School.objects.filter(
        status=SchoolStatus.ACTIVE,
        school_activities__activity=sport  # corregido aquí
    ).select_related("city", "country").distinct()

    blogs = []

    # Get available levels according to activity variants of the schools
    from .models import SchoolActivityVariant
    variants = SchoolActivityVariant.objects.filter(
        school_activity__activity=sport,
        is_active=True
    )

    # We no longer use a `levels` field on variants. Use `difficulty` as the user-facing level filter.
    available_levels = set()
    for variant in variants:
        diffs = getattr(variant, "difficulty", None)
        if isinstance(diffs, (list, tuple)):
            available_levels.update([d for d in diffs if d])
        elif diffs:
            available_levels.add(diffs)

    available_levels = sorted([lvl for lvl in available_levels if lvl])

    context = {
        "sport": sport,
        "hero_image": hero_image,
        "destinations": destinations,
        "schools": schools,
        "blogs": blogs,
        "available_levels": available_levels,
    }
    return render(request, "directory/sports.html", context)


# ------------------------------------------------------------
# Extra detail per city
# ------------------------------------------------------------
def city_extra_detail(request, country_slug, city_slug):
    return render(request, "directory/city_extra_detail.html", {
        "country_slug": country_slug,
        "city_slug": city_slug
    })


# ------------------------------------------------------------
# List of schools by city
# ------------------------------------------------------------
def schools_by_city(request, country_slug, city_slug):
    from django.db.models import Case, When, IntegerField
    from .models import Activity

    country, city, suggestions = _resolve_country_and_city(country_slug, city_slug)
    if city is None:
        return render(request, "directory/city_ambiguous.html", {
            "country": country,
            "query": city_slug,
            "suggestions": suggestions
        })

    # Get the activity from query param if exists
    activity_slug = request.GET.get("activity")
    activity = None
    schools_qs = School.objects.filter(
        status=SchoolStatus.ACTIVE,
        city=city
    ).select_related("country", "city", "finance")

    # If an activity filter is applied, narrow the queryset
    if activity_slug:
        activity = Activity.objects.filter(slug=activity_slug).first()
        if activity:
            schools_qs = schools_qs.filter(
                school_activities__activity=activity
            ).distinct()

    # Order by Premium first, then alphabetically
    schools_qs = schools_qs.order_by(
        Case(When(finance__plan="premium", then=0), default=1, output_field=IntegerField()),
        "name"
    )

    return render(request, "directory/schools_by_city.html", {
        "country": country,
        "city": city,
        "activity": activity,
        "schools": schools_qs,
    })


# ------------------------------------------------------------
# List of schools by sport and city
# ------------------------------------------------------------
def schools_by_sport_and_city(request, country_slug, city_slug, activity_slug):
    from django.db.models import Case, When, IntegerField
    country, city, suggestions = _resolve_country_and_city(country_slug, city_slug)
    if city is None:
        return render(request, "directory/city_ambiguous.html", {
            "country": country,
            "query": city_slug,
            "suggestions": suggestions
        })

    activity = get_object_or_404(Activity, slug=activity_slug)

    schools = School.objects.filter(
        status=SchoolStatus.ACTIVE,
        city=city,
        school_activities__activity=activity
    ).select_related("country", "city", "finance").distinct().order_by(
        Case(When(finance__plan="premium", then=0), default=1, output_field=IntegerField()),
        "name"
    )

    return render(request, "directory/schools_by_city.html", {
        "country": country,
        "city": city,
        "activity": activity,
        "schools": schools,
    })



# ------------------------------------------------------------
# Main signup page: signup type selector
# ------------------------------------------------------------
def signup_selector(request):
    """
    Main registration page: displays options to register as
    user/traveler, school/company, or instructor/guide.
    """
    return render(request, "directory/signup_basic.html")

# ------------------------------------------------------------
# Basic signup: users / travelers
# ------------------------------------------------------------
from django.contrib import messages  # Add import for messages
from django.contrib.auth.models import User

def signup_basic(request):
    """
    Basic signup for regular users (Travelers).
    Implements user creation logic, validations, and authentication.
    """
    if request.method == "POST":
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "")
        confirm_password = request.POST.get("confirm_password", "")

        # Basic validations
        if not email or not password or not confirm_password:
            messages.error(request, "Please complete all fields.")
            return render(request, "directory/signup_basic.html")

        if password != confirm_password:
            messages.error(request, "Passwords do not match.")
            return render(request, "directory/signup_basic.html", {"email": email})

        # Check if email already exists
        if User.objects.filter(email__iexact=email).exists():
            messages.error(request, "An account with this email already exists.")
            return render(request, "directory/signup_basic.html", {"email": email})

        # Create user (inactive until verification)
        user = User.objects.create_user(username=email, email=email, password=password)
        user.is_active = False
        user.save()

        # Generate and store verification code
        code = str(random.randint(100000, 999999))
        request.session["email_verification_code"] = code
        request.session["pending_user_id"] = user.id

        # Send verification code (will print to console)
        send_mail(
            subject="Your verification code - The Travel Wild",
            message=f"Your verification code is: {code}",
            from_email="noreply@thetravelwild.com",
            recipient_list=[user.email],
        )

        print("📩 Email sent to:", user.email)
        print("✅ Verification code:", code)

        return redirect("verify_email_code")
    else:
        return render(request, "directory/signup_basic.html")

# ------------------------------------------------------------
# Basic registration for instructors or guides
# ------------------------------------------------------------
def instructor_signup_basic(request):
    """
    Basic registration for instructors or guides.
    Currently only renders the base form without registration logic.
    """
    return render(request, "directory/signup_instructor.html")

# ------------------------------------------------------------
# School registration step 1: basic registration + code sending
# (Premium path: basic -> Stripe checkout redirect)
# ------------------------------------------------------------
def school_signup_basic(request):
    """
    Step 1: Basic school registration, creation of inactive user, and sending of verification code.
    If the form is submitted with subscribe_premium=1, immediately create a Stripe
    Checkout Session (subscription) and redirect to Stripe after creating the School.
    """
    from django.contrib.auth.models import User
    from uuid import uuid4
    from django.core.mail import send_mail
    from django.contrib import messages
    import random
    from .models import School, Country, City, SchoolStatus

    # Detect premium subscription intention from form/querystring
    subscribe_premium = (request.POST.get("subscribe_premium") or request.GET.get("subscribe_premium") or "0").strip()
    print("[signup] subscribe_premium:", subscribe_premium)

    if request.method == "POST":
        school_name = request.POST.get("school_name", "").strip()
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "")
        confirm_password = request.POST.get("confirm_password", "")

        if not school_name or not email or not password or not confirm_password:
            messages.error(request, "Please complete all required fields.")
            return render(request, "directory/school_signup_basic.html")

        if password != confirm_password:
            messages.error(request, "Passwords do not match.")
            return render(request, "directory/school_signup_basic.html", {"email": email})

        if User.objects.filter(email__iexact=email).exists():
            messages.error(request, "An account with this email already exists.")
            return render(request, "directory/school_signup_basic.html", {"email": email})

        # Create inactive user
        user = User.objects.create_user(username=email, email=email, password=password)
        user.is_active = False
        user.save()

        # Create School record (real DB)
        default_country = Country.objects.first()
        default_city = City.objects.filter(country=default_country).first()

        school = School.objects.create(
            id=uuid4(),
            name=school_name,
            email=email,
            country=default_country,
            city=default_city,
            slug=school_name.lower().replace(" ", "-"),
            status=SchoolStatus.PENDING,
            verification_status="pending",
            is_verified=False,
        )

        # --- Stripe Express account creation (kept for payouts later) ---
        try:
            stripe.api_key = settings.STRIPE_SECRET_KEY
            finance = school.ensure_finance()
            if not finance.stripe_account_id:
                account = stripe.Account.create(
                    type="express",
                    country=school.country.code if hasattr(school.country, "code") else "US",
                    email=school.email,
                    capabilities={
                        "card_payments": {"requested": True},
                        "transfers": {"requested": True},
                    },
                )
                finance.stripe_account_id = account.id
                finance.save(update_fields=["stripe_account_id"])
            else:
                _ = stripe.Account.retrieve(finance.stripe_account_id)
        except stripe.error.StripeError as e:
            print("⚠️ Stripe account creation error:", e)
        # --- End Stripe Express logic ---

        # Generate verification code (kept, but we may redirect to Stripe first if premium)
        code = str(random.randint(100000, 999999))
        request.session["email_verification_code"] = code
        request.session["pending_user_id"] = user.id
        try:
            send_mail(
                subject="Your verification code - The Travel Wild",
                message=f"Your verification code is: {code}",
                from_email="noreply@thetravelwild.com",
                recipient_list=[user.email],
            )
            print(f"📩 Email sent to: {user.email}")
            print(f"✅ Verification code: {code}")
        except Exception as e:
            print("⚠️ Error sending email:", e)
            messages.error(request, "Error sending verification email.")

        # If Premium was requested, go straight to Stripe Checkout now
        if subscribe_premium == "1":
            try:
                stripe.api_key = settings.STRIPE_SECRET_KEY
                # Ensure finance exists and mark pending
                finance = school.ensure_finance()
                if finance.plan != "premium" and not getattr(finance, "subscription_active", False):
                    finance.plan = "premium"
                    finance.subscription_active = False
                    finance.save(update_fields=["plan", "subscription_active"])

                # Create Stripe Checkout session (subscription)
                session = stripe.checkout.Session.create(
                    mode="subscription",
                    line_items=[{"price": settings.STRIPE_PREMIUM_PRICE_ID, "quantity": 1}],
                    customer_email=email,
                    success_url=request.build_absolute_uri(reverse("verify_email_code")) + "?session_id={CHECKOUT_SESSION_ID}",
                    cancel_url=request.build_absolute_uri(reverse("pricing")),
                    metadata={
                        "kind": "premium_subscription",
                        "school_id": str(school.id),
                        "user_email": email,
                    },
                )
                print("✅ Stripe Checkout session created:", getattr(session, "url", None))
                return redirect(session.url)
            except stripe.error.StripeError as e:
                print("⚠️ Stripe error during checkout:", e)
                messages.error(request, f"Stripe error: {getattr(e, 'user_message', str(e))}")
                return redirect("verify_email_code")

        # Default (Free) flow
        messages.success(request, "School account created. Please verify your email.")
        return redirect("verify_email_code")

    return render(request, "directory/school_signup_basic.html")


# ------------------------------------------------------------
# Step 2: verify email code
# ------------------------------------------------------------
def verify_email_code(request):
    """
    Verifies the code sent by email and activates the user's account.
    This version includes improvements to keep the session active and clearer debugging.
    """
    # Ensure the session is active
    if not request.session.session_key:
        request.session.create()

    print("DEBUG | session key:", request.session.session_key)
    print("DEBUG | session data (initial):", dict(request.session))

    user_id = request.session.get("pending_user_id")
    stored_code = str(request.session.get("email_verification_code", "")).strip()

    if request.method == "POST":
        input_code = request.POST.get("code", "").strip()
        print("DEBUG | Entered code:", input_code)
        print("DEBUG | Stored code:", stored_code)
        print("DEBUG | Pending user ID:", user_id)

        if not input_code:
            messages.error(request, "Please enter the code received by email.")
            return render(request, "directory/verify_email.html")

        # Verify that the data is still in the session
        if not stored_code or not user_id:
            messages.error(request, "Your session has expired. Please register again.")
            return redirect("signup_basic")

        if input_code == stored_code:
            user = User.objects.filter(id=user_id).first()
            if not user:
                messages.error(request, "User not found. Please try again.")
                return redirect("signup_basic")

            user.is_active = True
            user.save()
            login(request, user)

            # Clear session
            for key in ["email_verification_code", "pending_user_id"]:
                request.session.pop(key, None)

            messages.success(request, "✅ Your account has been successfully verified.")
            print("DEBUG | Successful verification for user:", user.email)

            # New logic: redirect to complete school profile if applicable
            from .models import School
            school = School.objects.filter(email=user.email).first()
            if school:
                return redirect("school_profile_completion_view")
            return redirect("home")

        messages.error(request, "Invalid code. Please try again.")
        print("DEBUG | Code does not match.")
        return render(request, "directory/verify_email.html")

    # If GET, show form or check session
    if not stored_code or not user_id:
        messages.error(request, "Your session has expired. Please register again.")
        return redirect("signup_basic")

    # Additional debug for checking cookies
    print("DEBUG | Active cookies:", request.COOKIES)
    print("DEBUG | Session data (GET):", dict(request.session))

    return render(request, "directory/verify_email.html")


# ------------------------------------------------------------
# View: complete school profile after verifying email
# ------------------------------------------------------------
from django.contrib.auth.decorators import login_required
from django.contrib import messages

@login_required
def school_profile_completion_view(request):
    """
    Allows a school to complete its profile after verifying the email.
    """
    # Load the school associated with the current user
    school = School.objects.filter(email=request.user.email).first()
    if not school:
        messages.error(request, "No school associated with your account was found.")
        return redirect("home")

    if request.method == "POST":
        form = SchoolProfileCompletionForm(request.POST, request.FILES, instance=school)
        if form.is_valid():
            school_instance = form.save(commit=False)
            # Process service_types (MultipleChoiceField) as JSON list
            service_types = form.cleaned_data.get("service_types", [])
            if service_types:
                school_instance.service_types = service_types
            school_instance.save()
            messages.success(request, "School profile completed successfully.")
            return redirect("school_select_activities_view")
        else:
            messages.error(request, "Please correct the errors in the form.")
    else:
        form = SchoolProfileCompletionForm(instance=school)

    return render(request, "directory/school_complete_profile.html", {"form": form, "school": school})


# ------------------------------------------------------------
# View for initial activity selection (School select activities)
# ------------------------------------------------------------
from django.urls import reverse
@login_required
def school_select_activities_view(request):
    """
    Allows a newly registered school to select its initial activities.
    Shows all active activities sorted by name.
    """
    user = request.user
    school = School.objects.filter(email=user.email).first()
    if not school:
        messages.error(request, "No se encontró una escuela asociada a tu cuenta.")
        return redirect("home")

    # All activities sorted by name
    activities = Activity.objects.all().order_by("name")
    # Currently selected IDs
    selected_ids = set(SchoolActivity.objects.filter(school=school).values_list("activity_id", flat=True))

    if request.method == "POST":
        selected_ids_post = request.POST.getlist("activities")

        # Keep IDs as strings (Activity.id is CharField/shortuuid)
        selected_ids_post = [i for i in selected_ids_post if i]

        # Remove unselected activities
        SchoolActivity.objects.filter(school=school).exclude(activity_id__in=selected_ids_post).delete()

        # Create any newly selected activities
        for act_id in selected_ids_post:
            activity = Activity.objects.filter(id=act_id).first()
            if activity:
                SchoolActivity.objects.get_or_create(school=school, activity=activity)

        messages.success(request, "✅ Activities selected successfully.")
        return redirect("school_setup_activities_view")

    context = {
        "school": school,
        "activities": activities,
        "selected_activities": selected_ids,
    }
    return render(request, "directory/school_select_activities.html", context)





from django.contrib.auth.models import User
from django.contrib import messages
import random
from django.core.mail import send_mail

# ------------------------------------------------------------
# Resend email verification code
# ------------------------------------------------------------
def resend_verification_code(request):
    """Resends a new verification code to the pending user's email."""
    user_id = request.session.get('pending_user_id')
    if not user_id:
        messages.error(request, "There is no pending registration to verify.")
        return redirect('signup_basic')

    user = User.objects.filter(id=user_id).first()
    if not user:
        messages.error(request, "User does not exist or session has expired.")
        return redirect('signup_basic')

    # Generate and save new code
    new_code = str(random.randint(100000, 999999))
    request.session['email_verification_code'] = new_code

    # Send the code (in test mode, it prints to console)
    send_mail(
        subject="Your new verification code - The Travel Wild",
        message=f"Your new verification code is: {new_code}",
        from_email="noreply@thetravelwild.com",
        recipient_list=[user.email],
    )

    print("📩 New code sent to:", user.email)
    print("✅ Code:", new_code)

    messages.success(request, "A new verification code has been sent to your email.")
    return redirect('verify_email_code')


# ------------------------------------------------------------
# User authentication and profile (Travelers)
# ------------------------------------------------------------
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required

# ------------------------------------------------------------
# User login
# ------------------------------------------------------------
def login_view(request):
    """Automatic login that detects if the email belongs to a school or a regular user."""
    if request.user.is_authenticated:
        messages.info(request, "You are already logged in.")
        if School.objects.filter(email=request.user.email).exists():
            return redirect("school_dashboard_view")
        return redirect("account_profile")

    if request.method == "POST":
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "")

        # Validate fields
        if not email or not password:
            messages.error(request, "Please enter your email and password.")
            return render(request, "directory/login.html")

        # Find user by email
        try:
            user_obj = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            messages.error(request, "No account found with that email address.")
            return render(request, "directory/login.html")

        user = authenticate(request, username=user_obj.username, password=password)

        if user is not None and user.is_active:
            login(request, user)

            # Detect if the email belongs to a school
            if School.objects.filter(email=email).exists():
                messages.success(request, "Welcome to your school's dashboard!")
                return redirect("school_dashboard_view")

            # Regular user (traveler)
            messages.success(request, "Welcome back!")
            return redirect("account_profile")

        elif user is not None and not user.is_active:
            messages.error(request, "Your account is not active. Please verify your email.")
        else:
            messages.error(request, "Invalid credentials. Please try again.")

    return render(request, "directory/login.html")

# ------------------------------------------------------------
# User logout
# ------------------------------------------------------------
def logout_view(request):
    """Logs out the user and redirects to home."""
    logout(request)
    messages.info(request, "You have been logged out successfully.")
    return redirect("home")

# ------------------------------------------------------------
@login_required
def account_profile(request):
    """
    Unified user profile page.
    Shows basic information, bookings, payments, and allows editing personal data.
    """
    from .models import UserProfile, Booking, BookingPayment
    from .forms import UserProfileForm, DeleteAccountForm
    from django.contrib import messages
    from django.contrib.auth import logout

    user = request.user
    profile, _ = UserProfile.objects.get_or_create(user=user)

    # Initialize form with user and profile data
    initial_data = {
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
    }
    profile_form = UserProfileForm(instance=profile, initial=initial_data)
    delete_form = DeleteAccountForm()

    # Booking and payment data
    bookings = Booking.objects.filter(user=user).order_by("-created_at")
    payments = BookingPayment.objects.filter(booking__user=user).order_by("-created_at")

    # POST handling
    if request.method == "POST":
        try:
            if "save_profile" in request.POST:
                profile_form = UserProfileForm(request.POST, request.FILES, instance=profile)
                if profile_form.is_valid():
                    profile = profile_form.save(commit=False)
                    # Save User model data
                    user.first_name = request.POST.get("first_name", user.first_name)
                    user.last_name = request.POST.get("last_name", user.last_name)
                    user.email = request.POST.get("email", user.email)
                    user.save()
                    profile.save()
                    messages.success(request, "Profile updated successfully.")
                    return redirect("account_profile")
                else:
                    messages.error(request, "Please check the entered data.")

            elif "delete_account" in request.POST:
                delete_form = DeleteAccountForm(request.POST)
                if delete_form.is_valid() and delete_form.cleaned_data.get("confirm"):
                    user.delete()
                    messages.success(request, "Your account has been successfully deleted.")
                    logout(request)
                    return redirect("home")

        except Exception as e:
            messages.error(request, f"An error occurred: {e}")
            return redirect("account_profile")

    return render(request, "directory/account_profile.html", {
        "user": user,
        "profile_form": profile_form,
        "delete_form": delete_form,
        "bookings": bookings,
        "payments": payments,
    })
# ------------------------------------------------------------
# View for school activity selection and registration (School setup activities)
# ------------------------------------------------------------
# This view allows a school (authenticated user) to select the activities it offers.
# Shows all available activities and saves the selected ones in SchoolActivity.
# On POST, updates the school's related activities and redirects with a success message.
from django.urls import reverse



@login_required
def school_setup_activities_view(request):
    """
    Allows a school to select the activities it offers during the setup process.
    Also allows creating and editing variants (SchoolActivityVariant) for each selected activity.
    """
    from .models import Activity, School, SchoolActivity, SchoolActivityVariant
    from django.contrib import messages
    from django.utils.datastructures import MultiValueDictKeyError

    user = request.user
    school = School.objects.filter(email=user.email).first()
    if not school:
        messages.error(request, "No se encontró una escuela asociada a tu cuenta.")
        return redirect("home")

    # IDs selected by the school
    selected_ids = set(
        SchoolActivity.objects.filter(school=school).values_list("activity_id", flat=True)
    )

    # Get only the activities selected by the school (via SchoolActivity)
    selected_activities = Activity.objects.filter(
        id__in=SchoolActivity.objects.filter(school=school).values("activity_id")
    ).order_by("name")

    # Load existing variants grouped by activity
    existing_variants = {}
    for sa in SchoolActivity.objects.filter(school=school):
        existing_variants[sa.activity_id] = list(
            SchoolActivityVariant.objects.filter(school_activity=sa)
        )

    if request.method == "POST":
        selected_ids = request.POST.getlist("activities")
        if not selected_ids:
            messages.warning(request, "Please select at least one activity.")
            return redirect("school_setup_activities")

        # Remove unselected activities
        SchoolActivity.objects.filter(school=school).exclude(activity_id__in=selected_ids).delete()

        # Create or keep selected activities
        for act_id in selected_ids:
            activity = Activity.objects.filter(id=act_id).first()
            if activity:
                SchoolActivity.objects.get_or_create(school=school, activity=activity)

        # --- SchoolActivityVariant creation/editing ---
        errors = []
        for act_id in selected_ids:
            activity = Activity.objects.filter(id=act_id).first()
            if not activity:
                continue
            school_activity = SchoolActivity.objects.filter(school=school, activity=activity).first()
            if not school_activity:
                continue
            # Variants for this activity, indexed by a unique index (could be 0, 1, 2, ...)
            variant_keys = [k for k in request.POST.keys() if k.startswith(f"variant-{act_id}-")]
            # Group by index
            variant_indexes = set()
            for k in variant_keys:
                try:
                    idx = k.split("-")[2]
                    variant_indexes.add(idx)
                except Exception:
                    continue
            for idx in variant_indexes:
                prefix = f"variant-{act_id}-{idx}-"
                description = request.POST.get(f"{prefix}description", "").strip()
                offer_type = request.POST.get(f"{prefix}offer_type", "").strip()
                # Images from FILES
                profile_image = request.FILES.get(f"{prefix}profile_image")
                cover_image = request.FILES.get(f"{prefix}cover_image")
                # Difficulty/level/experience depending on offer_type
                difficulty = request.POST.get(f"{prefix}difficulty", "").strip()
                level = request.POST.get(f"{prefix}level", "").strip()
                experience = request.POST.get(f"{prefix}experience", "").strip()
                season_start = request.POST.get(f"{prefix}season_start", "").strip()
                season_end = request.POST.get(f"{prefix}season_end", "").strip()
                # Rental fields
                equipment_included_flag = request.POST.get(f"{prefix}equipment_included")
                equipment_items_list = request.POST.getlist(f"{prefix}equipment_items")
                # Normalize rental items (strip empties)
                equipment_items_list = [i.strip() for i in equipment_items_list if i and i.strip()]

                # Validation: required fields
                if not profile_image:
                    errors.append(f"Profile image required for activity {activity.name}.")
                if not cover_image:
                    errors.append(f"Cover image required for activity {activity.name}.")
                if not season_start or not season_end:
                    errors.append(f"Season start and end are required for activity {activity.name}.")
                # Validate difficulty/level/experience depending on offer_type
                selected_difficulty = None
                if offer_type == "rental":
                    if not equipment_items_list:
                        errors.append(f"At least one rental item is required for activity {activity.name}.")
                    # No level/difficulty/experience required for rental
                elif offer_type == "levels":
                    if not level:
                        errors.append(f"Level required for activity {activity.name}.")
                    selected_difficulty = level
                elif offer_type == "difficulty":
                    if not difficulty:
                        errors.append(f"Difficulty required for activity {activity.name}.")
                    selected_difficulty = difficulty
                elif offer_type == "experience":
                    if not experience:
                        errors.append(f"Experience required for activity {activity.name}.")
                    selected_difficulty = experience
                elif offer_type == "pack":
                    # Only for premium schools
                    if getattr(school.finance, "plan", "basic") != "premium":
                        errors.append(f"Packs are available only for Premium schools. {activity.name} not saved.")
                    else:
                        pack_title = request.POST.get(f"{prefix}pack_title", "").strip()
                        description_short = request.POST.get(f"{prefix}description_short", "").strip()
                        description_long = request.POST.get(f"{prefix}description_long", "").strip()
                        duration_days = request.POST.get(f"{prefix}duration_days", "").strip()
                        location = request.POST.get(f"{prefix}location", "").strip()
                        included_activities = request.POST.getlist(f"{prefix}included_activities")
                        included_services = request.POST.getlist(f"{prefix}included_services")
                        max_group_size = request.POST.get(f"{prefix}max_group_size", "").strip()
                        min_group_size = request.POST.get(f"{prefix}min_group_size", "").strip()
                        difficulty_pack = request.POST.get(f"{prefix}difficulty", "").strip()
                        languages = request.POST.getlist(f"{prefix}languages")
                        price = request.POST.get(f"{prefix}price", "").strip()
                        pack_image = request.FILES.get(f"{prefix}pack_image")

                        if not pack_title or not duration_days or not price:
                            errors.append(f"Missing required fields for pack in {activity.name}.")
                        else:
                            variant_obj = SchoolActivityVariant(
                                school_activity=school_activity,
                                description=description_long or description_short or pack_title,
                                profile_image=pack_image,
                                offer_type="pack",
                                is_active=True
                            )
                            variant_obj.extra_data = {
                                "pack_title": pack_title,
                                "short_description": description_short,
                                "long_description": description_long,
                                "duration_days": duration_days,
                                "location": location,
                                "included_activities": included_activities,
                                "included_services": included_services,
                                "group_size": {"max": max_group_size, "min": min_group_size},
                                "difficulty": difficulty_pack,
                                "languages": languages,
                                "price": price,
                            }
                            variant_obj.save()
                else:
                    errors.append(f"Offer type required for activity {activity.name}.")

                # If no errors for this variant, create or update
                if not errors:
                    # Try to update existing variant by index if possible (optional: you can match by description or index)
                    variant_obj = None
                    # If you want to update by description, you could do:
                    # variant_obj = SchoolActivityVariant.objects.filter(school_activity=school_activity, description=description).first()
                    # For now, always create new
                    variant_obj = SchoolActivityVariant(
                        school_activity=school_activity,
                        description=description,
                        profile_image=profile_image,
                        cover_image=cover_image,
                        season_start=season_start,
                        season_end=season_end,
                        is_active=True,
                        offer_type=offer_type,
                    )
                    # Set correct field depending on offer_type
                    if offer_type == "levels":
                        variant_obj.levels = level
                        variant_obj.difficulty = None
                        variant_obj.experience_type = None
                    elif offer_type == "difficulty":
                        variant_obj.levels = None
                        variant_obj.difficulty = difficulty
                        variant_obj.experience_type = None
                    elif offer_type == "experience":
                        variant_obj.levels = None
                        variant_obj.difficulty = None
                        variant_obj.experience_type = experience
                    # Actual assignments for new model fields, depending on offer_type
                    if offer_type == "rental":
                        variant_obj.equipment_included = bool(equipment_included_flag)
                        variant_obj.equipment_items = equipment_items_list or None
                    elif offer_type == "pack":
                        variant_obj.pack_title = pack_title
                        variant_obj.pack_description_short = description_short
                        variant_obj.included_services = included_services or None
                        variant_obj.date_start = date_start or None  # pyright: ignore
                        variant_obj.date_end = date_end or None # type: ignore
                    variant_obj.save()
        if errors:
            for err in errors:
                messages.error(request, err)
            # Re-render page with errors and previously entered data
            context = {
                "school": school,
                "activities": selected_activities,
                "selected_activities": selected_ids,
                "existing_variants": existing_variants,
            }
            return render(request, "directory/school_setup_activities.html", context)

        messages.success(request, "✅ Activities and variants added successfully!")
        return redirect("home")

    context = {
        "school": school,
        "activities": selected_activities,
        "selected_activities": selected_ids,
        "existing_variants": existing_variants,
    }
    return render(request, "directory/school_setup_activities.html", context)


# ------------------------------------------------------------
# Main school dashboard
# ------------------------------------------------------------
@login_required
def school_dashboard_view(request):
    """
    Main landing page for the school dashboard.
    Shows summary information and access to key sections.
    """
    school = get_object_or_404(School, email=request.user.email)

    activities = SchoolActivity.objects.filter(school=school)
    total_activities = activities.count()
    finance = getattr(school, "finance", None)
    plan = finance.plan if finance else "basic"

    total_reviews = SchoolReview.objects.filter(school=school).count()
    total_transactions = SchoolTransaction.objects.filter(school=school).count()

    context = {
        "school": school,
        "total_activities": total_activities,
        "plan": plan,
        "activities": activities,
        "total_reviews": total_reviews,
        "total_transactions": total_transactions,
    }
    return render(request, "directory/school_dashboard.html", context)


# ------------------------------------------------------------
# School finances (transaction history)
# ------------------------------------------------------------
@login_required
def school_transactions_view(request):
    """
    Shows the school's financial history:
    received payments, commissions, net amounts, and pending payments.
    """
    from .models import Booking, BookingStatus

    school = get_object_or_404(School, email=request.user.email)
    transactions = SchoolTransaction.objects.filter(school=school).order_by('-created_at')
    finance = getattr(school, "finance", None)

    # Totals received and commissions
    total_earned = sum(t.net_amount for t in transactions)
    total_fees = sum(t.fee_amount for t in transactions)

    # Calculate pending amounts (bookings not completed)
    pending_bookings = Booking.objects.filter(
        variant__school_activity__school=school
    ).exclude(status=BookingStatus.COMPLETED)

    pending_balance = sum(
        getattr(b.variant, "price", Decimal("0.00")) or Decimal("0.00")
        for b in pending_bookings
        if getattr(b, "variant", None)
    )

    context = {
        "school": school,
        "transactions": transactions,
        "finance": finance,
        "total_earned": total_earned,
        "total_fees": total_fees,
        "pending_balance": pending_balance,
    }
    return render(request, "directory/school_finances.html", context)


# ------------------------------------------------------------
# School bookings
# ------------------------------------------------------------
@login_required
def school_bookings_view(request):
    """
    Shows the bookings received by the school and allows updating them with advanced status logic.
    """
    from .models import Booking, BookingStatus
    from django.conf import settings
    from django.contrib import messages
    from django.utils import timezone

    MANUAL_PAYOUT_APPROVAL = getattr(settings, 'MANUAL_PAYOUT_APPROVAL', False)

    school = get_object_or_404(School, email=request.user.email)

    if request.method != 'POST':
        bookings = (
            Booking.objects.filter(variant__school_activity__school=school)
            .select_related('user', 'variant', 'variant__school_activity', 'variant__school_activity__activity')
            .order_by('-created_at')
        )
        context = {
            'school': school,
            'bookings': bookings,
            'status_choices': getattr(BookingStatus, 'choices', [('COMPLETED', 'Completed'), ('PARTIAL', 'Partial'), ('NO_SHOW', 'No Show')]),
        }
        return render(request, 'directory/school_bookings.html', context)

    # POST: update booking
    booking_id = request.POST.get('booking_id')
    new_status = (request.POST.get('status') or '').upper().strip()
    partial_percent_raw = request.POST.get('partial_percent', '').strip()

    booking = get_object_or_404(Booking, id=booking_id, variant__school_activity__school=school)

    # Avoid changes if payout already released
    if getattr(booking, 'payout_released', False):
        messages.info(request, 'This booking already has released payout.')
        return redirect('school_bookings_view')

    # Block update before session date if available
    today = timezone.now().date()
    session_date = getattr(booking, 'session_date', None)
    if session_date and session_date > today:
        messages.error(request, 'You can only update status after the session date.')
        return redirect('school_bookings_view')

    allowed = {'COMPLETED', 'PARTIAL', 'NO_SHOW'}
    if new_status not in allowed:
        messages.error(request, 'Invalid status.')
        return redirect('school_bookings_view')

    if new_status == 'PARTIAL':
        try:
            val = int(partial_percent_raw)
        except ValueError:
            messages.error(request, 'Enter a valid percentage for Partial.')
            return redirect('school_bookings_view')
        if not (10 <= val <= 100):
            messages.error(request, 'Percentage must be between 10 and 100.')
            return redirect('school_bookings_view')
        booking.partial_percent = val
    else:
        if hasattr(booking, 'partial_percent'):
            booking.partial_percent = None

    booking.status = new_status
    booking.save(update_fields=['status', 'partial_percent'] if new_status == 'PARTIAL' else ['status'])

    if MANUAL_PAYOUT_APPROVAL:
        messages.success(request, 'Status saved. Pending admin approval for payout.')
        return redirect('school_bookings_view')

    # Automatic payout release
    try:
        release_funds_for_booking(request, booking.id)
        messages.success(request, 'Status updated and payout processed.')
    except Exception as e:
        messages.error(request, f'Error processing payout: {e}')

    return redirect('school_bookings_view')


# ------------------------------------------------------------
# Update booking status (via booking update form)
# ------------------------------------------------------------
from django.shortcuts import redirect, get_object_or_404
from django.contrib import messages
from decimal import Decimal
from .models import School

@login_required
def update_booking_status(request, booking_id):
    """
    Handles update of booking status (Completed / Partial / No Show).
    """
    from .models import Booking, BookingStatus

    booking = get_object_or_404(Booking, id=booking_id)
    school = get_object_or_404(School, email=request.user.email)

    if request.method == "POST":
        new_status = request.POST.get("status")
        partial_percent = request.POST.get("partial_percent")

        if new_status:
            booking.status = new_status
            if new_status == "PARTIAL" and partial_percent:
                try:
                    booking.partial_percent = Decimal(partial_percent)
                except Exception:
                    messages.error(request, "Invalid percentage value.")
            booking.save(update_fields=["status", "partial_percent"] if new_status == "PARTIAL" else ["status"])

            # Trigger fund release for completed, partial, or no show
            if new_status.lower() in ["completed", "partial", "no_show"]:
                try:
                    release_funds_for_booking(request, booking.id)
                except Exception as e:
                    print("⚠️ Error releasing funds:", e)

            messages.success(request, f"Booking updated successfully ({new_status}).")
        else:
            messages.error(request, "Invalid booking status.")

    return redirect("school_bookings_view")

# ------------------------------------------------------------
# Password Reset Views (using Django built-in auth views)
# ------------------------------------------------------------
from django.contrib.auth import views as auth_views

class CustomPasswordResetView(auth_views.PasswordResetView):
    template_name = "directory/password_reset.html"
    email_template_name = "directory/password_reset_email.html"
    subject_template_name = "directory/password_reset_subject.txt"
    success_url = "/password-reset/done/"

class CustomPasswordResetDoneView(auth_views.PasswordResetDoneView):
    template_name = "directory/password_reset_done.html"

class CustomPasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "directory/password_reset_confirm.html"
    success_url = "/reset/done/"

class CustomPasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "directory/password_reset_complete.html"


# ------------------------------------------------------------
# Destinations Page
# ------------------------------------------------------------
from django.db.models import Count
from django.templatetags.static import static

def destinations_view(request):
    """
    Destinations landing:
    - Shows cards by country with active schools and available activities.
    - Links redirect using the format /<country>/?activity=<sport>.
    """
    selected_country = (request.GET.get("country") or "").strip()
    selected_sport = (request.GET.get("sport") or "").strip()

    activities_payload = Activity.objects.order_by("name")

    # Active schools
    active_schools = School.objects.filter(status=SchoolStatus.ACTIVE)

    # ✅ Countries with active schools
    countries_qs = (
        Country.objects
        .filter(city__school__status=SchoolStatus.ACTIVE)
        .annotate(schools_count=Count("city__school", distinct=True))
        .order_by("name")
        .distinct()
    )

    destinations = []
    for country in countries_qs:
        # ✅ City with most active schools
        top_city = (
            City.objects
            .filter(country=country, school__status=SchoolStatus.ACTIVE)
            .annotate(schools_total=Count("school", distinct=True))
            .order_by("-schools_total", "name")
            .first()
        )

        # ✅ Top 3 active sports per country
        top_sports = (
            Activity.objects
            .filter(
                school_activities__school__city__country=country,
                school_activities__school__status=SchoolStatus.ACTIVE,
            )
            .annotate(cnt=Count("school_activities", distinct=True))
            .order_by("-cnt", "name")[:3]
        )

        # Tentative or generic image
        image_url = f"/media/uploads/country/{country.slug}.jpg"

        destinations.append({
            "name": country.name,
            "slug": country.slug,
            "image_url": image_url,
            "city": top_city,
            "sports": [a.name for a in top_sports],
        })

    activities_global = Activity.objects.order_by("name").all()
    context = {
        "destinations": destinations,
        "countries": countries_qs,
        "activities_payload": activities_payload,
        "activities": activities_global,
        "selected_country": selected_country,
        "selected_sport": selected_sport,
        "destinations_hero_img": f"{settings.MEDIA_URL}uploads/destinations/hero_destinations.jpg",
    }

    return render(request, "directory/destinations.html", context)
# ------------------------------------------------------------
# Stripe: Release of funds according to booking status
# ------------------------------------------------------------
from django.db import transaction as db_transaction
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponseForbidden

from django.http import JsonResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required
from django.db import transaction as db_transaction
from .utils import send_payout_notification

@login_required
@db_transaction.atomic
def release_funds_for_booking(request, booking_id):
    """
    Development-safe version:
    - No Stripe logic
    - Sends payout notification email to finance/admin
    - Updates booking payout flags
    """
    from .models import Booking

    if request.method != "POST":
        return HttpResponseBadRequest("Invalid method")

    booking = get_object_or_404(Booking, id=booking_id)
    school = booking.variant.school_activity.school

    # Authorization: only the owning school can trigger this
    if school.email != request.user.email:
        return HttpResponseForbidden("Not authorized")

    # ✅ Send payout notification instead of releasing funds
    try:
        send_payout_notification(booking)
    except Exception as e:
        print(f"⚠️ Failed to send payout notification: {e}")

    # Update local flags
    booking.payout_released = False  # stays pending until finance approves
    booking.save(update_fields=["payout_released"])

    return JsonResponse({
        "ok": True,
        "message": "✅ Payout notification sent to finance team (Stripe disabled in this environment).",
    })

# ------------------------------------------------------------
# Start Booking (user initiates a booking flow)
# ------------------------------------------------------------
from django.contrib.auth.decorators import login_required
from .models import SchoolActivitySession, Booking, BookingStatus

@login_required
def start_booking(request, session_id):
    # 1️⃣ Verificamos que la sesión exista
    session = get_object_or_404(SchoolActivitySession, id=session_id)
    user = request.user

    # 2️⃣ Creamos el booking usando los campos correctos del modelo
    booking, created = Booking.objects.get_or_create(
        user=user,
        variant=session.variant,
        school=session.variant.school_activity.school,
        session_date=session.date_start,
        defaults={
            "amount": session.variant.price,
            "status": "pending_payment",  # usamos string, no Enum
            "payment_status": "unpaid",
            "partial_percent": 0,
            "refund_percent": 0,
        },
    )

    # 3️⃣ Redirigimos al flujo de pago (Stripe)
    return redirect("checkout_page", booking_id=booking.id)

# ------------------------------------------------------------
# Checkout Page (Booking payment page)
# ------------------------------------------------------------
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render
from .models import Booking

@login_required
def checkout_page(request, booking_id):
    booking = get_object_or_404(Booking, id=booking_id)
    
    # Si quieres pasar info adicional al template (Stripe, etc)
    context = {
        "booking": booking,
        "amount_eur": booking.amount,
        "variant": booking.variant,
        "school": booking.school,
    }

    return render(request, "directory/checkout.html", context)

# ------------------------------------------------------------
# Checkout Page (frontend integration with Stripe Elements)
# ------------------------------------------------------------
from django.contrib.auth.decorators import login_required

@login_required
def checkout_page(request, booking_id):
    """
    Displays the Stripe checkout interface for a given booking.
    Includes Stripe publishable key, booking details, and total amount.
    """
    from .models import Booking

    booking = get_object_or_404(Booking, id=booking_id)
    school = booking.school
    variant = getattr(booking, "variant", None)

    # Monto total (en EUR)
    if hasattr(booking, "amount") and booking.amount:
        amount_eur = booking.amount 
    elif variant and getattr(variant, "price", None):
        amount_eur = variant.price
    else:
        amount_eur = Decimal("0.00")

    context = {
        "booking": booking,
        "school": school,
        "variant": variant,
        "amount_eur": amount_eur,
        "STRIPE_PUBLISHABLE_KEY": settings.STRIPE_PUBLISHABLE_KEY,
        "success_url": "/account/",  # puedes cambiar por /checkout/success/ si lo prefieres
    }
    return render(request, "directory/checkout.html", context)
# ------------------------------------------------------------
# Confirma un pago con Stripe y actualiza Booking y BookingPayment
# ------------------------------------------------------------
from django.views.decorators.csrf import csrf_exempt
from django.db import transaction
import stripe

@csrf_exempt
@login_required
def confirm_payment(request):
    """
    Confirma un pago con Stripe y actualiza Booking y BookingPayment.
    Se llama desde el frontend tras un pago exitoso (status=succeeded).
    """
    if request.method != "POST":
        return JsonResponse({"error": "Invalid method"}, status=405)

    payment_intent_id = request.POST.get("payment_intent_id")
    booking_id = request.POST.get("booking_id")

    if not payment_intent_id or not booking_id:
        return JsonResponse({"error": "Missing parameters"}, status=400)

    stripe.api_key = settings.STRIPE_SECRET_KEY

    try:
        pi = stripe.PaymentIntent.retrieve(payment_intent_id)
    except Exception as e:
        return JsonResponse({"error": f"Stripe retrieve failed: {e}"}, status=400)

    if pi.get("status") != "succeeded":
        return JsonResponse({"error": f"PaymentIntent not succeeded (status={pi.get('status')})"}, status=400)

    from .models import Booking, BookingPayment, BookingStatus

    with transaction.atomic():
        booking = Booking.objects.select_for_update().filter(id=booking_id, user=request.user).first()
        if not booking:
            return JsonResponse({"error": "Booking not found"}, status=404)

        # Crea o actualiza el registro del pago
        bp, _ = BookingPayment.objects.get_or_create(
            booking=booking,
            defaults={
                "amount": booking.amount,
                "currency": "EUR",
                "stripe_payment_intent": payment_intent_id,
                "status": "paid",
                "payment_method": "card",
            },
        )

        bp.amount = booking.amount
        bp.currency = "EUR"
        bp.stripe_payment_intent = payment_intent_id
        bp.status = "paid"
        bp.save(update_fields=["amount", "currency", "stripe_payment_intent", "status", "updated_at"])

        # Marca la reserva como confirmada o pagada
        if hasattr(BookingStatus, "CONFIRMED"):
            booking.status = BookingStatus.CONFIRMED
        elif hasattr(BookingStatus, "PAID_PENDING_RELEASE"):
            booking.status = BookingStatus.PAID_PENDING_RELEASE
        else:
            booking.status = "CONFIRMED"
        booking.save(update_fields=["status"])

    return JsonResponse({"ok": True})
# ------------------------------------------------------------
# Update booking status and release Stripe payment
# ------------------------------------------------------------
from django.contrib import messages

@login_required
def update_booking_status(request, booking_id):
    """
    Actualiza el estado de la reserva y, si aplica, libera el pago al Stripe account de la escuela.
    """
    from .models import Booking
    import stripe
    from decimal import Decimal
    stripe.api_key = settings.STRIPE_SECRET_KEY

    booking = get_object_or_404(Booking, id=booking_id)

    if request.method == "POST":
        status = request.POST.get("status")
        partial_percent = request.POST.get("partial_percent")

        booking.status = status
        if status == "partial" and partial_percent:
            booking.partial_percent = int(partial_percent)
        else:
            booking.partial_percent = None
        booking.save()

        if booking.stripe_payment_intent and booking.school and booking.school.finance and booking.school.finance.stripe_account_id:
            try:
                amount = int(booking.amount * 100)  # euros → céntimos
                fee_percent = booking.school.finance.fee_percent or Decimal("20")
                commission = int(amount * (fee_percent / 100))

                if status == "completed":
                    payout_amount = amount - commission
                elif status == "partial" and partial_percent:
                    payout_amount = int(amount * (int(partial_percent) / 100)) - commission
                elif status == "no_show":
                    payout_amount = amount - commission
                else:
                    payout_amount = 0

                if payout_amount > 0:
                    stripe.Transfer.create(
                        amount=payout_amount,
                        currency="eur",
                        destination=booking.school.finance.stripe_account_id,
                        transfer_group=f"booking_{booking.id}"
                    )
                    messages.success(request, f"✅ Payment of €{payout_amount / 100:.2f} sent to the school.")
                else:
                    messages.info(request, "No payout generated for this booking.")
            except stripe.error.StripeError as e:
                messages.error(request, f"Stripe error: {getattr(e, 'user_message', str(e))}")
            except Exception as e:
                messages.error(request, f"Unexpected error: {e}")

        return redirect("school_bookings_view")

    return redirect("school_bookings_view")
# ------------------------------------------------------------
# Update booking status and payout (for admin/school dashboard)
# ------------------------------------------------------------
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from .models import Booking, BookingStatus
from decimal import Decimal
import stripe

from django.conf import settings
from django.contrib.auth.decorators import login_required

@login_required
@require_POST
def update_booking_status(request, booking_id):
    """
    Updates booking status and notifies finance for payout review.
    Prevents duplicate notifications by checking the email_payout_sent flag.
    """
    from .models import Booking
    from .utils import send_payout_notification

    booking = get_object_or_404(Booking, id=booking_id)
    new_status = request.POST.get("status")
    partial_percent = request.POST.get("partial_percent")

    if not new_status:
        messages.error(request, "No status provided.")
        return redirect("school_bookings_view")

    # Update booking status
    booking.status = new_status
    if new_status.lower() == "partial" and partial_percent:
        try:
            booking.partial_percent = Decimal(partial_percent)
        except Exception:
            messages.error(request, "Invalid percentage value.")
            return redirect("school_bookings_view")
    booking.save(update_fields=["status", "partial_percent"])

    # Check if email was already sent
    if getattr(booking, "email_payout_sent", False):
        messages.warning(request, "⚠️ Payment process already initiated. Notification cannot be sent again.")
        return redirect("school_bookings_view")

    # Send payout notification once
    try:
        send_payout_notification(booking)
        booking.email_payout_sent = True
        booking.save(update_fields=["email_payout_sent"])
        messages.success(request, "✅ Notification sent. Payment process initiated.")
    except Exception as e:
        messages.error(request, f"Error sending payout notification: {e}")

    return redirect("school_bookings_view")
# ------------------------------------------------------------
# Notify admin to release payment for booking
# ------------------------------------------------------------
from django.views.decorators.http import require_POST
from django.http import JsonResponse
from django.conf import settings
from django.core.mail import send_mail
from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required

@login_required
def notify_payment_release(request, booking_id):
    """
    View to notify admin to release payment for a booking.
    Only allowed if booking status is 'completed' or 'partial'.
    Sends an email to admin@thetravelwild.com with relevant info.
    """
    from .models import Booking
    booking = get_object_or_404(Booking, id=booking_id)
    valid_statuses = {"completed", "partial"}
    status = getattr(booking, "status", "")
    if status not in valid_statuses:
        return JsonResponse({"error": "Booking is not eligible for payout."}, status=400)

    school_name = getattr(getattr(booking, "variant", None), "school_activity", None)
    if school_name and hasattr(school_name, "school"):
        school_name = school_name.school.name
    else:
        school_name = "Unknown"
    amount = getattr(booking, "amount", None)
    try:
        amount_eur = f"{float(amount):.2f} EUR" if amount is not None else "Unknown"
    except Exception:
        amount_eur = str(amount)
    user_email = getattr(getattr(booking, "user", None), "email", "Unknown")

    subject = f"[PAYOUT NOTICE] Booking {booking.id} ready for payout"
    message = (
        f"Booking ID: {booking.id}\n"
        f"School: {school_name}\n"
        f"Amount: {amount_eur}\n"
        f"User email: {user_email}\n"
        f"Status: {status}\n\n"
        "Please review this booking and release the payment manually via Stripe."
    )
    send_mail(
        subject=subject,
        message=message,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@thetravelwild.com"),
        recipient_list=["admin@thetravelwild.com"],
    )
    return JsonResponse({"ok": True})
# ------------------------------------------------------------
# Admin: Mark transaction as paid (manual payout) and notify school
# ------------------------------------------------------------
from django.contrib.admin.views.decorators import staff_member_required
from django.core.mail import send_mail

@staff_member_required
def mark_transaction_paid(request, transaction_id):
    """
    Marks a SchoolTransaction as paid (manual payout) and notifies the school.
    """
    tx = get_object_or_404(SchoolTransaction, id=transaction_id)
    if not tx.is_released:
        tx.is_released = True
        tx.released_at = timezone.now()
        tx.save(update_fields=["is_released", "released_at"])

        if tx.school and getattr(tx.school, "email", None):
            send_mail(
                subject=f"[PAYMENT CONFIRMATION] Payment sent for transaction {tx.id}",
                message=(
                    f"Dear {tx.school.name},\n\n"
                    f"We’ve sent your payout for transaction ID {tx.id}.\n"
                    f"Amount: €{tx.net_amount}\n"
                    f"Date: {tx.released_at.strftime('%Y-%m-%d %H:%M')}\n\n"
                    f"Thank you for partnering with The Travel Wild!\n"
                    f"— The Travel Wild Team"
                ),
                from_email="noreply@thetravelwild.com",
                recipient_list=[tx.school.email],
            )

    messages.success(request, f"Payment for transaction {tx.id} marked as sent.")
    return redirect("admin:directory_schooltransaction_changelist")

# ------------------------------------------------------------
# Legal pages (Terms, Privacy, Cookies)
# ------------------------------------------------------------

def terms_view(request):
    return render(request, "directory/terms.html")

def privacy_view(request):
    return render(request, "directory/privacy.html")

def cookies_view(request):
    return render(request, "directory/cookies.html")