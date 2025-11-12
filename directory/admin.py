from django.contrib import admin, messages
from django.http import JsonResponse
from django.urls import path, re_path
from django.utils.html import format_html
from django.db import connection
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet
from django import forms
from django.forms import FileInput, widgets
from django.db.models import Max
from django.utils.safestring import mark_safe
from django.conf import settings
from django.db import models
from functools import lru_cache

from .models import (
    Activity,
    School,
    SchoolActivity,
    SchoolActivityVariant,
    Media,
    SchoolStatus,
    MediaKind,
    ActivityRule,
    ActivityOverride,
    PopularDestination,
    CityExtra,
    CityActivityGallery,
    CityActivityImage,
    SchoolBlog,
    SchoolReview,
    Instructor,
    InstructorMedia,
    InstructorSubscription,
    InstructorReview,
    InstructorActivity,
    SchoolFinance,
    SchoolTransaction,
    SchoolSubscription,
    ActivityTemplate,
    SchoolActivitySeason,
    SchoolActivitySession,
)
###############################################################################
# ADMIN UTILS & HELPERS (scalable, reusable, for querysets and more)
###############################################################################

# --- Queryset utilities for scalable autocomplete and filtering ---------------
def get_school_queryset():
    """Utility: Returns a queryset for School, optimized for autocomplete (scalable)."""
    return School.objects.only("id", "name").order_by("name")

def get_activity_queryset():
    """Utility: Returns a queryset for Activity, optimized for autocomplete (scalable)."""
    return Activity.objects.only("id", "name").order_by("name")

def get_effective_plan(school_id):
    """Return current plan string (basic|premium) or None from SchoolFinance."""
    try:
        return SchoolFinance.objects.get(school_id=school_id).plan
    except SchoolFinance.DoesNotExist:
        return None

def get_plan_rank(plan):
    return {"premium": 2, "basic": 1}.get(plan or "", 0)


###############################################################################
# FORM & WIDGET STANDARDIZATION (scalable, reusable, headless compatible)
###############################################################################

class MultiFileInput(FileInput):
    """Widget: Enables multiple file selection (for custom admin or headless CMS)."""
    allow_multiple_selected = True


# --- Inlines & validations ----------------------------------------------------

#
# MediaInlineFormSet: Plan-based media upload limits removed for admin flexibility.
#


class MediaInlineForm(forms.ModelForm):
    """Standardized admin form for Media: file or url required, for compatibility."""
    class Meta:
        model = Media
        fields = ["kind", "file", "url", "position"]
    def clean(self):
        cleaned = super().clean()
        file_field = cleaned.get("file")
        url = cleaned.get("url")
        inst = self.instance if hasattr(self, "instance") else None
        existing_file = getattr(inst, "file", None)
        existing_file_name = getattr(existing_file, "name", "") if existing_file else ""
        existing_url = getattr(inst, "url", "") if inst else ""
        has_existing = bool(existing_file_name or existing_url)
        if not file_field and not url and not has_existing:
            raise ValidationError("You must upload a file or provide a URL.")
        return cleaned

class MediaInline(admin.TabularInline):
    """Inline for Media: standardized fields and preview, scalable for headless."""
    model = Media
    form = MediaInlineForm
    extra = 1
    fields = ("kind", "file", "url", "preview", "position", "created_at", "updated_at")
    help_texts = {
        "file": "Upload an image/video from your device.",
        "url": "Or paste a public URL (S3, CDN, etc.). Provide only one of the two.",
    }
    readonly_fields = ("preview", "created_at", "updated_at")
    ordering = ("position",)
    def preview(self, obj):
        """Image preview for admin and headless compatibility."""
        if not obj:
            return ""
        src = None
        try:
            if getattr(obj, "file", None) and getattr(obj.file, "url", ""):
                src = obj.file.url
        except Exception:
            src = None
        if not src and getattr(obj, "url", None):
            src = obj.url
        if src and obj.kind == MediaKind.IMAGE:
            return mark_safe(
                f'<img src="{src}" style="max-height:80px;max-width:140px;border:1px solid #ddd;border-radius:4px;" />'
            )
        return src or ""
    preview.short_description = "Preview"


