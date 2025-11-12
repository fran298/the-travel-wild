from django.core.mail import EmailMessage, BadHeaderError
from django.conf import settings
import logging

logger = logging.getLogger("django.core.mail")

def _format_booking_date(booking):
    """
    Returns a YYYY-MM-DD string using (in order): booking.session_date,
    variant.date_start, or 'To be arranged' if none available.
    """
    d = getattr(booking, "session_date", None)
    if not d:
        variant = getattr(booking, "variant", None)
        if variant is not None:
            d = getattr(variant, "date_start", None)
    return d.strftime("%Y-%m-%d") if d else "To be arranged"

def send_booking_emails(booking):
    """
    Sends professional booking confirmation emails automatically
    to both the user and the school.
    During development, these emails are printed in the console.
    """
    try:
        # Force load all related objects to ensure they are available
        from directory.models import Booking
        booking = Booking.objects.select_related(
            "variant__school_activity__activity",
            "user",
            "school",
        ).get(id=booking.id)

        # ===== User Email =====
        user_subject = "✅ Booking Confirmation - The Travel Wild"
        user_message = (
            f"Hi {booking.user.first_name or booking.user.username},\n\n"
            f"Your booking for '{booking.variant.school_activity.activity.name}' has been successfully confirmed!\n\n"
            f"Details:\n"
            f"• School: {booking.school.name}\n"
            f"• Date: {_format_booking_date(booking)}\n"
            f"• Participants: {getattr(booking, 'participants', 1)}\n"
            f"• Total Paid: €{booking.amount}\n\n"
            f"\nOur partner school will contact you shortly to confirm the schedule and provide final details about your experience.\n\n"
            f"Thank you for choosing The Travel Wild!\n"
            f"We wish you an incredible experience 🌍\n\n"
            f"— The Travel Wild Team"
        )

        # Send user email (using EmailMessage to support Reply-To)
        user_email = EmailMessage(
            subject=user_subject,
            body=user_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[booking.user.email],
            reply_to=[getattr(settings, "DEFAULT_REPLY_TO", settings.DEFAULT_FROM_EMAIL)],
        )
        user_email.send(fail_silently=False)

        # ===== School Email =====
        school_subject = "📩 New Booking Received - The Travel Wild"
        school_message = (
            f"Hello {booking.school.name},\n\n"
            f"You have received a new booking through The Travel Wild platform.\n\n"
            f"Details:\n"
            f"• Activity: {booking.variant.school_activity.activity.name}\n"
            f"• Date: {_format_booking_date(booking)}\n"
            f"• Participants: {getattr(booking, 'participants', 1)}\n"
            f"• Amount Paid: €{booking.amount}\n"
            f"• Customer Email: {booking.user.email}\n"
            f"• Customer Phone: {getattr(booking.user, 'phone', 'Not provided')}\n\n"
            f"Please contact the customer directly to confirm time or other details.\n"
            f"Log in to your school dashboard to view the full booking information.\n\n"
            f"— The Travel Wild Team 🌍"
        )

        # Send school email (using EmailMessage to support Reply-To)
        school_email = EmailMessage(
            subject=school_subject,
            body=school_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[booking.school.email],
            reply_to=[getattr(settings, "DEFAULT_REPLY_TO", settings.DEFAULT_FROM_EMAIL)],
        )
        school_email.send(fail_silently=False)

        logger.info(f"✅ Booking emails sent successfully for booking ID {booking.id}")

    except BadHeaderError:
        logger.error(f"❌ Invalid header found while sending booking emails for booking {booking.id}.")
    except Exception as e:
        logger.error(f"❌ Error sending booking emails for booking {booking.id}: {e}")


# --- Payout Notification ---
def send_payout_notification(booking):
    """
    Sends an internal notification email to the admin when a booking
    is marked as completed, signaling that a payout should be reviewed.
    Ensures the email is sent only once per booking, and warns on retries.
    """
    try:
        # ✅ Prevent duplicate sends with a dedicated flag
        if getattr(booking, "email_payout_sent", False):
            logger.warning(f"⚠️ Payout email already sent for booking {booking.id}. Payment is in process.")
            return {
                "ok": False,
                "message": "⚠️ Payment process already initiated. Notification cannot be sent again."
            }

        # Try to retrieve commission and net amount
        try:
            from directory.models import SchoolTransaction
            transaction = SchoolTransaction.objects.filter(booking=booking).last()
        except Exception:
            transaction = None

        if transaction:
            commission_rate = (
                getattr(transaction, "commission_rate", None)
                or getattr(transaction, "commission", None)
                or getattr(transaction, "commission_percentage", None)
                or getattr(transaction, "commission_fee", None)
            )
            net_amount = getattr(transaction, "net_amount", None)
        else:
            commission_rate = getattr(booking, "commission_rate", None)
            net_amount = getattr(booking, "net_amount", None)

        if commission_rate is None:
            try:
                finance_obj = getattr(booking.school, "finance", None)
                if finance_obj is None and hasattr(booking.school, "ensure_finance"):
                    finance_obj = booking.school.ensure_finance()

                plan = getattr(finance_obj, "plan", None)
                if plan and str(plan).lower() == "premium":
                    commission_rate = 20
                else:
                    commission_rate = 25
            except Exception:
                commission_rate = 25

        if net_amount is None and booking.amount:
            from decimal import Decimal
            net_amount = (booking.amount * (Decimal(1) - Decimal(commission_rate) / Decimal(100))).quantize(Decimal("0.01"))

        commission_rate_display = f"{commission_rate}%" if commission_rate is not None else "N/A"
        net_amount_display = f"€{net_amount}" if net_amount is not None else "N/A"

        subject = f"💸 Payout Pending - Booking #{booking.id}"
        message = (
            f"A booking has been marked as COMPLETED and may be ready for payout.\n\n"
            f"Booking Details:\n"
            f"• ID: {booking.id}\n"
            f"• School: {booking.school.name} ({booking.school.email})\n"
            f"• Activity: {booking.variant.school_activity.activity.name}\n"
            f"• Date: {_format_booking_date(booking)}\n"
            f"• Traveler: {booking.user.get_full_name()} ({booking.user.email})\n"
            f"• Total Paid: €{booking.amount}\n"
            f"• Commission: {commission_rate_display}\n"
            f"• Net to School: {net_amount_display}\n\n"
            f"Please review and release payment accordingly.\n\n"
            f"— The Travel Wild System"
        )

        admin_email = getattr(settings, "FINANCE_TEAM_EMAIL", settings.DEFAULT_FROM_EMAIL)
        email = EmailMessage(
            subject=subject,
            body=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[admin_email],
        )
        email.send(fail_silently=False)

        # ✅ Mark as sent
        booking.email_payout_sent = True
        booking.save(update_fields=["email_payout_sent"])

        logger.info(f"✅ Payout notification sent for booking ID {booking.id}")
        return {"ok": True, "message": "✅ Notification sent. Payment process initiated."}

    except Exception as e:
        logger.error(f"❌ Error sending payout notification for booking {booking.id}: {e}")
        return {"ok": False, "message": f"❌ Error sending payout notification: {e}"}