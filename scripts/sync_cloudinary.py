import os
import django
import cloudinary.api
import cloudinary.uploader

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "extreme_site.settings")
django.setup()

from directory.models import (
    Activity, School, CityExtra, Instructor,
    PopularDestination, SchoolBlog, CityActivityGallery, CityActivityImage
)

CLOUD_FOLDER = "the_travel_wild"


def list_files(folder):
    """Return the public_id and URL of files inside a folder, alphabetically sorted."""
    try:
        resources = cloudinary.api.resources(
            type="upload",
            prefix=f"{CLOUD_FOLDER}/{folder}/",
            max_results=500
        )["resources"]

        resources_sorted = sorted(
            resources,
            key=lambda x: x["public_id"]
        )

        return [
            (r["public_id"], r["secure_url"])
            for r in resources_sorted
        ]

    except Exception as e:
        print(f"Error reading {folder}: {e}")
        return []


def assign_one_to_one(model_objects, images, field_names):
    """
    Assign one or more image URLs to each object in order.
    Example:
      field_names = ["logo", "cover_image"]
    """
    for obj, image_group in zip(model_objects, chunks(images, len(field_names))):
        for field, (_, url) in zip(field_names, image_group):
            setattr(obj, field, url)
        obj.save()
        print(f"Updated {model_objects.model.__name__}: {obj}")


def chunks(lst, n):
    """Yield n-sized chunks."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


print("\n===============================\nSYNC START\n===============================\n")

# ------------------------------------
# 1. ACTIVITIES
# ------------------------------------
activity_images = list_files("activities")
activities = Activity.objects.order_by("name")

if activity_images:
    for activity, (_, url) in zip(activities, activity_images):
        activity.image = url
        activity.save()
        print(f"[Activity] {activity.name} → {url}")


# ------------------------------------
# 2. SCHOOLS (logo + cover)
# ------------------------------------
school_images = list_files("schools")
schools = School.objects.order_by("name")

if school_images:
    assign_one_to_one(schools, school_images, ["logo", "cover_image"])


# ------------------------------------
# 3. CITIES
# ------------------------------------
city_images = list_files("cities")
cities = CityExtra.objects.order_by("city__name")

if city_images:
    assign_one_to_one(cities, city_images, ["image_hero", "image_square"])


# ------------------------------------
# 4. INSTRUCTORS
# ------------------------------------
instr_images = list_files("instructors")
instructors = Instructor.objects.order_by("user__username")

if instr_images:
    assign_one_to_one(instructors, instr_images, ["profile_image", "cover_image"])


# ------------------------------------
# 5. POPULAR DESTINATIONS
# ------------------------------------
popular_images = list_files("popular")
popular = PopularDestination.objects.order_by("title")

if popular_images:
    assign_one_to_one(popular, popular_images, ["image_card", "image_hero"])


# ------------------------------------
# 6. BLOGS
# ------------------------------------
blog_images = list_files("blogs")
blogs = SchoolBlog.objects.order_by("title")

if blog_images:
    for blog, (_, url) in zip(blogs, blog_images):
        blog.cover_image = url
        blog.save()
        print(f"[Blog] {blog.title} → {url}")


print("\n===============================\nSYNC COMPLETE\n===============================\n")