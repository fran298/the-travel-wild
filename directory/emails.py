from dataclasses import dataclass
from typing import Optional
from django.core.mail import send_mail, EmailMessage
from django.conf import settings

@dataclass
class SchoolDTO:
    name: str
    email: Optional[str]
    plan: Optional[str] = None
    is_verified: bool = False

def alta_creada(to_email: str, school: SchoolDTO):
    subject = f"[Extreme] Alta creada: {school.name}"
    body = f"Escuela '{school.name}' creada. Plan: {school.plan or '—'}."
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [to_email], fail_silently=False)

def pago_ok(to_email: str, school: SchoolDTO, amount_text: str = ""):
    subject = f"[Extreme] Pago confirmado: {school.name}"
    body = f"Pago confirmado para '{school.name}'. {amount_text}"
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [to_email], fail_silently=False)

def solicitar_verificacion(to_email: str, school: SchoolDTO):
    subject = f"[Extreme] Verificación requerida: {school.name}"
    body = f"Tu escuela '{school.name}' en plan {school.plan} requiere verificación."
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [to_email], fail_silently=False)

def verificacion_resultado(to_email: str, school: SchoolDTO, approved: bool, notes: str = ""):
    subject = f"[Extreme] Verificación {'aprobada' if approved else 'rechazada'}: {school.name}"
    body = f"Resultado: {'aprobada' if approved else 'rechazada'}. {notes}"
    msg = EmailMessage(subject, body, settings.DEFAULT_FROM_EMAIL, [to_email])
    msg.send(fail_silently=False)