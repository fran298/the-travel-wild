from directory.models import PopularDestination

def run():
    for dest in PopularDestination.objects.all():
        # Card
        if dest.image_card and not str(dest.image_card).startswith("http"):
            print("Fixing CARD →", dest.slug)
            dest.image_card = ""
            dest.save()

        # Hero
        if dest.image_hero and not str(dest.image_hero).startswith("http"):
            print("Fixing HERO →", dest.slug)
            dest.image_hero = ""
            dest.save()