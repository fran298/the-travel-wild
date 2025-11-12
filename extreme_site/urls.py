from django.contrib import admin
admin.site.login_template = 'admin/login.html'
admin.site.session_cookie_name = 'admin_sessionid'
from django.urls import path, include
from directory import views
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.home, name='home'),

    # Listado de escuelas por país, ciudad o deporte
    path('schools/<slug:country_slug>/<slug:city_slug>/<slug:school_slug>/', views.school_detail, name='school_detail'),
    path('schools/<slug:country_slug>/<slug:city_slug>/<slug:activity_slug>/', views.schools_by_sport_and_city, name='schools_by_sport_and_city'),
    path('schools/<slug:country_slug>/<slug:city_slug>/', views.schools_by_city, name='schools_by_city'),
    path('schools/<slug:country_slug>/', views.country_view, name='country_view'),

    path('schools/<slug:country_slug>/<slug:city_slug>/<slug:school_slug>/create-payment-intent/', views.create_payment_intent, name='create_payment_intent'),
    path('schools/<slug:slug>/add-review/', views.add_review, name='add_review'),
    path('pricing/', views.pricing, name='pricing'),
    path('search/', views.search_redirect, name='search'),

    # Flujo de suscripción Premium (Stripe Billing)
    path('pricing/checkout/success/', views.premium_success_view, name='premium_success'),
    path('pricing/checkout/cancel/', views.premium_cancel_view, name='premium_cancel'),
    path('destinations/', views.destinations_view, name='destinations'),
    path('activities/<slug:sport_slug>/', views.sport_detail, name='sport_detail'),

    # Booking flow (user initiates a booking)
    path('book/<uuid:session_id>/', views.start_booking, name='start_booking'),


    # Registro y autenticación de escuelas
    path('signup/', views.signup_selector, name='signup_selector'),
    path('signup/basic/', views.signup_basic, name='signup_basic'),
    path('signup/instructor/', views.instructor_signup_basic, name='instructor_signup_basic'),

    # Flujo de registro para escuelas: rutas organizadas bajo 'signup/school/'
    path('signup/school/', views.school_signup_basic, name='school_signup_basic'),
    path('signup/school/verify/', views.verify_email_code, name='verify_email_code'),
    path('signup/school/resend-code/', views.resend_verification_code, name='resend_verification_code'),
    path('signup/school/complete-profile/', views.school_profile_completion_view, name='school_profile_completion_view'),
    path('school/select-activities/', views.school_select_activities_view, name='school_select_activities_view'),
    path('school/setup/activities/', views.school_setup_activities_view, name='school_setup_activities_view'),
    path('school/dashboard/', views.school_dashboard_view, name='school_dashboard_view'),
    path('school/finance/connect/', views.connect_stripe_account_view, name='connect_stripe_account_view'),
    path('school/finance/refresh/', views.refresh_stripe_link_view, name='refresh_stripe_link_view'),
    path('stripe/onboarding/complete/', views.onboarding_complete_view, name='onboarding_complete_view'),
    path('school/finance/', views.school_finance, name='school_finance'),
    path('school/finance/connect/', views.connect_stripe_account_view, name='school_finance_connect'),
    path('school/finance/refresh/', views.refresh_stripe_link_view, name='school_finance_refresh'),
    path('school/bookings/', views.school_bookings_view, name='school_bookings_view'),
    path('school/bookings/update/<int:booking_id>/', views.update_booking_status, name='update_booking_status'),

    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('account/', views.account_profile, name='account_profile'),

    # Password reset flow
    path('password-reset/', views.CustomPasswordResetView.as_view(), name='password_reset'),
    path('password-reset/done/', views.CustomPasswordResetDoneView.as_view(), name='password_reset_done'),
    path('reset/<uidb64>/<token>/', views.CustomPasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    path('reset/done/', views.CustomPasswordResetCompleteView.as_view(), name='password_reset_complete'),

    path('cities/<slug:country_slug>/<slug:city_slug>/extra/', views.city_extra_detail, name='city_extra_detail'),

    # Autocomplete endpoints (DAL)
    path('country-autocomplete/', views.CountryAutocomplete.as_view(), name='country-autocomplete'),
    path('city-autocomplete/', views.CityAutocomplete.as_view(), name='city-autocomplete'),

    path("checkout/<int:booking_id>/", views.checkout_page, name="checkout_page"),

    path("api/variant/<uuid:variant_id>/sessions/", views.variant_sessions_api, name="variant_sessions_api"),
    path("api/booking/<int:booking_id>/mark-paid/", views.booking_mark_paid, name="booking_mark_paid"),

    path("payments/confirm/", views.confirm_payment, name="confirm_payment"),

    path("directory/stripe_webhook/", views.stripe_webhook, name="stripe_webhook_directory"),
    path("stripe_webhook/", views.stripe_webhook, name="stripe_webhook"),

    path('terms/', views.terms_view, name='terms'),
    path('privacy/', views.privacy_view, name='privacy'),
    path('cookies/', views.cookies_view, name='cookies'),
]


if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