#
# SchoolActivityInlineFormSet: Plan-based activity count limits removed for admin flexibility.
#


class SchoolActivityInline(admin.StackedInline):
    """Inline for SchoolActivity: scalable, standardized for headless CMS."""
    model = SchoolActivity
    extra = 1
    fields = ("activity", "activity_profile_image", "activity_description")

class SchoolFinanceInline(admin.StackedInline):
    """Inline for SchoolFinance, for direct plan editing (scalable)."""
    model = SchoolFinance
    extra = 0
    max_num = 1
    fk_name = "school"
    fields = ("plan", "is_stripe_verified", "subscription_active", "subscription_start", "subscription_end")
    readonly_fields = ("subscription_start", "subscription_end")




# --- ModelAdmins --------------------------------------------------------------

@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "slug")
    search_fields = ("name",)
    list_filter = ("category",)
    filter_horizontal = ("templates",)



# --- JSON Editor for ActivityTemplate.structure -----------------------------
class JSONTextarea(widgets.Textarea):
    """A simple JSON editor using a textarea. If you want a more advanced widget, you could use a 3rd party widget."""
    def __init__(self, attrs=None):
        final_attrs = {"style": "font-family:monospace;width:90%;min-height:120px"}
        if attrs:
            final_attrs.update(attrs)
        super().__init__(final_attrs)

class ActivityTemplateForm(forms.ModelForm):
    class Meta:
        model = ActivityTemplate
        fields = "__all__"
        widgets = {
            "structure": JSONTextarea(attrs={"rows": 10}),
        }

@admin.register(ActivityTemplate)
class ActivityTemplateAdmin(admin.ModelAdmin):
    form = ActivityTemplateForm
    list_display = ("key", "description")
    search_fields = ("key", "description")
    ordering = ("key",)
    readonly_fields = ()


class SchoolForm(forms.ModelForm):
    SERVICE_TYPE_CHOICES = [
        ("level", "Level"),
        ("difficulty", "Difficulty"),
        ("experience", "Experience"),
    ]
    service_types = forms.MultipleChoiceField(
        choices=SERVICE_TYPE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        help_text="Select one or more attributes. This defines which attributes appear in activity variants. Multiple selections are allowed.",
    )

    class Meta:
        model = School
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            value = self.instance.service_types
            if isinstance(value, list):
                self.fields["service_types"].initial = value
            elif isinstance(value, str):
                import json
                try:
                    self.fields["service_types"].initial = json.loads(value)
                except Exception:
                    self.fields["service_types"].initial = []
            else:
                self.fields["service_types"].initial = []

    def clean_service_types(self):
        value = self.cleaned_data["service_types"]
        return value

    def save(self, commit=True):
        instance = super().save(commit=False)
        stypes = self.cleaned_data.get("service_types", [])
        import json
        instance.service_types = stypes
        if commit:
            instance.save()
        return instance


@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    form = SchoolForm
    list_display = ("name", "email", "slug", "city", "country", "status", "is_verified", "get_plan", "created_at")
    list_filter = ("country", "city", "status", "is_verified")
    search_fields = ("name", "city__name", "country__name")
    readonly_fields = ("created_at", "updated_at")
    inlines = [
        SchoolFinanceInline,
        MediaInline,
        SchoolActivityInline,
    ]

    # Add service_types to fields/fieldsets, and add slug after name, and email after name
    fieldsets = (
        (None, {
            "fields": (
                "name",
                "email",
                "slug",
                "city",
                "country",
                "status",
                "service_types",
                "is_verified",
                "created_at",
                "updated_at",
            ),
        }),
    )

    def get_readonly_fields(self, request, obj=None):
        readonly = list(self.readonly_fields)
        if not request.user.is_superuser:
            readonly.append("slug")
        return readonly

    def get_plan(self, obj):
        plan = get_effective_plan(obj.id)
        rank = get_plan_rank(plan)
        return f"{plan or '—'} ({rank})"
    get_plan.short_description = "Plan"

    def save_model(self, request, obj, form, change):
        # User-friendly validations aligned to DB rules
        new_status = form.cleaned_data.get("status")
        if new_status == SchoolStatus.ACTIVE:
            finance, created = SchoolFinance.objects.get_or_create(school=obj)
            if finance.plan != "premium" or not finance.subscription_active:
                finance.plan = "basic"
                finance.save()
            if finance.plan == "premium" and not finance.subscription_active:
                raise ValidationError("Premium schools must have an active subscription.")
        super().save_model(request, obj, form, change)

    def save_formset(self, request, form, formset, change):
        # Ya no guardamos manualmente a disco: que lo maneje el FileField
        formset.save()
        super().save_formset(request, form, formset, change)

    actions = ["marcar_verificada", "desmarcar_verificada"]

    def marcar_verificada(self, request, queryset):
        updated = queryset.update(is_verified=True)
        self.message_user(request, f"{updated} school(s) marked as verified.", level=messages.SUCCESS)

    def desmarcar_verificada(self, request, queryset):
        updated = queryset.update(is_verified=False)
        self.message_user(request, f"{updated} school(s) unmarked as verified.", level=messages.INFO)




