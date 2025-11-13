from decimal import Decimal
from django.db import models
from django.db.models import Avg
from cities_light.models import Country, City
from django.core.exceptions import ValidationError
import uuid
import shortuuid
from django.conf import settings
from django.utils import timezone
from django.contrib.auth.models import User
import uuid


# -----------------------------
# User Profile Models
# -----------------------------

class UserProfile(models.Model):
    GENDER_CHOICES = [
        ('M', 'Male'),
        ('F', 'Female'),
        ('O', 'Prefer not to say'),
    ]

    class UserType(models.TextChoices):
        TRAVELER = "traveler", "Traveler"
        SCHOOL = "school", "School"
        INSTRUCTOR = "instructor", "Instructor"

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="user_profile")
    phone = models.CharField(max_length=50, blank=True, null=True)
    birth_date = models.DateField(blank=True, null=True)
    nationality = models.CharField(max_length=100, blank=True, null=True)
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES, blank=True, null=True)
    address = models.CharField(max_length=255, blank=True, null=True)
    profile_image = models.ImageField(upload_to="uploads/users/profiles/", blank=True, null=True)
    user_type = models.CharField(
        max_length=16,
        choices=UserType.choices,
        default=UserType.TRAVELER,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "directory_userprofile"
        verbose_name = "User Profile"
        verbose_name_plural = "User Profiles"

    def __str__(self):
        return f"Perfil de {self.user.get_full_name() or self.user.email}"

    def delete_account(self):
        """Elimina el perfil y el usuario asociado."""
        user = self.user
        self.delete()
        user.delete()


# -----------------------------
# Booking & Payments
# -----------------------------
class BookingStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PENDING_PAYMENT = "pending_payment", "Pending Payment"
    CONFIRMED = "confirmed", "Confirmed"
    PAID_PENDING_RELEASE = "paid_pending_release", "Paid Pending Release"
    COMPLETED = "completed", "Completed"
    PARTIAL = "partial", "Partial"
    NO_SHOW = "no_show", "No Show"
    CANCELED = "canceled", "Canceled"


class PaymentStatus(models.TextChoices):
    UNPAID = "unpaid", "Unpaid"
    PAID = "paid", "Paid"
    REFUNDED = "refunded", "Refunded"
    FAILED = "failed", "Failed"


class Booking(models.Model):
    """
    Represents a reservation made by a traveler for a specific school activity variant.
    Integrates payment, refund, and payout management via Stripe Connect.
    """
    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="bookings")
    variant = models.ForeignKey(
        'SchoolActivityVariant',
        on_delete=models.CASCADE,
        related_name="bookings",
        db_column='variant_id',
        to_field='id'
    )
    school = models.ForeignKey('School', on_delete=models.CASCADE, related_name="bookings", null=True, blank=True)

    session_date = models.DateField(blank=True, null=True)
    booking_date = models.DateTimeField(auto_now_add=True)

    status = models.CharField(max_length=20, choices=BookingStatus.choices, default=BookingStatus.PENDING)
    payment_status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.UNPAID)

    amount = models.DecimalField(max_digits=10, decimal_places=2, help_text="Total booking amount in EUR")
    refund_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, help_text="Percentage of refund applied")
    partial_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="For Partial payouts: percent (0–100) to pay to school."
    )
    notes = models.TextField(blank=True, null=True)

    stripe_payment_intent = models.CharField(max_length=255, blank=True, null=True)
    stripe_transfer_id = models.CharField(max_length=255, blank=True, null=True)

    payout_released = models.BooleanField(default=False)
    email_payout_sent = models.BooleanField(default=False)  # Tracks if payout email has been sent
    payout_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "directory_booking"
        verbose_name = "Booking"
        verbose_name_plural = "Bookings"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Booking {self.id} – {self.user.username} – {self.variant.name}"

    def get_refund_percentage(self):
        """Determines refund percentage based on cancellation timing."""
        from datetime import timedelta
        if not self.session_date:
            return Decimal("0.00")

        now = timezone.now().date()
        delta = (self.session_date - now).days

        if delta > 1:
            return Decimal("1.00")  # Full refund
        elif delta == 0:
            return Decimal("0.40")  # 40% refund on same day
        else:
            return Decimal("0.00")  # No refund after activity

    def apply_refund_policy(self):
        """Applies the refund logic and updates refund percentage."""
        self.refund_percent = self.get_refund_percentage()
        self.save(update_fields=["refund_percent"])

    def release_payment_to_school(self):
        """
        Development-safe version:
        Instead of releasing payout via Stripe, send a notification email to finance team.
        """
        from .utils import send_payout_notification

        send_payout_notification(self)
        self.payout_released = False
        self.save(update_fields=["payout_released"])
        return

    def handle_refund(self):
        """Handles refunds based on the refund percentage and updates payment status."""
        import stripe
        if not self.stripe_payment_intent:
            return

        refund_amount = self.amount * self.get_refund_percentage()
        stripe.Refund.create(
            payment_intent=self.stripe_payment_intent,
            amount=int(refund_amount * 100),
        )
        self.payment_status = PaymentStatus.REFUNDED
        self.save(update_fields=["payment_status", "refund_percent"])



