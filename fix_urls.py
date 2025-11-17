from directory.models import Activity

fields = ["image", "image_card", "card_image", "photo"]

for a in Activity.objects.all():
    updated = False

    for f in fields:
        field = getattr(a, f, None)
        if not field:
            continue

        name = field.name or ""
        if name.startswith("https:/") and not name.startswith("https://"):
            field.name = name.replace("https:/", "https://")
            updated = True

    if updated:
        a.save()

print("DONE: URLs fixed.")