###############################################################################
# INLINE VARIANTS: AJAX, dynamic, scalable for external integrations
###############################################################################

class ExpandableJSONWidget(widgets.Textarea):
    """Widget: Expand/collapse JSON for admin or headless CMS."""
    def render(self, name, value, attrs=None, renderer=None):
        import json
        if value and not isinstance(value, str):
            try:
                value = json.dumps(value, indent=2, ensure_ascii=False)
            except Exception:
                pass
        html = super().render(name, value, attrs, renderer)
        return mark_safe(
            f'''<div>
                <button type="button" onclick="var n=this.nextElementSibling; n.style.display=n.style.display==='none'?'block':'none';">{'Mostrar/Ocultar'}</button>
                <div style="display:none">{html}</div>
            </div>'''
        )

class SchoolActivityVariantInline(admin.TabularInline):
    """
    Inline for SchoolActivityVariant with AJAX/dynamic support and query caching.
    Designed for scalable admin, headless, or API-based integrations.
    """
    model = SchoolActivityVariant
    extra = 0
    can_delete = True
    show_change_link = True
    fields = (
        "name",
        "offer_type",
        "price",
        "classes",
        "persons",
        "instructors",
        "levels",
        "difficulty",
        "experience_type",
        "duration_minutes",
        "equipment_included",
        "is_active",
        "created_at",
        "updated_at",
    )
    readonly_fields = ("created_at", "updated_at")
    formfield_overrides = {
        models.JSONField: {
            "widget": ExpandableJSONWidget,
        },
    }

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == "levels":
            return forms.MultipleChoiceField(
                choices=[
                    ("beginner", "Beginner"),
                    ("intermediate", "Intermediate"),
                    ("advanced", "Advanced"),
                ],
                widget=forms.CheckboxSelectMultiple,
                required=False,
            )
        return super().formfield_for_dbfield(db_field, request, **kwargs)

class SchoolActivitySeasonInline(admin.TabularInline):
    """Inline for SchoolActivitySeason: scalable, standardized."""
    model = SchoolActivitySeason
    extra = 0
    can_delete = True
    show_change_link = True
    fields = (
        "season_type",
        "start_month",
        "end_month",
        "is_active",
        "created_at",
        "updated_at",
    )
    readonly_fields = ("created_at", "updated_at")


