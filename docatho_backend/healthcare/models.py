"""Phase 1 healthcare domain: doctors, appointments, diagnostics, reminders, support."""

from __future__ import annotations

from django.conf import settings
from django.db import models

from docatho_backend.masters.models import BaseModel
from docatho_backend.medicines.models import Medicine
from docatho_backend.providers.models import Provider


class MedicalSpecialty(BaseModel):
    name = models.CharField(max_length=120, unique=True)
    icon_url = models.URLField(blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "medical specialties"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class VerificationStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class Qualification(BaseModel):
    """A degree an admin can pick from when filing a doctor's credentials.

    Only an option source. `DoctorProfile.qualifications` stays a JSON list of
    strings, which is what both apps already read — this exists so the picker
    has something to offer, and so a missing degree is added by an admin rather
    than by a deploy.
    """

    name = models.CharField(max_length=120, unique=True)
    level = models.CharField(
        max_length=10,
        choices=[
            ("ug", "Undergraduate"),
            ("pg", "Postgraduate"),
            ("other", "Other"),
        ],
        default="other",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class ConsultationMode(models.TextChoices):
    ONLINE = "online", "Online"
    IN_CLINIC = "in_clinic", "In-Clinic"
    HOME_VISIT = "home_visit", "Home Visit"


class DoctorProfile(BaseModel):
    provider = models.OneToOneField(
        Provider,
        on_delete=models.CASCADE,
        related_name="doctor_profile",
    )
    biography = models.TextField(blank=True, default="")
    qualifications = models.JSONField(default=list, blank=True)
    qualifications_ug = models.JSONField(default=list, blank=True)
    qualifications_pg = models.JSONField(default=list, blank=True)
    experience_years = models.PositiveIntegerField(default=0)
    languages = models.JSONField(default=list, blank=True)
    fee_online = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    fee_in_clinic = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    fee_home_visit = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    consultation_modes = models.JSONField(default=list, blank=True)
    rating_avg = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    review_count = models.PositiveIntegerField(default=0)
    clinic_name = models.CharField(max_length=255, blank=True, default="")
    clinic_address = models.TextField(blank=True, default="")
    clinic_city = models.CharField(max_length=100, blank=True, default="")
    clinic_latitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True,
    )
    clinic_longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True,
    )
    clinic_images = models.JSONField(default=list, blank=True)
    # A stored URL rather than an ImageField, matching `clinic_images` and
    # `Medicine.image_url`: the bytes go to S3 through /api/uploads/ and only
    # the address is kept, so every image in the product is addressed one way.
    #
    # CharField, not URLField: /api/uploads/ answers with whatever the active
    # storage backend calls the file, which is an absolute S3 URL in production
    # but a relative "/media/..." path on local disk. URLField rejects the
    # latter, so the field could not hold its own endpoint's output off S3.
    profile_picture = models.CharField(max_length=500, blank=True, default="")
    # URLs from /api/uploads/, like `profile_picture`, rather than FileFields.
    # Nothing ever wrote them as files — no endpoint accepted an upload, so a
    # doctor was approved on the strength of the profile text alone. Storing a
    # URL means one upload path for every document in the product, and it takes
    # PDFs, which is what a licence usually is.
    license_document = models.CharField(max_length=500, blank=True, default="")
    degree_document = models.CharField(max_length=500, blank=True, default="")
    verification_status = models.CharField(
        max_length=20,
        choices=VerificationStatus.choices,
        default=VerificationStatus.PENDING,
    )
    is_online = models.BooleanField(default=False)
    is_verified = models.BooleanField(default=False)
    auto_accept_appointments = models.BooleanField(default=False)
    specialties = models.ManyToManyField(
        MedicalSpecialty,
        related_name="doctors",
        blank=True,
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Dr. {self.provider.name}"


class DoctorAvailability(BaseModel):
    doctor = models.ForeignKey(
        DoctorProfile,
        on_delete=models.CASCADE,
        related_name="availability_slots",
    )
    day_of_week = models.PositiveSmallIntegerField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    consultation_mode = models.CharField(
        max_length=20,
        choices=ConsultationMode.choices,
        default=ConsultationMode.ONLINE,
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["day_of_week", "start_time"]
        unique_together = [("doctor", "day_of_week", "start_time", "consultation_mode")]


class ProviderAvailability(BaseModel):
    """Weekly hours for a lab, diagnostic centre or home-care agency.

    Doctors already have `DoctorAvailability`. Centres need the same slot
    picker so a patient can book an MRI between 9 and 1, not "sometime today".
    """

    provider = models.ForeignKey(
        Provider,
        on_delete=models.CASCADE,
        related_name="availability_slots",
    )
    day_of_week = models.PositiveSmallIntegerField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["day_of_week", "start_time"]
        unique_together = [("provider", "day_of_week", "start_time")]


class BlockedDate(BaseModel):
    doctor = models.ForeignKey(
        DoctorProfile,
        on_delete=models.CASCADE,
        related_name="blocked_dates",
    )
    date = models.DateField()
    reason = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        unique_together = [("doctor", "date")]


class AppointmentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    CONFIRMED = "confirmed", "Confirmed"
    REJECTED = "rejected", "Rejected"
    IN_PROGRESS = "in_progress", "In Progress"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class AppointmentPaymentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PAID = "paid", "Paid"
    FAILED = "failed", "Failed"
    REFUNDED = "refunded", "Refunded"


class Appointment(BaseModel):
    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="appointments",
    )
    doctor = models.ForeignKey(
        DoctorProfile,
        on_delete=models.CASCADE,
        related_name="appointments",
    )
    scheduled_at = models.DateTimeField()
    consultation_mode = models.CharField(
        max_length=20,
        choices=ConsultationMode.choices,
    )
    status = models.CharField(
        max_length=20,
        choices=AppointmentStatus.choices,
        default=AppointmentStatus.PENDING,
    )
    fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    # The platform's cut of what the patient pays, shown as its own line in the
    # payment summary both apps render ("Platform fee (incl. of GST)"). Kept
    # apart from `fee`, which is the doctor's charge: the two are paid to
    # different parties and a single total cannot be split back apart later.
    platform_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    payment_method = models.CharField(max_length=20, blank=True, default="")
    payment_status = models.CharField(
        max_length=20,
        choices=AppointmentPaymentStatus.choices,
        default=AppointmentPaymentStatus.PENDING,
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    video_room_id = models.CharField(max_length=120, blank=True, default="")
    video_started_at = models.DateTimeField(null=True, blank=True)
    video_ended_at = models.DateTimeField(null=True, blank=True)
    recording_url = models.URLField(blank=True, default="")
    transcript_url = models.URLField(blank=True, default="")
    symptoms = models.TextField(blank=True, default="")
    notes = models.TextField(blank=True, default="")
    prescription_notes = models.TextField(blank=True, default="")
    patient_rating = models.PositiveSmallIntegerField(null=True, blank=True)
    patient_feedback = models.TextField(blank=True, default="")
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-scheduled_at"]

    def __str__(self) -> str:
        return f"Appt #{self.pk} — {self.patient} / {self.doctor}"


class ConsultationMessageKind(models.TextChoices):
    """
    What a row in the consultation thread is.

    A missed call is not a message with the text "missed call" — it has no
    body, cannot be replied to, and the thread renders it as its own card.
    Modelling it as a kind rather than a magic string keeps the client from
    having to parse prose to decide how to draw a row.
    """

    TEXT = "text", "Text"
    FILE = "file", "File"
    MISSED_CALL = "missed_call", "Missed call"
    SYSTEM = "system", "System"


class ConsultationMessage(BaseModel):
    """
    One line of the doctor↔patient conversation attached to an appointment.

    `sender` is the account that wrote it, so the client decides which side of
    the thread a bubble sits on by comparing it with the appointment's patient;
    a stored "is_from_doctor" flag would go stale the moment an admin posted on
    a doctor's behalf.
    """

    appointment = models.ForeignKey(
        Appointment,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="consultation_messages",
    )
    kind = models.CharField(
        max_length=16,
        choices=ConsultationMessageKind.choices,
        default=ConsultationMessageKind.TEXT,
    )
    body = models.TextField(blank=True, default="")
    # An upload URL from /api/uploads/, plus the name and size to render the
    # attachment card without fetching the file itself.
    attachment_url = models.CharField(max_length=500, blank=True, default="")
    attachment_name = models.CharField(max_length=255, blank=True, default="")
    attachment_size = models.PositiveIntegerField(null=True, blank=True)
    # A reply quotes the row above it — the "Sorry, I was in a meeting" card
    # that sits under the missed call it answers.
    reply_to = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replies",
    )
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"Msg #{self.pk} on appt {self.appointment_id}"


class AppointmentPaymentTransaction(BaseModel):
    appointment = models.ForeignKey(
        Appointment,
        on_delete=models.CASCADE,
        related_name="payment_transactions",
    )
    provider = models.CharField(max_length=32, default="razorpay")
    transaction_order_id = models.CharField(max_length=128, db_index=True)
    razorpay_payment_id = models.CharField(max_length=128, blank=True, default="")
    razorpay_signature = models.CharField(max_length=256, blank=True, default="")
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    succeeded = models.BooleanField(default=False)
    paid_at = models.DateTimeField(null=True, blank=True)
    raw_response = models.JSONField(default=dict, blank=True)


class SavedDoctor(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="saved_doctors",
    )
    doctor = models.ForeignKey(
        DoctorProfile,
        on_delete=models.CASCADE,
        related_name="saved_by",
    )

    class Meta:
        unique_together = [("user", "doctor")]


class DiagnosticTestCategory(BaseModel):
    name = models.CharField(max_length=120, unique=True)
    icon_url = models.URLField(blank=True, default="")
    is_active = models.BooleanField(default=True)

    def __str__(self) -> str:
        return self.name


class DiagnosticTest(BaseModel):
    name = models.CharField(max_length=255)
    category = models.ForeignKey(
        DiagnosticTestCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tests",
    )
    # The lab that runs this test. Null is the shared catalogue every centre
    # can fulfil — which is what every row was before centres could own one.
    provider = models.ForeignKey(
        Provider,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="diagnostic_tests",
    )
    description = models.TextField(blank=True, default="")
    # `price` is what the patient pays. `mrp` is the list price it is struck
    # through against; null means there is no discount to show, not zero.
    price = models.DecimalField(max_digits=10, decimal_places=2)
    mrp = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    # What is actually collected — "Blood", "Urine", "Tissue". The provider app
    # shows it beside the category on every test row, because it decides what
    # the technician has to prepare for. Free text: the list is long, varies by
    # lab, and a choices list would reject the next one.
    sample_type = models.CharField(max_length=60, blank=True, default="")
    preparation_instructions = models.TextField(blank=True, default="")
    # A list of image URLs uploaded through /api/uploads/, same shape as
    # `DoctorProfile.clinic_images`. A test can show a sample report, the
    # equipment and the collection kit, so one field would not do.
    images = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    test_kind = models.CharField(
        max_length=20,
        choices=[
            ("lab", "Lab"),
            ("imaging", "Imaging"),
            ("home_care", "Home care"),
        ],
        default="lab",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @property
    def discount_percent(self) -> int:
        return _discount_percent(self.price, self.mrp)


def _discount_percent(price, mrp) -> int:
    """Whole percent off the list price, or 0 when there is nothing to show."""
    if not mrp or not price or mrp <= price:
        return 0
    return int(round((mrp - price) / mrp * 100))


class DiagnosticPackage(BaseModel):
    """Two or more tests sold together at one price.

    A panel is how labs actually sell — "full body checkup, 62 tests, ₹1,499" —
    and pricing it as the sum of its parts is not the offer. The tests stay
    first-class: a package points at them, so a booking still records exactly
    which tests were ordered.
    """

    name = models.CharField(max_length=255)
    provider = models.ForeignKey(
        Provider,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="diagnostic_packages",
    )
    description = models.TextField(blank=True, default="")
    tests = models.ManyToManyField(DiagnosticTest, related_name="packages")
    price = models.DecimalField(max_digits=10, decimal_places=2)
    mrp = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    preparation_instructions = models.TextField(blank=True, default="")
    images = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @property
    def discount_percent(self) -> int:
        return _discount_percent(self.price, self.mrp)


class DiagnosticBookingKind(models.TextChoices):
    LAB = "lab", "Lab"
    DIAGNOSTIC = "diagnostic", "Diagnostic"
    HOME_HEALTHCARE = "home_healthcare", "Home Healthcare"


class DiagnosticBookingStatus(models.TextChoices):
    REQUESTED = "requested", "Requested"
    CONFIRMED = "confirmed", "Confirmed"
    SAMPLE_COLLECTED = "sample_collected", "Sample Collected"
    REPORT_GENERATED = "report_generated", "Report Generated"
    SLOT_ALLOTTED = "slot_allotted", "Slot Allotted"
    PATIENT_ARRIVED = "patient_arrived", "Patient Arrived"
    TEST_DONE = "test_done", "Test Done"
    ASSIGNED = "assigned", "Assigned"
    IN_PROGRESS = "in_progress", "In Progress"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class VisitType(models.TextChoices):
    AT_CENTRE = "at_centre", "At Centre"
    AT_HOME = "at_home", "At Home"


class DiagnosticBooking(BaseModel):
    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="diagnostic_bookings",
    )
    center = models.ForeignKey(
        Provider,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="diagnostic_bookings",
    )
    tests = models.ManyToManyField(DiagnosticTest, related_name="bookings")
    # Packages booked as such. Their tests are also written into `tests`, so
    # anything reading a booking's tests keeps working; this records what was
    # actually sold, and at which price.
    packages = models.ManyToManyField(
        DiagnosticPackage, related_name="bookings", blank=True
    )
    kind = models.CharField(
        max_length=32,
        choices=DiagnosticBookingKind.choices,
        default=DiagnosticBookingKind.LAB,
        db_index=True,
    )
    status = models.CharField(
        max_length=32,
        choices=DiagnosticBookingStatus.choices,
        default=DiagnosticBookingStatus.REQUESTED,
    )
    # Where the sample is taken. A non-empty `patient_address` used to be the
    # only hint, which is not the same question — a centre visit can still
    # carry an address for the report, and a home visit booked by phone can
    # have none yet. The lists show it as its own column, so it is its own
    # field.
    visit_type = models.CharField(
        max_length=16,
        choices=VisitType.choices,
        default=VisitType.AT_CENTRE,
    )
    scheduled_date = models.DateField(null=True, blank=True)
    scheduled_time = models.TimeField(null=True, blank=True)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    patient_address = models.TextField(blank=True, default="")
    notes = models.TextField(blank=True, default="")
    # Result documents the centre uploads, as URLs from /api/uploads/ — the
    # same convention every other document in the product uses. A list, not one
    # file: a booking can carry several tests and each has its own report, and
    # a re-test adds a row rather than overwriting the first result.
    reports = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-created_at"]


class MedicineReminder(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="medicine_reminders",
    )
    medicine = models.ForeignKey(
        Medicine,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reminders",
    )
    medicine_name = models.CharField(max_length=255)
    dosage = models.CharField(max_length=100, blank=True, default="")
    reminder_times = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class WishlistItem(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="wishlist_items",
    )
    medicine = models.ForeignKey(
        Medicine,
        on_delete=models.CASCADE,
        related_name="wishlisted_by",
    )

    class Meta:
        unique_together = [("user", "medicine")]


class SupportTicketStatus(models.TextChoices):
    OPEN = "open", "Open"
    IN_PROGRESS = "in_progress", "In Progress"
    RESOLVED = "resolved", "Resolved"
    CLOSED = "closed", "Closed"


class SupportTicketPriority(models.TextChoices):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"


class SupportTicket(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="support_tickets",
    )
    subject = models.CharField(max_length=255)
    description = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=SupportTicketStatus.choices,
        default=SupportTicketStatus.OPEN,
    )
    priority = models.CharField(
        max_length=10,
        choices=SupportTicketPriority.choices,
        default=SupportTicketPriority.MEDIUM,
    )
    # Files the reporter attached, as upload URLs from /api/uploads/ — the same
    # convention every other document in the product uses.
    attachments = models.JSONField(default=list, blank=True)
    # The partner this ticket is *about*, when it is a complaint rather than a
    # question. Distinct from `user`, who raised it: a patient complaining
    # about a lab is one row with both set. The partner profile's "Complaints"
    # figure counts these.
    about_provider = models.ForeignKey(
        "providers.Provider",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="complaints",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_tickets",
    )

    class Meta:
        ordering = ["-created_at"]


class ContentPageType(models.TextChoices):
    FAQ = "faq", "FAQ"
    ABOUT = "about", "About Us"
    PRIVACY = "privacy", "Privacy Policy"
    TERMS = "terms", "Terms & Conditions"


class ContentPage(BaseModel):
    page_type = models.CharField(max_length=20, choices=ContentPageType.choices)
    title = models.CharField(max_length=255)
    body = models.TextField()
    is_published = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "title"]
        unique_together = [("page_type", "title")]


class AIChatSession(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ai_sessions",
    )
    title = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]


class AIChatMessage(BaseModel):
    session = models.ForeignKey(
        AIChatSession,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    role = models.CharField(max_length=20)
    content = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["created_at"]
