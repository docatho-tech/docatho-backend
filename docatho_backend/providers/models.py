from django.db import models

from docatho_backend.masters.models import BaseModel
from docatho_backend.providers.enums import ProviderType
from docatho_backend.users.models import User


class OnboardingStatus(models.TextChoices):
    """
    Where a partner sits in the invite-to-live pipeline.

    `APPROVED` is the only value that means "this partner can take orders";
    everything above it is the onboarding queue the admin works through, and
    each value is one of the pills the Invite Partner screen renders.
    """

    INVITED = "invited", "Invited"
    UNDER_VERIFICATION = "under_verification", "Under Verification"
    INSUFFICIENT_DOCUMENTS = "insufficient_documents", "Insuff. Documents"
    REJECTED = "rejected", "Rejected"
    APPROVED = "approved", "Approved"


class Provider(BaseModel):
    name = models.CharField(max_length=255)
    specialty = models.CharField(max_length=255)
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="provider")

    # ------------------------------------------------------------------
    # Identity and location.
    #
    # Stored URLs rather than ImageFields, matching `DoctorProfile.
    # profile_picture` and `Medicine.image_url`: bytes go to S3 through
    # /api/uploads/ and only the address is kept, so every image in the
    # product is addressed one way. CharField because that endpoint answers
    # with a relative "/media/..." path off S3, which URLField rejects.
    # ------------------------------------------------------------------
    logo_url = models.CharField(max_length=500, blank=True, default="")
    description = models.TextField(blank=True, default="")
    # Phone and email deliberately live on `user`, not here: that account is
    # what the partner signs in with and what the admin already edits through
    # AdminProviderSerializer. A second copy on the profile would be the one
    # the lists render and the one nobody kept up to date.
    city = models.CharField(max_length=100, blank=True, default="")
    # The neighbourhood inside the city — "Kukatpally", "Andheri". The
    # partner lists show it beside the city, so it is its own field rather
    # than the first line of the address.
    location = models.CharField(max_length=120, blank=True, default="")
    address_line1 = models.CharField(max_length=255, blank=True, default="")
    address_line2 = models.CharField(max_length=255, blank=True, default="")
    pincode = models.CharField(max_length=12, blank=True, default="")
    map_url = models.CharField(max_length=500, blank=True, default="")

    # Point of contact: who the admin rings about this partner. Distinct from
    # `user`, which is the login the partner signs in with.
    poc_name = models.CharField(max_length=255, blank=True, default="")
    poc_phone = models.CharField(max_length=20, blank=True, default="")

    # Denormalised from the reviews the patient app writes, the same pair
    # `DoctorProfile` keeps, so a partner row can show a rating without a
    # per-row aggregate query.
    rating_avg = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    review_count = models.PositiveIntegerField(default=0)
    is_online = models.BooleanField(default=False)

    # Onboarding pipeline.
    onboarding_status = models.CharField(
        max_length=32,
        choices=OnboardingStatus.choices,
        default=OnboardingStatus.APPROVED,
    )
    invited_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, default="")

    # Proof documents, as upload URLs like `logo_url`.
    license_document = models.CharField(max_length=500, blank=True, default="")
    registration_certificate = models.CharField(max_length=500, blank=True, default="")
    gst_certificate = models.CharField(max_length=500, blank=True, default="")

    # type Doctor, Diagnostic Center, Chemist, etc.
    provider_type = models.CharField(
        max_length=100,
        choices=ProviderType.choices(),
        default=ProviderType.CHEMIST.value,
    )

    # Payout / bank details (EP-07)
    bank_account_name = models.CharField(max_length=255, blank=True, null=True)
    bank_account_number = models.CharField(max_length=34, blank=True, null=True)
    bank_ifsc = models.CharField(max_length=20, blank=True, null=True)
    upi_id = models.CharField(max_length=100, blank=True, null=True)

    # Per-centre platform cut. The dashboard sale rail and order commission
    # both read this so a lab on 8% and a pharmacy on 12% stay honest.
    commission_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=10,
    )

    def __str__(self):
        return self.name