@admin.register(SchoolActivity)
class SchoolActivityAdmin(admin.ModelAdmin):
    """
    Admin for SchoolActivity: scalable, modular, supports headless, API, or custom admin.
    """
    list_display = ("school", "activity", "is_active", "created_at", "updated_at")
    list_filter = ("is_active", "school__country", "school__city", "activity")
    search_fields = ("school__name", "activity__name", "activity_description")
    readonly_fields = ("created_at", "updated_at")
    inlines = [SchoolActivityVariantInline, SchoolActivitySeasonInline]
    formfield_overrides = {
        models.JSONField: {"widget": JSONTextarea},
    }
    autocomplete_fields = ["school", "activity"]

    fieldsets = (
        ("General Info", {
            "fields": (
                "school",
                "activity",
                "activity_profile_image",
                "activity_description",
                "structure_data",
                "is_active",
            ),
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
        }),
    )

    def save_formset(self, request, form, formset, change):
        """
        Save inlines with error resilience and performance.
        Can be extended for caching, async, or headless usage.
        """
        import traceback
        try:
            if formset.is_valid():
                instances = formset.save(commit=False)
                for obj in instances:
                    obj.save()
                formset.save_m2m()
                for obj in formset.deleted_objects:
                    obj.delete()
                messages.success(request, "✅ Cambios guardados correctamente.")
            else:
                messages.warning(request, f"⚠️ Advertencia: se detectaron errores no críticos en los inlines. Se intentará guardar de todas formas.")
                instances = formset.save(commit=False)
                for obj in instances:
                    try:
                        obj.save()
                    except Exception as e:
                        print("Error al guardar objeto inline:", e)
                formset.save_m2m()
                for obj in formset.deleted_objects:
                    obj.delete()
        except Exception as e:
            print("❌ EXCEPCIÓN EN save_formset:", e)
            print(traceback.format_exc())
            messages.error(request, f"❌ Error al guardar: {e}")
        super().save_formset(request, form, formset, change)


# --- Stripe-based school finance admins ---
@admin.register(SchoolFinance)
class SchoolFinanceAdmin(admin.ModelAdmin):
    list_display = ("school", "plan", "is_stripe_verified", "subscription_active", "subscription_start", "subscription_end")
    list_filter = ("plan", "is_stripe_verified", "subscription_active")
    search_fields = ("school__name", "stripe_account_id")
    readonly_fields = ("subscription_start", "subscription_end")

@admin.register(SchoolTransaction)
class SchoolTransactionAdmin(admin.ModelAdmin):
    list_display = ("school", "amount", "fee_percent", "fee_amount", "net_amount", "created_at")
    list_filter = ("school",)
    search_fields = ("school__name", "stripe_payment_id")
    readonly_fields = ("created_at",)

    actions = ["mark_as_paid_and_notify_school"]

    def mark_as_paid_and_notify_school(self, request, queryset):
        """
        Custom admin action: Mark selected transactions as released and notify school by email.
        """
        sent = 0
        for transaction in queryset:
            transaction.is_released = True
            transaction.released_at = timezone.now()
            transaction.save()
            school = getattr(transaction, "school", None)
            school_email = getattr(school, "email", None)
            school_name = getattr(school, "name", "School")
            released_at = transaction.released_at.strftime("%Y-%m-%d %H:%M")
            net_amount = getattr(transaction, "net_amount", None)
            if net_amount is None:
                net_amount = "-"
            if school_email:
                subject = f"[PAYMENT CONFIRMATION] Payment sent for transaction {transaction.id}"
                message = (
                    f"Dear {school_name},\n\n"
                    f"We’ve sent your payout for transaction ID {transaction.id}.\n"
                    f"Amount: €{net_amount}\n"
                    f"Date: {released_at}\n\n"
                    f"Thank you for partnering with The Travel Wild!\n"
                    f"— The Travel Wild Team"
                )
                send_mail(
                    subject,
                    message,
                    None,  # from_email: use settings.DEFAULT_FROM_EMAIL
                    [school_email],
                    fail_silently=False,
                )
                sent += 1
        self.message_user(request, f"{sent} school(s) notified and marked as paid.", level=messages.SUCCESS)
    mark_as_paid_and_notify_school.short_description = "Mark as paid and notify school"

    def has_module_permission(self, request):
        return True

@admin.register(SchoolSubscription)
class SchoolSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("school", "plan", "status", "current_period_start", "current_period_end")
    list_filter = ("plan", "status")
    search_fields = ("school__name", "stripe_subscription_id", "stripe_customer_id")
    readonly_fields = ("current_period_start", "current_period_end")
    def has_module_permission(self, request):
        return True


