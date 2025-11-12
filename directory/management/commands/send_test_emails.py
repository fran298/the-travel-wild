from django.core.management.base import BaseCommand
from directory.emails import alta_creada, pago_ok, solicitar_verificacion, verificacion_resultado, SchoolDTO

class Command(BaseCommand):
    help = "Envía emails de prueba al correo indicado"

    def add_arguments(self, parser):
        parser.add_argument('to_email', type=str)

    def handle(self, *args, **opts):
        to = opts['to_email']
        school = SchoolDTO(name="Escuela Demo", email=to, plan="basic")

        alta_creada(to, school)
        pago_ok(to, school, amount_text="€99.00")
        solicitar_verificacion(to, school)
        verificacion_resultado(to, school, approved=True)
        verificacion_resultado(to, school, approved=False, notes="Falta documentación.")
        self.stdout.write(self.style.SUCCESS("Emails de prueba enviados"))