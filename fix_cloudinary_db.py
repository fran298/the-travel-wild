import os
import django
from cloudinary.api import resource
from cloudinary.exceptions import NotFound
from django.core.exceptions import ObjectDoesNotExist

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "extreme_site.settings")
django.setup()

import cloudinary
cloudinary.config(
    cloud_name="dmvlubzor",
    api_key=os.environ.get("CLOUDINARY_API_KEY"),
    api_secret=os.environ.get("CLOUDINARY_API_SECRET"),
    secure=True,
)

from directory.models import Activity, SchoolActivity, CityActivityImage, School, PopularDestination


def fix_model(model, field_name, folder):
    print(f"🔍 Fixing model: {model.__name__}")

    items = model.objects.all()

    for item in items:
        image_field = getattr(item, field_name, None)

        if not image_field:
            continue

        old_name = image_field.name  # e.g. the_travel_wild/activities/xxyyzz123

        if not old_name:
            continue

        # Extract the last segment (filename)
        filename = old_name.split("/")[-1]

        # If filename already has an extension, skip
        if "." in filename:
            print(f"✔ Already has extension, skipping: {old_name}")
            continue

        public_id = old_name  # Cloudinary public_id WITHOUT extension

        # Try to retrieve the resource from Cloudinary
        try:
            cloud_resource = resource(public_id)
        except NotFound:
            print(f"⚠️ Not found in Cloudinary: {public_id}")
            continue

        # Get the real extension from Cloudinary
        file_format = cloud_resource.get("format")

        if not file_format:
            print(f"⚠️ Missing format in Cloudinary for: {public_id}")
            continue

        # Build corrected name with extension
        correct_public_id = f"{public_id}.{file_format}"

        # Save corrected name to DB
        image_field.name = correct_public_id
        item.save()

        print(f"✔ Fixed: {old_name} → {correct_public_id}")


# Run fixes for all models (correct image fields)

# Activity: image
fix_model(Activity, "image", None)

# SchoolActivity: activity_profile_image
fix_model(SchoolActivity, "activity_profile_image", None)

# CityActivityImage: file
fix_model(CityActivityImage, "file", None)

# School: logo and cover_image
fix_model(School, "logo", None)
fix_model(School, "cover_image", None)

# PopularDestination: image_card and image_hero
fix_model(PopularDestination, "image_card", None)
fix_model(PopularDestination, "image_hero", None)

print("🎉 DONE — All image names synced with Cloudinary")