@admin.register(Media)
class MediaAdmin(admin.ModelAdmin):
    list_display = ("preview", "school", "kind", "position", "created_at")
    list_filter = ("kind",)
    search_fields = ("school__name", "file")
    ordering = ("school", "position")
    readonly_fields = ("preview", "created_at", "updated_at")

    def preview(self, obj):
        if not obj:
            return ""
        src = None
        try:
            if getattr(obj, "file", None) and getattr(obj.file, "url", ""):
                src = obj.file.url
        except Exception:
            src = None
        if not src and getattr(obj, "url", None):
            src = obj.url
        if src and obj.kind == MediaKind.IMAGE:
            return mark_safe(
                f'<img src="{src}" style="max-height:120px;max-width:200px;border:1px solid #ddd;border-radius:4px;" />'
            )
        return src or ""
    preview.short_description = "Preview"


# --- ActivityRule and ActivityOverride Admins ---------------------------------

@admin.register(ActivityRule)
class ActivityRuleAdmin(admin.ModelAdmin):
    list_display = ("activity", "require_sea", "require_large_lake", "allow_indoor")
    search_fields = ("activity__name", "activity__slug")


@admin.register(ActivityOverride)
class ActivityOverrideAdmin(admin.ModelAdmin):
    list_display = ("activity", "country", "city", "allow")
    list_filter = ("allow", "country")
    search_fields = ("activity__name", "activity__slug", "city__name", "country__name")


@admin.register(PopularDestination)
class PopularDestinationAdmin(admin.ModelAdmin):
    list_display = ("title", "city", "slug", "is_active", "created_at")
    list_filter = ("is_active", "city__country")
    search_fields = ("title", "slug", "city__name", "city__country__name")
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ("created_at", "updated_at")

@admin.register(CityExtra)
class CityExtraAdmin(admin.ModelAdmin):
    list_display = ("city",)
    list_filter = ("city__country",)

class CityActivityImageInline(admin.TabularInline):
    model = CityActivityImage
    extra = 1
    fields = ("file", "position", "preview", "created_at", "updated_at")
    readonly_fields = ("preview", "created_at", "updated_at")
    ordering = ("position",)

    def preview(self, obj):
        if not obj:
            return ""
        src = None
        try:
            if getattr(obj, "file", None) and getattr(obj.file, "url", ""):
                src = obj.file.url
        except Exception:
            src = None
        if src:
            return mark_safe(
                f'<img src="{src}" style="max-height:80px;max-width:140px;border:1px solid #ddd;border-radius:4px;" />'
            )
        return ""
    preview.short_description = "Preview"

@admin.register(CityActivityGallery)
class CityActivityGalleryAdmin(admin.ModelAdmin):
    list_display = ("city_name", "activity")
    list_filter = ("activity", "city_extra__city__country")
    search_fields = ("city_extra__city__name", "activity__name")
    ordering = ("city_extra__city__name", "activity__name")
    inlines = [CityActivityImageInline]

    def city_name(self, obj):
        return obj.city_extra.city.name if obj.city_extra_id else "-"
    city_name.short_description = "City"

@admin.register(SchoolBlog)
class SchoolBlogAdmin(admin.ModelAdmin):
    list_display = ("title", "school", "created_at", "updated_at")
    search_fields = ("title", "school__name")
    list_filter = ("school",)
    readonly_fields = ("created_at", "updated_at")
    prepopulated_fields = {"slug": ("title",)}

@admin.register(SchoolReview)
class SchoolReviewAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'rating', 'comment', 'created_at', 'school')
    list_filter = ("school", "rating")
    search_fields = ("school__name", "user__username", "user_name", "comment")
    readonly_fields = ("created_at",)



# --- Instructor / Freelancer Admins ----------------------------------------

# Inline for InstructorActivity
class InstructorActivityInline(admin.TabularInline):
    model = InstructorActivity
    extra = 1
    fields = ("activity", "experience_years", "is_active", "created_at", "updated_at")
    readonly_fields = ("created_at", "updated_at")

@admin.register(Instructor)
class InstructorAdmin(admin.ModelAdmin):
    list_display = ("user", "city", "country", "age", "gender", "created_at", "updated_at")
    search_fields = ("user__username", "user__first_name", "user__last_name", "city__name", "country__name")
    list_filter = ("country", "city", "gender")
    readonly_fields = ("created_at", "updated_at")
    inlines = [InstructorActivityInline]