class Payment(models.Model):
    """
    Stores raw payment information associated with a booking.
    """
    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="payments")
    booking = models.ForeignKey('Booking', on_delete=models.SET_NULL, null=True, blank=True, related_name="payments")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=10, default="EUR")
    payment_method = models.CharField(max_length=50, blank=True, null=True)
    status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.UNPAID)
    stripe_payment_id = models.CharField(max_length=255, unique=False, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "directory_payment"
        verbose_name = "Payment"
        verbose_name_plural = "Payments"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Payment {self.id} – {self.amount} {self.currency} ({self.status})"


# -----------------------------
# BookingPayment model
# -----------------------------
class BookingPayment(models.Model):
    """
    Represents Stripe payments linked directly to a Booking.
    This table tracks payments created via webhook or checkout completion.
    """
    id = models.AutoField(primary_key=True)
    booking = models.ForeignKey('Booking', on_delete=models.CASCADE, related_name='booking_payments')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=10, default="EUR")
    stripe_payment_intent = models.CharField(max_length=255, blank=True, null=True)
    stripe_charge_id = models.CharField(max_length=255, blank=True, null=True)
    payment_method = models.CharField(max_length=50, blank=True, null=True)
    status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.UNPAID)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "directory_booking_payment"
        verbose_name = "Booking Payment"
        verbose_name_plural = "Booking Payments"
        ordering = ["-created_at"]

    def __str__(self):
        return f"BookingPayment {self.id} – Booking {self.booking.id} – {self.amount} {self.currency} ({self.status})"


class SchoolProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="school_profile")
    school_name = models.CharField(max_length=255)
    contact_email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=50, blank=True, null=True)
    website = models.URLField(blank=True, null=True)
    logo = models.ImageField(upload_to="uploads/schools/logos/", blank=True, null=True)

    class Meta:
        db_table = "directory_schoolprofile"
        verbose_name = "School Profile"
        verbose_name_plural = "School Profiles"

    def __str__(self):
        return f"SchoolProfile: {self.school_name}"


class InstructorProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="instructor_profile")
    full_name = models.CharField(max_length=255, blank=True, null=True)
    experience_years = models.PositiveIntegerField(blank=True, null=True)
    languages = models.CharField(max_length=255, blank=True, null=True)
    country = models.CharField(max_length=100, blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    bio = models.TextField(blank=True, null=True)
    age = models.PositiveIntegerField(blank=True, null=True)
    gender = models.CharField(max_length=20, blank=True, null=True)
    profile_image = models.ImageField(upload_to="uploads/instructors/profiles/", blank=True, null=True)
    certifications = models.TextField(blank=True, null=True)

    class Meta:
        db_table = "directory_instructorprofile"
        verbose_name = "Instructor Profile"
        verbose_name_plural = "Instructor Profiles"

    def __str__(self):
        return f"InstructorProfile: {self.user.get_full_name() or self.user.email}"

# -----------------------------
# Enums / Choices
# -----------------------------
class ActivityCategory(models.TextChoices):
    WATER = 'water', 'water'
    LAND = 'land', 'land'
    AIR = 'air', 'air'
    SNOW = 'snow', 'snow'
    EXTREME = 'extreme', 'extreme'

class SchoolStatus(models.TextChoices):
    DRAFT = 'draft', 'draft'
    PENDING = 'pending', 'pending'
    ACTIVE = 'active', 'active'
    INACTIVE = 'inactive', 'inactive'
    SUSPENDED = 'suspended', 'suspended'


class SubscriptionStatus(models.TextChoices):
    ACTIVE = 'active', 'active'
    PENDING = 'pending', 'pending'
    PAST_DUE = 'past_due', 'past_due'
    CANCELED = 'canceled', 'canceled'
    EXPIRED = 'expired', 'expired'

class VerificationStatus(models.TextChoices):
    PENDING = 'pending', 'pending'
    APPROVED = 'approved', 'approved'
    REJECTED = 'rejected', 'rejected'

class MediaKind(models.TextChoices):
    IMAGE = 'image', 'image'
    VIDEO = 'video', 'video'

# -----------------------------
# Service Types Choices
# -----------------------------
class LevelChoices(models.TextChoices):
    BEGINNER = 'beginner', 'Beginner'
    INTERMEDIATE = 'intermediate', 'Intermediate'
    ADVANCED = 'advanced', 'Advanced'

class DifficultyChoices(models.TextChoices):
    EASY = 'easy', 'Easy'
    MODERATE = 'moderate', 'Moderate'
    HARD = 'hard', 'Hard'
    EXTREME = 'extreme', 'Extreme'

class ExperienceChoices(models.TextChoices):
    ONE_SHOT = 'oneshot', 'OneShot'
    ADVENTURE = 'adventure', 'Adventure'
    EXPEDITION = 'expedition', 'Expedition'


# -----------------------------
# Read models (managed=False)
# -----------------------------
class SchoolEffectivePlan(models.Model):
    school = models.OneToOneField('School', db_column='school_id', on_delete=models.DO_NOTHING, primary_key=True)
    plan = models.TextField(null=True)
    plan_rank = models.IntegerField()
    subscription_status = models.TextField(null=True)

    class Meta:
        db_table = "school_effective_plan"
        managed = True


# -----------------------------
# Core models
# -----------------------------

class ActivityTemplate(models.Model):
    key = models.CharField(
        max_length=32,
        choices=[
            ("lesson_course", "Lesson / Course"),
            ("experience", "Experience"),
            ("professional", "Professional Course")
        ],
        unique=True
    )
    description = models.TextField(blank=True, null=True)
    structure = models.JSONField(
        blank=True,
        null=True,
        help_text="JSON structure defining placeholders and requirements for this template type."
    )

    class Meta:
        db_table = "activity_template"
        verbose_name = "Activity Template"
        verbose_name_plural = "Activity Templates"
        managed = True

    def __str__(self):
        return dict(self._meta.get_field("key").choices).get(self.key, self.key)
class Activity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, unique=True)
    category = models.TextField(choices=ActivityCategory.choices, default=ActivityCategory.EXTREME)
    description = models.TextField(blank=True, null=True)
    slug = models.SlugField(unique=True)
    image = models.ImageField(
        upload_to="uploads/activities/",
        blank=True,
        null=True,
        help_text="Imagen representativa de la actividad"
    )
    templates = models.ManyToManyField('ActivityTemplate', related_name='activities', blank=True)

    class Meta:
        db_table = "activity"
        managed = True

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class School(models.Model):
    id = models.UUIDField(primary_key=True)
    country = models.ForeignKey(Country, on_delete=models.PROTECT, db_column="country_id")
    city = models.ForeignKey(City, on_delete=models.PROTECT, db_column="city_id")
    name = models.CharField(max_length=255, unique=True)
    slug = models.SlugField(unique=True)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=50, blank=True, null=True)
    website = models.URLField(blank=True, null=True)
    socials = models.JSONField(blank=True, null=True)
    description_short = models.TextField(blank=True, null=True)
    description_long = models.TextField(blank=True, null=True)
    logo = models.ImageField(
        upload_to="uploads/schools/logos/",
        blank=True,
        null=True,
        help_text="School Logo"
    )
    cover_image = models.ImageField(
        upload_to="uploads/schools/covers/",
        blank=True,
        null=True,
        help_text="Imagen de portada del perfil de la escuela"
    )
    verification_status = models.TextField(choices=VerificationStatus.choices)
    is_verified = models.BooleanField(default=False)
    verified_at = models.DateTimeField(blank=True, null=True)
    verified_by = models.TextField(blank=True, null=True)
    verification_notes = models.TextField(blank=True, null=True)
    doc_refs = models.TextField(blank=True, null=True)
    status = models.TextField(choices=SchoolStatus.choices)
    plan_type = models.CharField(
        max_length=16,
        choices=[("basic", "Basic"), ("premium", "Premium")],
        default="basic",
        help_text="Tipo de plan asignado a la escuela (editable desde el admin)."
    )
    is_featured = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    service_types = models.JSONField(
        blank=True,
        null=True,
        help_text="Selecciona los tipos de servicio ofrecidos (Level, Difficulty, Experience) desde el panel. Permite múltiples opciones."
    )

    class Meta:
        db_table = "school"
        managed = True

    def average_rating(self):
        from .models import SchoolReview
        avg = SchoolReview.objects.filter(school=self).aggregate(Avg("rating"))["rating__avg"]
        return round(avg or 0, 1)

    def __str__(self):
        return self.name

    def is_premium(self):
        return self.plan_type == "premium"

    def sync_activities_from_templates(self):
        """
        Crea automáticamente actividades y variantes base para la escuela usando Activity y ActivityTemplate.
        """
        from django.db import transaction
        from .models import Activity, ActivityTemplate, SchoolActivity, SchoolActivityVariant
        # Obtener todas las actividades y plantillas
        activities = Activity.objects.all()
        templates = ActivityTemplate.objects.all()
        with transaction.atomic():
            for activity in activities:
                school_activity, created = SchoolActivity.objects.get_or_create(
                    school=self,
                    activity=activity,
                    defaults={"is_active": True}
                )
                # Por cada plantilla, crear una variante base si no existe
                for template in templates:
                    variant_name = dict(ActivityTemplate._meta.get_field("key").choices).get(template.key, template.key)
                    SchoolActivityVariant.objects.get_or_create(
                        school_activity=school_activity,
                        name=variant_name,
                        defaults={
                            "description": template.description or "",
                            "structure_data": template.structure or {},
                            "offer_type": template.key,
                            "is_active": True,
                        }
                    )

    @property
    def stripe_account_id(self):
        """Proxy to SchoolFinance.stripe_account_id (no DB column on School)."""
        finance = getattr(self, "finance", None)
        return getattr(finance, "stripe_account_id", None)

    @property
    def is_stripe_verified(self):
        """Proxy to SchoolFinance.is_stripe_verified (no DB column on School)."""
        finance = getattr(self, "finance", None)
        return bool(getattr(finance, "is_stripe_verified", False))

    def ensure_finance(self):
        """Ensure there is a SchoolFinance row for this school and return it."""
        from .models import SchoolFinance
        finance, _ = SchoolFinance.objects.get_or_create(school=self)
        return finance



def get_default_structure():
    return {
        "languages": [],
        "equipment_included": True,
        "min_age": 10,
        "time_slots": ["09:00", "11:30", "15:00"],
        "note": "Horarios pueden variar según condiciones climáticas. Confirmar por WhatsApp.",
        "cancellation_policy": {
            "free_until_hours": 24,
            "late_fee_percent": 50
        }
    }

