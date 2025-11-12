import stripe
from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.db import connection, transaction

# Configurar clave secreta de Stripe desde settings
stripe.api_key = settings.STRIPE_SECRET_KEY

# Mapa plan -> price_id (desde .env)
PLAN_TO_PRICE = {
    "basic": settings.STRIPE_PRICE_BASIC,
    "medium": settings.STRIPE_PRICE_MEDIUM,
    "premium": settings.STRIPE_PRICE_PREMIUM,
}


def _school_id_from_request(request):
    """MVP: obtenemos school_id por querystring ?school_id=UUID"""
    return request.GET.get("school_id")


def checkout(request, plan: str):
    """Crea una sesión de Checkout para el plan indicado y redirige a Stripe."""
    plan = (plan or "").lower()
    if plan not in PLAN_TO_PRICE or not PLAN_TO_PRICE[plan]:
        return HttpResponseBadRequest("Plan inválido o sin PRICE_ID configurado")

    school_id = _school_id_from_request(request)
    if not school_id:
        return HttpResponseBadRequest("Falta school_id en la URL (?school_id=UUID)")

    price_id = PLAN_TO_PRICE[plan]

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            subscription_data={
                "metadata": {"school_id": school_id, "plan": plan}
            },
            success_url=request.build_absolute_uri(reverse("billing_success")) + "?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=request.build_absolute_uri(reverse("billing_cancel")),
            metadata={"school_id": school_id, "plan": plan},
        )
    except Exception as e:
        return HttpResponseBadRequest(f"Stripe error: {e}")

    return redirect(session.url)


def success(request):
    return render(request, "directory/billing_success.html")


def cancel(request):
    return render(request, "directory/billing_cancel.html")


# --- Helpers internos ---

def _map_status(stripe_status: str) -> str:
    """Mapea estado de Stripe -> ENUM subscription_status de nuestra BD."""
    if stripe_status in ("active", "trialing"):
        return "active"
    if stripe_status in ("past_due", "unpaid", "incomplete"):
        return "past_due"
    if stripe_status in ("canceled", "incomplete_expired"):
        return "canceled"
    return "pending"


def _upsert_subscription(school_id, plan, status, starts_at, ends_at, customer_id, subscription_id):
    """Crea/actualiza la fila en subscription y ajusta school.status según reglas de publicación."""
    with connection.cursor() as cur:
        # Inserta o actualiza por stripe_subscription_id (idempotente)
        cur.execute(
            """
            INSERT INTO subscription (school_id, plan, status, starts_at, ends_at, stripe_customer_id, stripe_subscription_id)
            VALUES (%s, %s, %s, to_timestamp(%s), to_timestamp(%s), %s, %s)
            ON CONFLICT (stripe_subscription_id) DO UPDATE
            SET status=EXCLUDED.status,
                starts_at=EXCLUDED.starts_at,
                ends_at=EXCLUDED.ends_at,
                stripe_customer_id=EXCLUDED.stripe_customer_id,
                plan=EXCLUDED.plan;
            """,
            [school_id, plan, status, starts_at, ends_at, customer_id, subscription_id],
        )

        # Aplicar regla mínima de publicación
        # - Basic: puede quedar active aunque no esté verificada
        # - Medium/Premium: si no está verificada -> pending; si verificada -> active
        if status == "active":
            cur.execute(
                """
                UPDATE school
                SET status = CASE
                    WHEN (%s IN ('medium','premium') AND (is_verified IS NOT TRUE)) THEN 'pending'
                    ELSE 'active'
                END,
                    updated_at = now()
                WHERE id = %s;
                """,
                [plan, school_id],
            )


# --- Webhook ---