class InstructorMediaInline(admin.TabularInline):
    model = InstructorMedia
    extra = 1
    fields = ("kind", "file", "url", "position", "created_at", "updated_at")
    readonly_fields = ("created_at", "updated_at")

@admin.register(InstructorSubscription)
class InstructorSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("instructor", "plan", "status", "starts_at", "ends_at", "created_at", "updated_at")
    list_filter = ("plan", "status")
    search_fields = ("instructor__user__username",)
    readonly_fields = ("created_at", "updated_at")

@admin.register(InstructorReview)
class InstructorReviewAdmin(admin.ModelAdmin):
    list_display = ("instructor", "user_name", "rating", "created_at")
    list_filter = ("instructor", "rating")
    search_fields = ("instructor__user__username", "user__username", "user_name", "comment")
    readonly_fields = ("created_at",)

    def user_name(self, obj):
        return obj.user_name
    user_name.short_description = "User"

# -- Register missing models for full CRUD in admin if not already registered
for model in [SchoolActivitySeason]:
    try:
        admin.site.register(model)
    except admin.sites.AlreadyRegistered:
        pass
# --- Register SchoolActivitySession for admin ---
from .models import SchoolActivitySession

# --- Nuevo formulario para SchoolActivitySession con rango de fechas ---
from datetime import datetime, timedelta
from django import forms
from django.utils.safestring import mark_safe
from .models import SchoolActivitySession

class SchoolActivitySessionForm(forms.ModelForm):
    date_start = forms.DateField(
        required=True,
        label="Start Date",
        widget=forms.DateInput(attrs={"type": "date", "class": "vDateField"}),
        help_text="Start date of the session period.",
    )
    date_end = forms.DateField(
        required=True,
        label="End Date",
        widget=forms.DateInput(attrs={"type": "date", "class": "vDateField"}),
        help_text="End date of the session period.",
    )
    time_slots = forms.CharField(
        required=True,
        label="Time Slots",
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "09:00-11:00\n15:00-17:00"}),
        help_text=mark_safe("Enter one time slot per line.<br>Example:<br>09:00-11:00<br>15:00-17:00"),
    )
    capacity = forms.IntegerField(
        required=True,
        label="Capacity",
        min_value=1,
        help_text="Maximum number of participants.",
    )
    is_available = forms.BooleanField(
        required=False,
        label="Available",
        help_text="Is this session available for booking?",
    )

    class Meta:
        model = SchoolActivitySession
        fields = [
            "school_activity",
            "variant",
            "date_start",
            "date_end",
            "time_slots",
            "capacity",
            "is_available",
            "session_metadata",
        ]

    def save(self, commit=True):
        # Save the single grouped session instance
        instance = super().save(commit=False)
        if commit:
            instance.save()
        return instance


###############################################################################
# SCHOOL ACTIVITY SESSION ADMIN (bulk creation, AJAX, autocompletar, scalable)
###############################################################################

