from django.contrib.auth.forms import UserCreationForm
from .models import School, InstructorProfile, UserProfile
from django.contrib.auth.models import User
from django import forms
from dal import autocomplete
from .models import Country, City

class SchoolActivityOfferAdminForm(forms.ModelForm):
    """
    Unified admin form for SchoolActivityOffer.
    This form is used both in inline (SchoolActivity) and in direct admin views.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Common fields: title, price, description, includes
        self.fields['title'].widget = forms.TextInput(attrs={"placeholder": "Enter offer title"})
        self.fields['title'].label = "Offer Title"
        self.fields['title'].required = True

        self.fields['price'].widget = forms.NumberInput(attrs={"step": "0.01", "placeholder": "Price in €"})
        self.fields['price'].label = "Price (€)"
        self.fields['price'].required = True
        self.fields['price'].help_text = "Set the price for this offer."

        self.fields['description'].widget = forms.Textarea(attrs={"rows": 3, "placeholder": "Brief description of the offer"})
        self.fields['description'].label = "Description"
        self.fields['description'].required = True

        self.fields['includes'].widget = forms.Textarea(attrs={"rows": 2, "placeholder": "What’s included in this offer?"})
        self.fields['includes'].label = "Includes"
        self.fields['includes'].required = True

        offer_type = None
        # Determine offer_type from instance or initial data
        if self.instance and self.instance.pk:
            offer_type = getattr(self.instance, 'offer_type', None)
        elif 'offer_type' in self.initial:
            offer_type = self.initial.get('offer_type')
        elif 'offer_type' in self.data:
            offer_type = self.data.get('offer_type')

        # Adjust fields based on offer_type
        if offer_type in ['lesson', 'course']:
            # hours
            if 'hours' in self.fields:
                self.fields['hours'].label = "Number of Hours"
                self.fields['hours'].widget.attrs.update({
                    "placeholder": "Total hours for this offer"
                })
                self.fields['hours'].help_text = "Specify how many hours this lesson or course lasts."
                self.fields['hours'].required = True

            # students
            if 'students' in self.fields:
                self.fields['students'].label = "Number of Students"
                self.fields['students'].widget.attrs.update({
                    "placeholder": "Max number of students"
                })
                self.fields['students'].help_text = "Maximum number of students allowed."
                self.fields['students'].required = True

            # instructors
            if 'instructors' in self.fields:
                self.fields['instructors'].label = "Instructors"
                self.fields['instructors'].widget.attrs.update({
                    "placeholder": "List of instructors"
                })
                self.fields['instructors'].help_text = "Instructors involved in this offer."
                self.fields['instructors'].required = True

        elif offer_type == 'experience':
            # hours
            if 'hours' in self.fields:
                self.fields['hours'].label = "Duration (hours)"
                self.fields['hours'].widget.attrs.update({
                    "placeholder": "Duration of the experience in hours"
                })
                self.fields['hours'].help_text = "How long does this experience last?"
                self.fields['hours'].required = True

            # students
            if 'students' in self.fields:
                self.fields['students'].label = "Participants"
                self.fields['students'].widget.attrs.update({
                    "placeholder": "Max number of participants"
                })
                self.fields['students'].help_text = "Maximum participants for the experience."
                self.fields['students'].required = True

            # instructors
            if 'instructors' in self.fields:
                self.fields['instructors'].label = "Guides/Instructors"
                self.fields['instructors'].widget.attrs.update({
                    "placeholder": "Names of guides or instructors"
                })
                self.fields['instructors'].help_text = "People leading this experience."
                self.fields['instructors'].required = True

        else:
            # For other offer types, keep hours, students, instructors but with generic labels and not required
            for field_name, label, placeholder in [
                ('hours', "Hours", "Specify hours"),
                ('students', "Students", "Specify number of students"),
                ('instructors', "Instructors", "Specify instructors"),
            ]:
                if field_name in self.fields:
                    self.fields[field_name].label = label
                    self.fields[field_name].widget.attrs.update({"placeholder": placeholder})
                    self.fields[field_name].required = False

    class Media:
        js = ("admin/js/offers_dynamic.js",)  # optional JS for dynamic fields


# ------------------------ Custom User Creation Form ------------------------
class UserRegistrationForm(UserCreationForm):
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={"placeholder": "Enter your email"}),
        label="Email Address",
    )

    class Meta:
        model = User
        fields = ("email", "password1", "password2")
        widgets = {
            "email": forms.EmailInput(attrs={"placeholder": "Enter your email"}),
            "password1": forms.PasswordInput(attrs={"placeholder": "Enter password"}),
            "password2": forms.PasswordInput(attrs={"placeholder": "Repeat password"}),
        }
        labels = {
            "email": "Email Address",
            "password1": "Password",
            "password2": "Confirm Password",
        }

    def clean_email(self):
        email = self.cleaned_data.get("email")
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("A user with that email already exists.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        # Set username as email for compatibility with standard User model
        user.username = self.cleaned_data["email"]
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
        return user


# ------------------------ School Signup Forms ------------------------
class SchoolSignupFormBasic(UserCreationForm):
    # Nota: SchoolProfile se crea desde la vista, no desde este formulario.
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={"placeholder": "Enter your contact email"}),
        label="Email Address",
    )

    class Meta:
        model = User
        fields = ("email", "password1", "password2")

    def clean_email(self):
        email = self.cleaned_data.get("email")
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("A user with that email already exists.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = self.cleaned_data["email"]
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
        return user


class SchoolProfileCompletionForm(forms.ModelForm):
    name = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={"placeholder": "School name"}),
        label="School Name",
        required=True,
    )
    country = forms.ModelChoiceField(
        queryset=Country.objects.all(),
        widget=autocomplete.ModelSelect2(url='country-autocomplete'),
        label="Country",
        required=True,
    )
    city = forms.ModelChoiceField(
        queryset=City.objects.all(),
        widget=autocomplete.ModelSelect2(url='city-autocomplete'),
        label="City",
        required=True,
    )
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={"placeholder": "Contact email"}),
        label="Contact Email",
        required=True,
    )
    phone = forms.CharField(
        max_length=20,
        widget=forms.TextInput(attrs={"placeholder": "Contact phone number"}),
        label="Phone",
        required=False,
    )
    website = forms.URLField(
        widget=forms.TextInput(attrs={"placeholder": "https://yourwebsite.com"}),
        label="Website",
        required=False,
    )
    description_short = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Short description of your school"}),
        label="Short Description",
        required=False,
    )
    description_long = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 5, "placeholder": "Detailed description of your school"}),
        label="Long Description",
        required=False,
    )
    logo = forms.ImageField(
        widget=forms.ClearableFileInput(),
        label="School Logo",
        required=False,
    )
    cover_image = forms.ImageField(
        widget=forms.ClearableFileInput(),
        label="Cover Image",
        required=False,
    )
    socials = forms.CharField(
        widget=forms.TextInput(attrs={"placeholder": "Social media links (comma separated)"}),
        label="Social Media",
        required=False,
    )
    SERVICE_TYPE_CHOICES = [
        ("lesson", "Lesson / Course"),
        ("experience", "Experience"),
        ("professional", "Professional"),
        ("rental", "Rental"),
        ("pack", "Pack"),
    ]

    service_types = forms.MultipleChoiceField(
        choices=SERVICE_TYPE_CHOICES,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "form-check-input poppins"}),
        label="Service Types",
        required=False,
    )

    class Meta:
        model = School
        fields = (
            "name",
            "country",
            "city",
            "email",
            "phone",
            "website",
            "description_short",
            "description_long",
            "logo",
            "cover_image",
            "socials",
            "service_types",
        )

    def clean_service_types(self):
        data = self.cleaned_data.get("service_types")
        return data or []


# ------------------------ School Activity Completion Workflow Forms ------------------------
from .models import SchoolActivity, SchoolActivityVariant, SchoolActivitySeason, SchoolActivitySession

class SchoolActivityForm(forms.ModelForm):
    class Meta:
        model = SchoolActivity
        fields = ("activity_description", "activity_profile_image", "is_active")
        widgets = {
            "activity_description": forms.Textarea(attrs={
                "placeholder": "Describe the activity",
                "class": "form-control poppins",
                "rows": 3,
            }),
            "activity_profile_image": forms.ClearableFileInput(attrs={
                "class": "form-control poppins",
            }),
            "is_active": forms.CheckboxInput(attrs={
                "class": "form-check-input poppins",
            }),
        }
        labels = {
            "activity_description": "Activity Description",
            "activity_profile_image": "Profile Image",
            "is_active": "Active",
        }


class SchoolActivityVariantForm(forms.ModelForm):
    class Meta:
        model = SchoolActivityVariant
        fields = (
            "name", "description", "price", "classes", "persons", "instructors",
            "difficulty", "experience_type", "duration_minutes", "equipment_included", "is_active"
        )
        widgets = {
            "name": forms.TextInput(attrs={
                "placeholder": "Variant name",
                "class": "form-control poppins",
            }),
            "description": forms.Textarea(attrs={
                "placeholder": "Describe this variant",
                "class": "form-control poppins",
                "rows": 2,
            }),
            "price": forms.NumberInput(attrs={
                "placeholder": "Price (€)",
                "class": "form-control poppins",
                "step": "0.01",
            }),
            "classes": forms.NumberInput(attrs={
                "placeholder": "Number of classes",
                "class": "form-control poppins",
            }),
            "persons": forms.NumberInput(attrs={
                "placeholder": "Number of persons",
                "class": "form-control poppins",
            }),
            "instructors": forms.TextInput(attrs={
                "placeholder": "Instructors (comma separated)",
                "class": "form-control poppins",
            }),
            "difficulty": forms.Select(attrs={
                "class": "form-control poppins",
            }),
            "experience_type": forms.Select(attrs={
                "class": "form-control poppins",
            }),
            "duration_minutes": forms.NumberInput(attrs={
                "placeholder": "Duration (minutes)",
                "class": "form-control poppins",
            }),
            "equipment_included": forms.Textarea(attrs={
                "placeholder": "Equipment included",
                "class": "form-control poppins",
                "rows": 2,
            }),
            "is_active": forms.CheckboxInput(attrs={
                "class": "form-check-input poppins",
            }),
        }
        labels = {
            "name": "Variant Name",
            "description": "Description",
            "price": "Price (€)",
            "classes": "Number of Classes",
            "persons": "Number of Persons",
            "instructors": "Instructors",
            "difficulty": "Difficulty",
            "experience_type": "Experience Type",
            "duration_minutes": "Duration (minutes)",
            "equipment_included": "Equipment Included",
            "is_active": "Active",
        }


class SchoolActivitySeasonForm(forms.ModelForm):
    class Meta:
        model = SchoolActivitySeason
        fields = ("season_type", "start_month", "end_month", "description", "is_active")
        widgets = {
            "season_type": forms.Select(attrs={
                "class": "form-control poppins",
            }),
            "start_month": forms.Select(attrs={
                "class": "form-control poppins",
            }),
            "end_month": forms.Select(attrs={
                "class": "form-control poppins",
            }),
            "description": forms.Textarea(attrs={
                "placeholder": "Season description",
                "class": "form-control poppins",
                "rows": 2,
            }),
            "is_active": forms.CheckboxInput(attrs={
                "class": "form-check-input poppins",
            }),
        }
        labels = {
            "season_type": "Season Type",
            "start_month": "Start Month",
            "end_month": "End Month",
            "description": "Description",
            "is_active": "Active",
        }


class SchoolActivitySessionForm(forms.ModelForm):
    class Meta:
        model = SchoolActivitySession
        fields = ("date_start", "date_end", "time_slots", "capacity", "is_available")
        widgets = {
            "date_start": forms.DateInput(attrs={
                "type": "date",
                "placeholder": "Start date",
                "class": "form-control poppins",
            }),
            "date_end": forms.DateInput(attrs={
                "type": "date",
                "placeholder": "End date",
                "class": "form-control poppins",
            }),
            "time_slots": forms.TextInput(attrs={
                "placeholder": "Time slots (e.g. 09:00-12:00,13:00-16:00)",
                "class": "form-control poppins",
            }),
            "capacity": forms.NumberInput(attrs={
                "placeholder": "Capacity",
                "class": "form-control poppins",
            }),
            "is_available": forms.CheckboxInput(attrs={
                "class": "form-check-input poppins",
            }),
        }
        labels = {
            "date_start": "Start Date",
            "date_end": "End Date",
            "time_slots": "Time Slots",
            "capacity": "Capacity",
            "is_available": "Available",
        }


# ------------------------ Instructor Signup Form ------------------------
class InstructorSignupForm(forms.ModelForm):
    class Meta:
        model = InstructorProfile
        fields = ("bio", "age", "gender", "profile_image", "certifications")
        widgets = {
            "bio": forms.Textarea(attrs={"placeholder": "Enter your biography"}),
            "age": forms.NumberInput(attrs={"placeholder": "Age"}),
            "gender": forms.Select(attrs={}),
            "profile_image": forms.ClearableFileInput(),
            "certifications": forms.Textarea(attrs={"placeholder": "List your certifications"}),
        }
        labels = {
            "bio": "Biography",
            "age": "Age",
            "gender": "Gender",
            "profile_image": "Profile Image",
            "certifications": "Certifications",
        }


# ------------------------ User Signup Form ------------------------

class UserSignupForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ("phone", "birth_date", "profile_image")
        widgets = {
            "phone": forms.TextInput(attrs={"placeholder": "Enter your phone number"}),
            "birth_date": forms.DateInput(attrs={"type": "date", "placeholder": "YYYY-MM-DD"}),
            "profile_image": forms.ClearableFileInput(),
        }
        labels = {
            "phone": "Phone Number",
            "birth_date": "Birth Date",
            "profile_image": "Profile Image",
        }


# ------------------------ User Profile Form ------------------------
class UserProfileForm(forms.ModelForm):
    """
    Unified user profile form for the /account/ page.
    Supports editing all main user profile information: first name, last name, email (read-only), phone,
    birth date, nationality, gender, address, and profile image. All widgets and placeholders follow
    Poppins-based UX style conventions.
    """
    first_name = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={
            "placeholder": "First name",
            "class": "form-control poppins",
            "autocomplete": "given-name"
        }),
        label="First Name",
    )
    last_name = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={
            "placeholder": "Last name",
            "class": "form-control poppins",
            "autocomplete": "family-name"
        }),
        label="Last Name",
    )
    email = forms.EmailField(
        required=False,
        widget=forms.EmailInput(attrs={
            "placeholder": "Email address",
            "class": "form-control poppins",
            "readonly": "readonly",
            "autocomplete": "email"
        }),
        label="Email Address",
        disabled=True,
    )
    phone = forms.CharField(
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={
            "placeholder": "Phone number",
            "class": "form-control poppins",
            "autocomplete": "tel"
        }),
        label="Phone Number",
    )
    birth_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={
            "type": "date",
            "placeholder": "YYYY-MM-DD",
            "class": "form-control poppins",
            "autocomplete": "bday"
        }),
        label="Birth Date",
    )
    nationality = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={
            "placeholder": "Country of origin",
            "class": "form-control poppins",
            "autocomplete": "country"
        }),
        label="Nationality",
    )
    gender = forms.ChoiceField(
        choices=[
            ("male", "Male"),
            ("female", "Female"),
            ("prefer_not_say", "Prefer not to say"),
        ],
        required=False,
        widget=forms.Select(attrs={
            "class": "form-control poppins"
        }),
        label="Gender",
    )
    address = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={
            "placeholder": "Address (street, city, postal code)",
            "class": "form-control poppins",
            "autocomplete": "street-address"
        }),
        label="Address",
    )
    profile_image = forms.ImageField(
        required=False,
        widget=forms.ClearableFileInput(attrs={
            "class": "form-control poppins"
        }),
        label="Profile Image",
    )

    class Meta:
        model = UserProfile
        fields = (
            "first_name",
            "last_name",
            "email",
            "phone",
            "birth_date",
            "nationality",
            "gender",
            "address",
            "profile_image",
        )


# ------------------------ Delete Account Form ------------------------
class DeleteAccountForm(forms.Form):
    """
    Formulario simple de confirmación para eliminar cuenta.
    """
    confirm = forms.BooleanField(
        required=True,
        label="Confirm that you want to delete your account permanently",
    )


# ------------------------ Booking Form ------------------------
class BookingForm(forms.ModelForm):
    """
    Formulario para crear reservas.
    """
    class Meta:
        from .models import Booking
        model = Booking
        fields = ("variant", "session_date", "notes")
        widgets = {
            "session_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3, "placeholder": "Any notes for the instructor"}),
        }


# ------------------------ Payment Form ------------------------
class PaymentForm(forms.ModelForm):
    """
    Formulario para registrar o mostrar pagos.
    Normalmente solo lectura, usado en vistas de historial.
    """
    class Meta:
        from .models import Payment
        model = Payment
        fields = ("amount", "currency", "status", "stripe_payment_id")
        widgets = {
            "amount": forms.NumberInput(attrs={"readonly": True}),
            "currency": forms.TextInput(attrs={"readonly": True}),
            "status": forms.TextInput(attrs={"readonly": True}),
            "stripe_payment_id": forms.TextInput(attrs={"readonly": True}),
        }