# Nueva estructura para SchoolActivity y SchoolActivityVariant
class SchoolActivity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey('School', on_delete=models.CASCADE, related_name='school_activities')
    activity = models.ForeignKey('Activity', on_delete=models.CASCADE, related_name='school_activities')
    activity_description = models.TextField(blank=True, null=True)
    activity_profile_image = models.ImageField(
        upload_to="uploads/schools/activities/",
        blank=True,
        null=True,
        help_text="Imagen representativa de la actividad dentro de la escuela"
    )
    structure_data = models.JSONField(
        blank=True,
        null=True,
        default=get_default_structure,
        help_text="Datos dinámicos para plantillas o configuraciones"
    )
    # Nuevo: fechas libres definidas por la escuela (lista de fechas YYYY-MM-DD)
    free_dates = models.JSONField(
        blank=True,
        null=True,
        help_text="Lista de fechas libres para reservas (YYYY-MM-DD)."
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "school_activity"
        verbose_name = "School Activity"
        verbose_name_plural = "School Activities"
        unique_together = ("school", "activity")
        managed = True

    def __str__(self):
        return f"{self.school.name} – {self.activity.name}"


class SchoolActivityVariant(models.Model):
    OFFER_TYPE_CHOICES = [
        ('lesson', 'Lesson'),
        ('course', 'Course'),
        ('experience', 'Experience'),
        ('rental', 'Rental'),
        ('pack', 'Pack'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school_activity = models.ForeignKey(
        'SchoolActivity',
        on_delete=models.CASCADE,
        related_name='variants'
    )
    name = models.CharField(max_length=150, help_text="Ej: Basic, Advanced, Master, etc.")
    description = models.TextField(blank=True, null=True)
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Precio en euros",
        blank=True,
        null=True
    )
    classes = models.PositiveIntegerField(blank=True, null=True, help_text="Número de clases o sesiones")
    persons = models.PositiveIntegerField(blank=True, null=True, help_text="Número de personas")
    instructors = models.PositiveIntegerField(blank=True, null=True, help_text="Número de instructores")
    duration_minutes = models.PositiveIntegerField(blank=True, null=True, help_text="Duración total en minutos")
    offer_type = models.CharField(max_length=32, choices=OFFER_TYPE_CHOICES, blank=True, null=True)

    difficulty = models.CharField(
        max_length=32,
        choices=[
            ('easy', 'Easy'),
            ('moderate', 'Moderate'),
            ('hard', 'Hard'),
            ('extreme', 'Extreme'),
        ],
        blank=True,
        null=True,
        help_text="Indica el nivel de dificultad si aplica (solo para offer_type='lesson' o 'course')."
    )

    experience_type = models.CharField(
        max_length=32,
        choices=[
            ('oneshot', 'One Shot'),
            ('adventure', 'Adventure'),
            ('expedition', 'Expedition'),
        ],
        blank=True,
        null=True,
        help_text="Indica el tipo de experiencia si offer_type='experience'."
    )

    included_services = models.JSONField(
        blank=True,
        null=True,
        help_text="Lista de servicios incluidos: ['Accommodation', 'Meals', 'Transport', ...]"
    )
    equipment_items = models.JSONField(
        blank=True,
        null=True,
        help_text="Equipos disponibles para alquiler si offer_type='rental'. Ejemplo: ['Tabla', 'Leash', 'Neoprene']"
    )
    pack_title = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Título o nombre comercial del paquete si offer_type='pack'"
    )
    pack_description_short = models.TextField(blank=True, null=True)
    date_start = models.DateField(blank=True, null=True)
    date_end = models.DateField(blank=True, null=True)
    selectable_dates = models.JSONField(
        blank=True,
        null=True,
        help_text="Lista de fechas seleccionables para reservas de esta variante (YYYY-MM-DD)."
    )

    equipment_included = models.BooleanField(
        default=False,
        help_text="Indica si el equipamiento está incluido en esta variante (visible para la escuela y los usuarios)."
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "school_activity_variant"
        verbose_name = "School Activity Variant"
        verbose_name_plural = "School Activity Variants"
        managed = True

    def __str__(self):
        return f"{self.name} – {self.school_activity.school.name} – {self.school_activity.activity.name}"
class SchoolActivitySession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school_activity = models.ForeignKey('SchoolActivity', on_delete=models.CASCADE, related_name='sessions')
    variant = models.ForeignKey('SchoolActivityVariant', on_delete=models.CASCADE, related_name='sessions', blank=True, null=True)
    date_start = models.DateField()
    date_end = models.DateField()
    time_slots = models.JSONField(
        blank=True,
        null=True,
        help_text="Lista de horarios, ejemplo: ['09:00-11:00', '15:00-17:00']"
    )
    capacity = models.PositiveIntegerField(help_text="Capacidad máxima de participantes para este grupo de fechas/horarios")
    is_available = models.BooleanField(default=True)
    bulk_generated = models.BooleanField(default=False, help_text="Indica si la sesión fue creada por una acción masiva.")
    session_metadata = models.JSONField(blank=True, null=True, help_text="Metadatos adicionales de la sesión (ejemplo: origen, lote, etc.)")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "school_activity_session"
        verbose_name = "School Activity Session"
        verbose_name_plural = "School Activity Sessions"
        managed = True

    def __str__(self):
        return f"{self.school_activity.school.name} – {self.school_activity.activity.name} ({self.date_start} → {self.date_end})"




class Media(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey(School, on_delete=models.CASCADE, db_column="school_id")
    kind = models.TextField(choices=MediaKind.choices, default=MediaKind.IMAGE)
    file = models.FileField(upload_to="uploads/media/", blank=True, null=True)
    url = models.TextField(blank=True, null=True)
    position = models.IntegerField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "media"
        managed = True

    def clean(self):
        # Al menos una fuente: archivo o URL
        super().clean()
        if not self.file and not self.url:
            raise ValidationError({
                "file": "Sube un archivo o ingresa una URL.",
                "url": "Sube un archivo o ingresa una URL.",
            })

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def media_url(self):
        if self.file:
            try:
                return self.file.url
            except ValueError:
                return None
        return self.url

    @property
    def is_local(self):
        return bool(self.file)


# -----------------------------
# Rules / Overrides
# -----------------------------
class ActivityRule(models.Model):
    activity = models.ForeignKey(Activity, db_column="activity_id", on_delete=models.CASCADE)
    require_sea = models.BooleanField()
    require_large_lake = models.BooleanField()
    allow_indoor = models.BooleanField()

    class Meta:
        db_table = "activity_rule"
        managed = True
        unique_together = ("activity",)

    def __str__(self):
        return f"Rule for {self.activity.slug if hasattr(self.activity, 'slug') else self.activity}"


class ActivityOverride(models.Model):
    activity = models.ForeignKey(Activity, db_column="activity_id", on_delete=models.CASCADE)
    country = models.ForeignKey(Country, db_column="country_id", on_delete=models.CASCADE)
    city = models.ForeignKey(City, db_column="city_id", on_delete=models.CASCADE, blank=True, null=True)
    allow = models.BooleanField()

    class Meta:
        db_table = "activity_override"
        managed = True
        unique_together = ("activity", "country", "city")

    def __str__(self):
        city_str = f", City: {self.city}" if self.city else ""
        return f"Override: {self.activity.slug if hasattr(self.activity, 'slug') else self.activity} in {self.country}{city_str} - {'Allowed' if self.allow else 'Not allowed'}"


# -----------------------------
# Popular Destinations
# -----------------------------
class PopularDestination(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    city = models.ForeignKey(City, db_column="city_id", on_delete=models.CASCADE)
    slug = models.SlugField(unique=True)
    title = models.CharField(max_length=255)
    description_short = models.TextField()
    description_long = models.TextField(blank=True, null=True)
    image_card = models.ImageField(upload_to="uploads/popular/card/", blank=True, null=True)
    image_hero = models.ImageField(upload_to="uploads/popular/hero/", blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        db_table = "popular_destination"
        managed = True

    def __str__(self):
        return self.title


# -----------------------------
# City Extra + Gallery
# -----------------------------
class CityExtra(models.Model):
    city = models.OneToOneField(City, db_column="city_id", on_delete=models.CASCADE, primary_key=True)
    description_short = models.TextField(blank=True, null=True)
    description_long = models.TextField(blank=True, null=True)
    image_hero = models.ImageField(upload_to="uploads/cities/hero/", blank=False, null=False)
    image_square = models.ImageField(
        upload_to="uploads/cities/square/",
        blank=True,
        null=True,
        help_text="Square thumbnail for grid or card display"
    )

    class Meta:
        db_table = "city_extra"
        managed = True

    def __str__(self):
        return f"Extra for {self.city.name}"


class CityActivityGallery(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # NOT NULL en DB — se completa automáticamente desde city_extra en save()
    city = models.ForeignKey(
        City,
        db_column="city_id",
        on_delete=models.CASCADE,
        editable=False,
        related_name="gallery_items",
    )
    city_extra = models.ForeignKey(
        CityExtra,
        db_column="city_extra_id",
        on_delete=models.CASCADE,
        related_name="gallery_items",
    )
    activity = models.ForeignKey(
        Activity,
        db_column="activity_id",
        on_delete=models.CASCADE,
        related_name="city_gallery_items",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "city_activity_gallery"
        managed = True
        unique_together = ("city", "activity")
        ordering = ["id"]

    def save(self, *args, **kwargs):
        # Completa city desde CityExtra antes de guardar (evita NULL en DB)
        if self.city_extra and (self.city_id is None or self.city_id != self.city_extra.city_id):
            self.city_id = self.city_extra.city_id
        super().save(*args, **kwargs)

    def __str__(self):
        city_name = self.city_extra.city.name if self.city_extra_id else (self.city.name if self.city_id else "?")
        act = self.activity.name if self.activity_id else "?"
        return f"{city_name} – {act}"


class CityActivityImage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    gallery = models.ForeignKey(CityActivityGallery, on_delete=models.CASCADE, related_name="images")
    file = models.ImageField(upload_to="uploads/cities/gallery/", blank=False, null=False)
    position = models.IntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "city_activity_image"
        ordering = ["position", "id"]

    def __str__(self):
        return f"Image {self.position} for {self.gallery}"
    
class SchoolBlog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey(School, on_delete=models.CASCADE, db_column="school_id")
    title = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    content = models.TextField()
    cover_image = models.ImageField(upload_to="uploads/blogs/", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "school_blog"
        managed = True

    def __str__(self):
        return f"{self.school.name} – {self.title}"
    
class SchoolReview(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey(School, on_delete=models.CASCADE, db_column="school_id")
    user = models.ForeignKey(User, on_delete=models.CASCADE, db_column="user_id", related_name="school_reviews")
    rating = models.IntegerField()
    comment = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "school_review"
        managed = True
        ordering = ["-created_at"]

    def clean(self):
        # Only allow one review per user per school
        if self.user_id and self.school_id:
            existing = SchoolReview.objects.filter(school=self.school, user=self.user).exclude(id=self.id)
            if existing.exists():
                raise ValidationError("Solo puedes dejar una reseña por escuela. Si quieres cambiar tu opinión, edita la existente.")
        if not (1 <= self.rating <= 5):
            raise ValidationError("El rating debe estar entre 1 y 5 estrellas.")

    @property
    def user_display_name(self):
        full_name = f"{self.user.first_name} {self.user.last_name}".strip()
        return full_name if full_name else self.user.username

    def __str__(self):
        return f"{self.user_display_name} – {self.rating}★ para {self.school.name}"
    
# -----------------------------
# Instructor / Freelancer models
# -----------------------------
class Instructor(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    activities = models.ManyToManyField('Activity', through='InstructorActivity', related_name='instructors')
    country = models.ForeignKey(Country, on_delete=models.PROTECT, db_column="country_id")
    city = models.ForeignKey(City, on_delete=models.PROTECT, db_column="city_id")
    bio_short = models.TextField(blank=True, null=True)
    bio_long = models.TextField(blank=True, null=True)
    age = models.IntegerField(blank=True, null=True)
    gender = models.CharField(max_length=20, blank=True, null=True)
    languages = models.TextField(blank=True, null=True)
    profile_image = models.ImageField(
        upload_to="uploads/instructors/profiles/",
        blank=True,
        null=True,
        help_text="Foto de perfil del instructor"
    )
    cover_image = models.ImageField(
        upload_to="uploads/instructors/covers/",
        blank=True,
        null=True,
        help_text="Imagen de portada del instructor"
    )
    certifications = models.TextField(blank=True, null=True)
    portfolio_description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "instructor"
        managed = True

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username}"


# Nueva tabla intermedia para Instructor y Activity
class InstructorActivity(models.Model):
    instructor = models.ForeignKey('Instructor', on_delete=models.CASCADE)
    activity = models.ForeignKey(
        'Activity',
        on_delete=models.CASCADE,
        db_column="activity_id",
        to_field="id"  # asegura que apunta al campo 'id' de Activity (tipo CharField)
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "instructor_activity"
        managed = True
        unique_together = ("instructor", "activity")

    def __str__(self):
        return f"{self.instructor} – {self.activity}"


class InstructorMedia(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    instructor = models.ForeignKey(Instructor, on_delete=models.CASCADE, db_column="instructor_id")
    kind = models.TextField(choices=MediaKind.choices, default=MediaKind.IMAGE)
    file = models.FileField(upload_to="uploads/instructors/media/", blank=True, null=True)
    url = models.TextField(blank=True, null=True)
    position = models.IntegerField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "instructor_media"
        managed = True
        ordering = ["position", "id"]

    def clean(self):
        super().clean()
        if not self.file and not self.url:
            raise ValidationError({
                "file": "Debes subir un archivo o agregar una URL.",
                "url": "Debes subir un archivo o agregar una URL."
            })

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"Media for {self.instructor}"


class InstructorPlan(models.TextChoices):
    STANDARD = "standard", "Standard"
    PREMIUM = "premium", "Premium"

class InstructorSubscription(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    instructor = models.ForeignKey(Instructor, on_delete=models.CASCADE, db_column="instructor_id")
    plan = models.TextField(choices=InstructorPlan.choices)
    status = models.TextField(choices=SubscriptionStatus.choices)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "instructor_subscription"
        managed = True

    def __str__(self):
        return f"{self.instructor} – {self.plan}"


class InstructorReview(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    instructor = models.ForeignKey(Instructor, on_delete=models.CASCADE, db_column="instructor_id")
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    rating = models.IntegerField()
    comment = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "instructor_review"
        managed = True
        ordering = ["-created_at"]

    def clean(self):
        if self.rating < 1 or self.rating > 5:
            raise ValidationError("La puntuación debe estar entre 1 y 5 estrellas.")

    @property
    def user_name(self):
        return f"{self.user.first_name} {self.user.last_name}".strip() or self.user.username

    def __str__(self):
        return f"{self.user_name} – {self.rating}★ para {self.instructor}"

    @staticmethod
    def average_rating_for_instructor(instructor):
        avg = InstructorReview.objects.filter(instructor=instructor).aggregate(Avg("rating"))["rating__avg"]
        return round(avg or 0, 1)
############################################################
# Stripe Connect & Suscripciones de Escuelas
# Modelos para integración de pagos, planes y comisiones
############################################################

# Precios oficiales de los planes de escuela (en euros)
SCHOOL_PLAN_PRICES = {
    "basic": Decimal("0.00"),     # Basic: Gratis
    "premium": Decimal("499.00"), # Premium: 499€ anuales
}

# Porcentaje de comisión por plan (puedes modificar aquí)
SCHOOL_PLAN_FEES = {
    "basic": Decimal("0.25"),   # 25% comisión para Basic
    "premium": Decimal("0.20"), # 20% comisión para Premium
}

class SchoolFinance(models.Model):
    """
    Información financiera de la escuela para Stripe Connect y suscripción.
    """
    school = models.OneToOneField('School', on_delete=models.CASCADE, related_name="finance")
    stripe_account_id = models.CharField(max_length=128, blank=True, null=True, unique=False)
    is_stripe_verified = models.BooleanField(default=False)
    plan = models.CharField(
        max_length=16,
        choices=[("basic", "Basic"), ("premium", "Premium")],
        default="basic"
    )
    plan_price_eur = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=SCHOOL_PLAN_PRICES["basic"],
        help_text="Precio actual del plan en EUR. Modificar precios oficiales en SCHOOL_PLAN_PRICES."
    )
    subscription_active = models.BooleanField(default=False)
    subscription_start = models.DateTimeField(null=True, blank=True)
    subscription_end = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "directory_schoolfinance"
        managed = True
        verbose_name = "School Finance"
        verbose_name_plural = "School Finances"

    def get_fee_rate(self):
        """
        Devuelve la fracción decimal de comisión del plan actual (ej: 0.25 para 25%)
        """
        return SCHOOL_PLAN_FEES.get(self.plan, Decimal("0.10"))

    def get_fee_percent(self):
        """Retrocompatibilidad: devuelve el fee como porcentaje (20 o 25)."""
        return (self.get_fee_rate() * Decimal("100")).quantize(Decimal("0.01"))

    def apply_commission(self, amount):
        """
        Calcula la comisión y el neto para un monto dado.
        Retorna (fee, net)
        """
        fee_rate = self.get_fee_rate()
        fee = (amount * fee_rate).quantize(Decimal("0.01"))
        net = (amount - fee).quantize(Decimal("0.01"))
        return fee, net

    def get_plan_price(self):
        """
        Devuelve el precio oficial del plan (ver SCHOOL_PLAN_PRICES).
        """
        return SCHOOL_PLAN_PRICES.get(self.plan, self.plan_price_eur)

    def activate_subscription(self, start=None, end=None):
        """
        Activa la suscripción y actualiza fechas.
        """
        from django.utils import timezone
        self.subscription_active = True
        self.subscription_start = start or timezone.now()
        self.subscription_end = end
        self.save(update_fields=["subscription_active", "subscription_start", "subscription_end"])

    def deactivate_subscription(self):
        """
        Desactiva la suscripción.
        """
        self.subscription_active = False
        self.subscription_end = None
        self.save(update_fields=["subscription_active", "subscription_end"])

    @property
    def fee_percent_display(self):
        """Devuelve el porcentaje de comisión como número (ej: 25.00 para 25%)"""
        return (self.get_fee_rate() * Decimal("100")).quantize(Decimal("0.01"))

    def __str__(self):
        return f"{self.school.name} – {self.plan.capitalize()} ({'Activa' if self.subscription_active else 'Inactiva'})"


class SchoolTransaction(models.Model):
    """
    Registro de pagos recibidos por la escuela a través de Stripe.
    Calcula automáticamente comisión y neto.
    """
    school = models.ForeignKey('School', on_delete=models.CASCADE, related_name="transactions")
    booking = models.ForeignKey('Booking', on_delete=models.CASCADE, related_name="transactions", null=True, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2, help_text="Monto bruto en EUR")
    fee_percent = models.DecimalField(max_digits=5, decimal_places=2, help_text="Porcentaje de comisión aplicado", editable=False)
    fee_amount = models.DecimalField(max_digits=10, decimal_places=2, help_text="Monto de la comisión en EUR", editable=False)
    net_amount = models.DecimalField(max_digits=10, decimal_places=2, help_text="Monto neto para la escuela en EUR", editable=False)
    stripe_payment_id = models.CharField(max_length=128, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_released = models.BooleanField(default=False, help_text="Indica si el pago ya fue transferido a la escuela.")

    class Meta:
        db_table = "directory_schooltransaction"
        managed = True
        verbose_name = "School Transaction"
        verbose_name_plural = "School Transactions"

    def save(self, *args, **kwargs):
        # Calcular comisión y neto usando SchoolFinance (siempre, incluso en updates)
        try:
            finance = self.school.finance
        except SchoolFinance.DoesNotExist:
            finance = SchoolFinance.objects.create(
                school=self.school,
                stripe_account_id=f"auto_{uuid.uuid4().hex[:10]}",
                plan="basic",
                is_stripe_verified=False,
                subscription_active=False
            )

        fee_rate = finance.get_fee_rate()  # ej: 0.20
        fee, net = finance.apply_commission(self.amount)

        # Guardar como porcentaje (20.00) y valores calculados
        self.fee_percent = (fee_rate * Decimal("100")).quantize(Decimal("0.01"))
        self.fee_amount = fee
        self.net_amount = net

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.school.name}: {self.amount}€ (fee {self.fee_amount}€, net {self.net_amount}€)"


class SchoolSubscription(models.Model):
    """
    Estado de la suscripción Stripe de la escuela.
    """
    school = models.OneToOneField('School', on_delete=models.CASCADE, related_name="subscription")
    plan = models.CharField(
        max_length=16,
        choices=[("basic", "Basic"), ("premium", "Premium")],
        default="basic"
    )
    stripe_subscription_id = models.CharField(max_length=128, unique=True)
    stripe_customer_id = models.CharField(max_length=128)
    current_period_start = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=16,
        choices=[
            ("active", "Active"),
            ("pending", "Pending"),
            ("past_due", "Past Due"),
            ("canceled", "Canceled"),
            ("expired", "Expired"),
        ],
        default="active"
    )

    class Meta:
        db_table = "directory_schoolsubscription"
        managed = True
        verbose_name = "School Subscription"
        verbose_name_plural = "School Subscriptions"

    def activate_from_stripe(self, subscription_obj):
        """
        Actualiza los campos desde un objeto de suscripción de Stripe (webhook).
        """
        from django.utils import timezone
        self.plan = subscription_obj.get("plan", self.plan)
        self.stripe_subscription_id = subscription_obj.get("id", self.stripe_subscription_id)
        self.stripe_customer_id = subscription_obj.get("customer", self.stripe_customer_id)
        # Stripe timestamps are in seconds; Django expects datetime
        import datetime
        if subscription_obj.get("current_period_start"):
            self.current_period_start = datetime.datetime.fromtimestamp(
                int(subscription_obj["current_period_start"]), tz=timezone.utc
            )
        if subscription_obj.get("current_period_end"):
            self.current_period_end = datetime.datetime.fromtimestamp(
                int(subscription_obj["current_period_end"]), tz=timezone.utc
            )
        self.status = subscription_obj.get("status", self.status)
        self.save()

    def __str__(self):
        return f"{self.school.name} – {self.plan.capitalize()} ({self.status})"


class SchoolActivitySeason(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school_activity = models.ForeignKey(
        'SchoolActivity',
        on_delete=models.CASCADE,
        related_name="seasons"
    )
    season_type = models.CharField(
        max_length=16,
        choices=[
            ('high', 'High'),
            ('mid', 'Mid'),
            ('low', 'Low'),
        ],
        blank=True,
        null=True
    )
    start_month = models.IntegerField(blank=True, null=True)
    end_month = models.IntegerField(blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    # Eliminado: days_of_week (ya no se usa para reservas por calendario)
    # days_of_week = models.JSONField(blank=True, null=True, help_text="Días de la semana activos para esta temporada (ej: ['Mon', 'Wed', 'Fri'])")
    # Nuevo: fechas libres específicas para la temporada (si aplica)
    free_dates = models.JSONField(
        blank=True,
        null=True,
        help_text="Fechas libres específicas para esta temporada (YYYY-MM-DD)."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "school_activity_season"
        managed = True
        verbose_name = "School Activity Season"
        verbose_name_plural = "School Activity Seasons"

    def __str__(self):
        return f"{self.school_activity.school.name} – {self.school_activity.activity.name} – {self.get_season_type_display()} Season"
    

# Helper actualizado para generar sesiones desde fechas libres (no basadas en días de semana)
def generate_sessions_from_structure_data(activity_instance):
    """
    Genera automáticamente sesiones en base a fechas libres y/o rangos definidos en la actividad o sus variantes.
    Compatible con la nueva estructura basada en fechas específicas.
    Cada sesión generada cubre el rango de fechas indicado (date_start a date_end).
    Deja preparado el helper para soportar time_slots en el futuro.
    """
    import datetime
    # 1. Procesar fechas libres a nivel de SchoolActivity
    free_dates = activity_instance.free_dates or []
    if free_dates:
        try:
            parsed_dates = sorted([
                datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                for date_str in free_dates
            ])
        except Exception:
            parsed_dates = []
        if parsed_dates:
            # Una sola sesión desde la menor a la mayor fecha
            SchoolActivitySession.objects.get_or_create(
                school_activity=activity_instance,
                date_start=parsed_dates[0],
                date_end=parsed_dates[-1],
                defaults={
                    'is_available': True,
                    'bulk_generated': True,
                    'session_metadata': {'source': 'activity_free_dates', 'dates': free_dates},
                    # 'time_slots': None, # Preparado para el futuro
                }
            )
    # 2. Procesar rangos de fechas a nivel de SchoolActivity
    # 3. Procesar fechas libres y rangos en variantes
    for variant in activity_instance.variants.all():
        # fechas seleccionables
        selectable_dates = variant.selectable_dates or []
        if selectable_dates:
            try:
                parsed_dates = sorted([
                    datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                    for date_str in selectable_dates
                ])
            except Exception:
                parsed_dates = []
            if parsed_dates:
                SchoolActivitySession.objects.get_or_create(
                    school_activity=activity_instance,
                    variant=variant,
                    date_start=parsed_dates[0],
                    date_end=parsed_dates[-1],
                    defaults={
                        'is_available': True,
                        'bulk_generated': True,
                        'session_metadata': {'source': 'variant_selectable_dates', 'dates': selectable_dates},
                        # 'time_slots': None,
                    }
                )


# -----------------------------
# Señales para sincronización automática
# -----------------------------
from django.db.models.signals import post_save
from django.dispatch import receiver

@receiver(post_save, sender=School)
def create_school_activities_from_templates(sender, instance, created, **kwargs):
    if created:
        instance.sync_activities_from_templates()


@receiver(post_save, sender=SchoolActivityVariant)
def variant_generate_sessions(sender, instance, created, **kwargs):
    if created:
        # Llama al helper para generar sesiones automáticamente para la variante recién creada
        generate_sessions_from_structure_data(instance.school_activity)