@admin.register(SchoolActivitySession)
class SchoolActivitySessionAdmin(admin.ModelAdmin):
    """
    Admin for SchoolActivitySession (grouped model):
    - Shows and edits grouped session fields.
    - Dynamic AJAX inlines for variants.
    - Progressive autocomplete for 50k+ activities.
    - Designed for external integrations (API, paneles, headless).
    """
    list_display = ("school_activity", "variant", "date_start", "date_end", "capacity", "is_available")
    list_filter = ("is_available",)
    search_fields = ('school_activity__school__name', 'school_activity__activity__name')
    readonly_fields = ("created_at", "updated_at")
    form = SchoolActivitySessionForm
    ordering = ("-date_start",)
    fieldsets = (
        ("General Information", {
            "fields": (
                "school_activity",
                "variant",
                "date_start",
                "date_end",
                "time_slots",
                "capacity",
                "is_available",
            ),
        }),
        ("Metadata", {"fields": ("session_metadata", "created_at", "updated_at")}),
    )

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        # Use scalable querysets for autocomplete (50k+ support)
        if db_field.name == "school_activity":
            kwargs["queryset"] = SchoolActivity.objects.all().order_by("school__name")
        elif db_field.name == "variant":
            kwargs["queryset"] = SchoolActivityVariant.objects.only("id", "name").order_by("name")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    class Media:
        js = (
            "https://cdn.jsdelivr.net/npm/flatpickr/dist/flatpickr.min.js",
            "https://cdn.jsdelivr.net/npm/flatpickr/dist/l10n/es.js",
            "https://code.jquery.com/jquery-3.6.0.min.js",
        )
        css = {
            "all": ("https://cdn.jsdelivr.net/npm/flatpickr/dist/flatpickr.min.css",)
        }
        js_inline = mark_safe("""
        document.addEventListener('DOMContentLoaded', function() {
            const activitySelect = document.getElementById('id_school_activity');
            const variantSelect = document.getElementById('id_variant');
            if (!activitySelect || !variantSelect) return;
            activitySelect.addEventListener('change', function() {
                const selectedId = this.value;
                if (!selectedId) {
                    variantSelect.innerHTML = '<option value="">---------</option>';
                    return;
                }
                variantSelect.innerHTML = '<option value="">Cargando...</option>';
                const url = `/admin/directory/schoolactivitysession/variants-by-activity/?school_activity=${selectedId}`;
                fetch(url)
                    .then(resp => {
                        if (!resp.ok) throw new Error(`HTTP error: ${resp.status}`);
                        return resp.json();
                    })
                    .then(data => {
                        variantSelect.innerHTML = '';
                        if (data.results && data.results.length > 0) {
                            const emptyOpt = document.createElement('option');
                            emptyOpt.value = '';
                            emptyOpt.textContent = '---------';
                            variantSelect.appendChild(emptyOpt);
                            data.results.forEach(item => {
                                const opt = document.createElement('option');
                                opt.value = item.id;
                                opt.textContent = item.name;
                                variantSelect.appendChild(opt);
                            });
                        } else {
                            const noOpt = document.createElement('option');
                            noOpt.value = '';
                            noOpt.textContent = 'No hay variantes activas';
                            variantSelect.appendChild(noOpt);
                        }
                    })
                    .catch(err => {
                        variantSelect.innerHTML = '';
                        const errorOpt = document.createElement('option');
                        errorOpt.value = '';
                        errorOpt.textContent = 'Error al cargar';
                        variantSelect.appendChild(errorOpt);
                    });
            });
            if (activitySelect.value) {
                activitySelect.dispatchEvent(new Event('change'));
            }
        });
        """)

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            re_path(
                r"^variants-by-activity/$",
                self.variants_by_activity,
                name="directory_schoolactivitysession_variants_by_activity",
            ),
        ]
        return custom_urls + urls

    from django.views.decorators.csrf import csrf_exempt
    from django.utils.decorators import method_decorator
    from django.contrib.admin.views.decorators import staff_member_required

    @method_decorator(staff_member_required)
    @csrf_exempt
    def variants_by_activity(self, request):
        from django.http import JsonResponse
        import uuid
        school_activity_param = request.GET.get("school_activity")
        if not school_activity_param:
            return JsonResponse({"results": [], "count": 0, "message": "Falta el parámetro school_activity (ID de la actividad de la escuela)."})
        try:
            school_activity_uuid = uuid.UUID(school_activity_param)
        except (ValueError, AttributeError, TypeError):
            return JsonResponse({
                "results": [],
                "count": 0,
                "message": "El parámetro school_activity no es un UUID válido."
            })
        @lru_cache(maxsize=256)
        def cached_variant_list(school_activity_uuid):
            return list(
                SchoolActivityVariant.objects.filter(
                    school_activity__id=school_activity_uuid,
                    is_active=True
                ).values("id", "name").order_by("name")
            )
        try:
            results = cached_variant_list(school_activity_uuid)
            count = len(results)
            if count == 0:
                return JsonResponse({
                    "results": [],
                    "count": 0,
                    "message": "No se encontraron variantes activas para esta actividad."
                })
            return JsonResponse({
                "results": results,
                "count": count,
                "message": f"Se encontraron {count} variante(s) activa(s)."
            })
        except Exception as e:
            return JsonResponse({
                "results": [],
                "count": 0,
                "message": f"Error al buscar variantes: {str(e)}"
            })


