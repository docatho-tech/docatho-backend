from allauth.account.forms import SignupForm
from allauth.socialaccount.forms import SignupForm as SocialSignupForm
from django.contrib.auth import forms as admin_forms
from django.forms import EmailField
from django.utils.translation import gettext_lazy as _
from phonenumber_field.formfields import PhoneNumberField as PhoneNumberFormField

from .models import User


class UserAdminChangeForm(admin_forms.UserChangeForm):
    class Meta(admin_forms.UserChangeForm.Meta):  # type: ignore[name-defined]
        model = User
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Override phone field with form field to avoid auto-generation issues
        from phonenumber_field.formfields import (
            PhoneNumberField as PhoneNumberFormField,
        )

        self.fields["phone"] = PhoneNumberFormField(required=True)
        # Make email optional
        if "email" in self.fields:
            self.fields["email"].required = False


class UserAdminCreationForm(admin_forms.AdminUserCreationForm):
    """
    Form for User Creation in the Admin Area.
    To change user signup, see UserSignupForm and UserSocialSignupForm.
    """

    class Meta(admin_forms.UserCreationForm.Meta):  # type: ignore[name-defined]
        model = User
        fields = ()  # Exclude all auto-generated fields
        error_messages = {
            "phone": {"unique": _("This phone has already been taken.")},
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Add phone field explicitly to avoid auto-generation issues
        self.fields["phone"] = PhoneNumberFormField(required=True)
        # Add email field as optional
        self.fields["email"] = EmailField(required=False)


class UserSignupForm(SignupForm):
    """
    Form that will be rendered on a user sign up section/screen.
    Default fields will be added automatically.
    Check UserSocialSignupForm for accounts created from social.
    """


class UserSocialSignupForm(SocialSignupForm):
    """
    Renders the form when user has signed up using social accounts.
    Default fields will be added automatically.
    See UserSignupForm otherwise.
    """
