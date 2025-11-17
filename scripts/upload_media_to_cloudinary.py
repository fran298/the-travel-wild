import os
from pathlib import Path

import django
import cloudinary
import cloudinary.uploader

# -------------------------
# Django setup
# -------------------------
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "extreme_site.settings")
django.setup()

from django.conf import settings
from directory.models import (
    UserProfile,
    SchoolProfile,
    InstructorProfile,
    Activity,
    School,
    SchoolActivity,
    Media,
    PopularDestination,
    CityExtra,
    CityActivityImage,
    SchoolBlog,
    Instructor,
    InstructorMedia,
)

# -------------------------
# Cloudinary setup
# -------------------------
cloudinary.config(
    cloud_name="dmvlubzor",
    api_key=os.environ.get("CLOUDINARY_API_KEY"),
    api_secret=os.environ.get("CLOUDINARY_API_SECRET"),
    secure=True,
)

BASE_DIR = Path(settings.BASE_DIR)
MEDIA_ROOT = BASE_DIR / "media"


def upload_field(instance, field_name, folder):
    """
    Sube el archivo local a Cloudinary y guarda el public_id en el campo.
    NO toca nada si:
      - el campo está vacío
      - ya contiene una URL (empieza por http)
    """
    field = getattr(instance, field_name, None)
    if not field:
        return False

    name = str(field.name or "").strip()
    if not name:
        return False

    # Si ya parece URL, no tocamos
    if name.startswith("http://") or name.startswith("https://"):
        return False

    local_path = MEDIA_ROOT / name
    if not local_path.exists():
        print(f"  ✗ Fichero no encontrado: {local_path}")
        return False

    print(f"  ⬆ Subiendo {local_path} → carpeta Cloudinary '{folder}'")

    result = cloudinary.uploader.upload(
        str(local_path),
        folder=f"the_travel_wild/{folder}",
        overwrite=False,
        resource_type="image",
    )

    public_id = result["public_id"]
    # Guardamos el public_id (CloudinaryStorage lo sabrá resolver)
    field.name = public_id
    instance.save(update_fields=[field_name])
    print(f"  ✓ Guardado {field_name} para {instance} → {public_id}")
    return True


def migrate_queryset(label, model, field_name, folder):
    print(f"\n======== {label} ({model.__name__}.{field_name}) ========")
    total = model.objects.count()
    print(f"Total objetos: {total}")
    changed = 0
    for obj in model.objects.all():
        try:
            if upload_field(obj, field_name, folder):
                changed += 1
        except Exception as e:
            print(f"  ⚠ Error con {obj} → {e}")
    print(f"✔ {label}: {changed} archivos subidos/actualizados.\n")


def run():
    # Usuarios
    migrate_queryset("UserProfile profile_image", UserProfile, "profile_image", "users/profiles")

    # Escuelas
    migrate_queryset("SchoolProfile logo", SchoolProfile, "logo", "schools/logos")
    migrate_queryset("School logo", School, "logo", "schools/logos")
    migrate_queryset("School cover_image", School, "cover_image", "schools/covers")

    # Instructores
    migrate_queryset("InstructorProfile profile_image", InstructorProfile, "profile_image", "instructors/profiles")
    migrate_queryset("Instructor profile_image", Instructor, "profile_image", "instructors/profiles")
    migrate_queryset("Instructor cover_image", Instructor, "cover_image", "instructors/covers")
    migrate_queryset("InstructorMedia file", InstructorMedia, "file", "instructors/media")

    # Actividades y actividades por escuela
    migrate_queryset("Activity image", Activity, "image", "activities")
    migrate_queryset("SchoolActivity activity_profile_image", SchoolActivity, "activity_profile_image", "schools/activities")

    # Media general
    migrate_queryset("Media file", Media, "file", "media")

    # Destinos populares
    migrate_queryset("PopularDestination image_card", PopularDestination, "image_card", "popular/card")
    migrate_queryset("PopularDestination image_hero", PopularDestination, "image_hero", "popular/hero")

    # Ciudades
    migrate_queryset("CityExtra image_hero", CityExtra, "image_hero", "cities/hero")
    migrate_queryset("CityExtra image_square", CityExtra, "image_square", "cities/square")
    migrate_queryset("CityActivityImage file", CityActivityImage, "file", "cities/gallery")

    # Blogs de escuelas
    migrate_queryset("SchoolBlog cover_image", SchoolBlog, "cover_image", "blogs")

    print("\n🎉 Migración COMPLETA de media a Cloudinary.\n")


print("\n=== Iniciando migración de media a Cloudinary ===\n")

# Permite ejecución tanto directa como usando:
#   python manage.py shell < script.py
try:
    run()
except Exception as e:
    print("\n❌ ERROR ejecutando la migración:", e)