@admin.register(SchoolActivityVariant)
class SchoolActivityVariantAdminSimple(admin.ModelAdmin):
    """
    Admin for SchoolActivityVariant: scalable, variant logic separated, API-ready.
    """
    search_fields = ("name", "school_activity__activity__name", "school_activity__school__name")
    list_display = (
        "name",
        "school_activity",
        "price",
        "offer_type",
        "difficulty",
        "experience_type",
        "is_active",
    )
    list_filter = (
        "offer_type",
        "difficulty",
        "experience_type",
        "is_active",
    )
    ordering = ("school_activity__school__name",)
    autocomplete_fields = ["school_activity"]

    def changelist_view(self, request, extra_context=None):
        """/?format=json returns data for AJAX or external panels (scalable)."""
        if request.GET.get("format") == "json":
            qs = self.get_queryset(request)
            school_activity_id = request.GET.get("school_activity")
            if school_activity_id:
                qs = qs.filter(school_activity_id=school_activity_id)
            data = [{"id": v.id, "name": v.name} for v in qs]
            return JsonResponse({"results": data})
        return super().changelist_view(request, extra_context)
#
# --- USER / BOOKING / PAYMENT ADMIN CONFIGURATIONS -------------------------

from .models import UserProfile, Booking, Payment

from django.core.mail import send_mail
from django.utils import timezone

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "gender", "birth_date", "nationality", "created_at")
    search_fields = ("user__username", "user__email", "nationality")
    list_filter = ("gender", "nationality")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("-created_at",)


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "booking_date", "variant", "status", "created_at")
    search_fields = ("user__username", "variant__name")
    list_filter = ("status", "created_at")
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("user", "variant")
    ordering = ("-created_at",)

    actions = ["mark_as_paid_and_notify_school"]

    def mark_as_paid_and_notify_school(self, request, queryset):
        """
        Custom admin action: Mark selected bookings as payout released and notify school by email.
        """
        sent = 0
        for booking in queryset:
            # Set payout_released
            booking.payout_released = True
            booking.save()

            # Get school and school email
            school = getattr(getattr(booking.variant, "school_activity", None), "school", None)
            school_email = getattr(school, "email", None)
            school_name = getattr(school, "name", "School")
            if school_email:
                subject = f"[PAYMENT CONFIRMATION] Payment sent for booking {booking.id}"
                current_datetime = timezone.now().strftime("%Y-%m-%d %H:%M")
                amount = getattr(booking, "amount", None)
                # fallback for amount
                if amount is None:
                    amount = "-"
                message = (
                    f"Dear {school_name},\n\n"
                    f"We’ve sent your payout for booking ID {booking.id}.\n"
                    f"Amount: €{amount}\n"
                    f"Date: {current_datetime}\n\n"
                    f"Thank you for partnering with The Travel Wild!\n"
                    f"— The Travel Wild Team"
                )
                send_mail(
                    subject,
                    message,
                    None,  # from_email: use settings.DEFAULT_FROM_EMAIL
                    [school_email],
                    fail_silently=False,
                )
                sent += 1
        self.message_user(request, f"{sent} school(s) notified and marked as payout released.", level=messages.SUCCESS)
    mark_as_paid_and_notify_school.short_description = "Mark as paid and notify school"


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("id", "booking", "amount", "currency", "status", "created_at")
    search_fields = ("booking__user__username", "stripe_payment_id")
    list_filter = ("currency", "status")
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("booking",)
    ordering = ("-created_at",)


# --- Custom UserAdmin to hide school users from User admin ---
from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

# Unregister User only if already registered to avoid AlreadyRegistered error
if hasattr(admin.site, "is_registered") and admin.site.is_registered(User):
    admin.site.unregister(User)

@admin.register(User)
class CustomUserAdmin(BaseUserAdmin):
    def get_queryset(self, request):
        # Las escuelas ya no están ligadas a User, devolvemos todos los usuarios normalmente
        return super().get_queryset(request)