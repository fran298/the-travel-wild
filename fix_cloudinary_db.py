import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "extreme_site.settings")
django.setup()

from directory.models import (
    Activity,
    SchoolActivity,
    CityActivityImage,
    School,
    PopularDestination,
    Instructor,
    InstructorProfile,
    InstructorMedia,
    SchoolBlog,
)

def clean_public_id(model, field_name):
    print(f"\n======== FIXING {model.__name__}.{field_name} ========")

    items = model.objects.all()
    print(f"Total objetos: {items.count()}")

    fixed = 0

    for obj in items:
        field = getattr(obj, field_name, None)

        if not field:
            continue

        if not field.name:
            continue

        name = field.name  # Ej: the_travel_wild/activities/abc123.png

        # Chequear si tiene extensión
        if name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):

            # Remover extensión
            new_name = (
                name.replace(".png", "")
                    .replace(".jpg", "")
                    .replace(".jpeg", "")
                    .replace(".webp", "")
            )

            field.name = new_name
            obj.save(update_fields=[field_name])

            print(f"✔ FIXED: {name} → {new_name}")
            fixed += 1

        else:
            print(f"✔ OK (sin extensión): {name}")

    print(f"✔ TOTAL CORREGIDOS: {fixed}")


# Ejecutar para todos los modelos
clean_public_id(Activity, "image")
clean_public_id(SchoolActivity, "activity_profile_image")
clean_public_id(CityActivityImage, "file")
clean_public_id(School, "logo")
clean_public_id(School, "cover_image")
clean_public_id(PopularDestination, "image_card")
clean_public_id(PopularDestination, "image_hero")
clean_public_id(SchoolBlog, "cover_image")
clean_public_id(Instructor, "profile_image")
clean_public_id(Instructor, "cover_image")
clean_public_id(InstructorProfile, "profile_image")
clean_public_id(InstructorMedia, "file")

print("\n🎉 DONE — Todos los public_id fueron limpiados correctamente.\n")