@csrf_exempt
def webhook(request):
    # --- Verificación de firma y parseo seguro ---
    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE")

    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=sig_header,
            secret=settings.STRIPE_WEBHOOK_SECRET,
        )
    except Exception as e:
        return HttpResponseBadRequest(f"Invalid payload/signature: {e}")

    evt_type = event.get("type")
    obj = event.get("data", {}).get("object", {})

    # 1) Evitar 500: no hacemos lógica en checkout.session.completed (dejamos 200 OK)
    if evt_type == "checkout.session.completed":
        return HttpResponse(status=200)

    # 2) Lógica principal en customer.subscription.created / updated
    if evt_type in ("customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"):
        sub = obj
        meta = sub.get("metadata") or {}
        school_id = meta.get("school_id")
        plan = (meta.get("plan") or "").lower()
        stripe_sub_id = sub.get("id")
        stripe_cus_id = sub.get("customer")
        period_start = sub.get("current_period_start") or 0
        period_end = sub.get("current_period_end") or 0
        # Forzamos duración anual en la BD (MVP)

        # Si "plan" no vino en metadata, deducirlo por el price_id usando settings
        price_id = None
        try:
            price_id = sub.get("items", {}).get("data", [{}])[0].get("price", {}).get("id")
        except Exception:
            price_id = None

        if not plan:
            try:
                if price_id == settings.STRIPE_PRICE_PREMIUM:
                    plan = "premium"
                elif price_id == settings.STRIPE_PRICE_MEDIUM:
                    plan = "medium"
                elif price_id == settings.STRIPE_PRICE_BASIC:
                    plan = "basic"
                else:
                    # fallback a PLAN_TO_PRICE si existe coincidencia
                    for k, v in PLAN_TO_PRICE.items():
                        if v == price_id:
                            plan = k
                            break
            except Exception:
                pass

        plan = (plan or "basic").lower()

        # Log informativo para depurar en consola (no interrumpe el flujo)
        try:
            print(f"[stripe-webhook] evt={evt_type} sub={stripe_sub_id} school_id={school_id} plan={plan} price_id={price_id} status={sub.get('status')}")
        except Exception:
            pass

        # Mapear estado Stripe -> enum local
        st = (sub.get("status") or "").lower()
        status_map = {
            "active": "active",
            "trialing": "active",
            "past_due": "past_due",
            "unpaid": "past_due",
            "canceled": "canceled" if evt_type == "customer.subscription.deleted" else "pending",
            "incomplete": "pending",
            "incomplete_expired": "pending",
        }
        local_status = status_map.get(st, "pending")

        # Si no tenemos school_id o stripe_sub_id, no podemos asociar; devolvemos 200 para no reintentar infinito
        if not school_id or not stripe_sub_id:
            return HttpResponse(status=200)

        try:
            with transaction.atomic():
                with connection.cursor() as cur:
                    # Cancelar cualquier suscripción activa previa de la misma escuela (que no sea esta)
                    cur.execute(
                        """
                        UPDATE subscription
                        SET status='canceled'
                        WHERE school_id=%s::uuid
                          AND status='active'
                          AND (stripe_subscription_id IS DISTINCT FROM %s)
                        """,
                        [school_id, stripe_sub_id],
                    )

                    # Guard clause: si local_status es "canceled" pero evento no es deleted, no actualizar estado a canceled
                    if local_status == "canceled" and evt_type != "customer.subscription.deleted":
                        # Obtener estado previo para mantenerlo
                        cur.execute(
                            """
                            SELECT status FROM subscription WHERE stripe_subscription_id = %s
                            """,
                            [stripe_sub_id],
                        )
                        row = cur.fetchone()
                        if row:
                            local_status = row[0]
                        else:
                            # Si no existe, mantener "pending" para no bloquear
                            local_status = "pending"

                    # Upsert por stripe_subscription_id
                    cur.execute(
                        """
                        INSERT INTO subscription (
                            school_id, plan, status, starts_at, ends_at,
                            stripe_customer_id, stripe_subscription_id
                        )
                        VALUES (
                            %s::uuid, %s, %s, to_timestamp(%s), to_timestamp(%s) + interval '1 year', %s, %s
                        )
                        ON CONFLICT (stripe_subscription_id)
                        DO UPDATE SET
                            plan=EXCLUDED.plan,
                            status=EXCLUDED.status,
                            starts_at=EXCLUDED.starts_at,
                            ends_at=EXCLUDED.ends_at,
                            stripe_customer_id=EXCLUDED.stripe_customer_id
                        """,
                        [
                            school_id,
                            plan,
                            local_status,
                            period_start,
                            period_start,
                            stripe_cus_id,
                            stripe_sub_id,
                        ],
                    )

                    # Actualizar estado de la escuela según reglas del MVP
                    if plan in ("medium", "premium"):
                        cur.execute(
                            """
                            UPDATE school
                            SET status = CASE WHEN is_verified THEN 'active' ELSE 'pending' END,
                                updated_at = now()
                            WHERE id=%s::uuid
                            """,
                            [school_id],
                        )
                    else:  # basic
                        cur.execute(
                            "UPDATE school SET status='active', updated_at=now() WHERE id=%s::uuid",
                            [school_id],
                        )
        except Exception:
            # No bloqueamos Stripe con 500: devolvemos 200 para no reintentar infinitamente
            return HttpResponse(status=200)

        return HttpResponse(status=200)

    # 3) Otros eventos: OK sin acción
    return HttpResponse(status=200)