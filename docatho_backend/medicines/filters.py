from django_filters import rest_framework as filters

from docatho_backend.medicines.models import Medicine


class MedicineFilter(filters.FilterSet):
    """Catalogue filters for the pharmacy browse screen.

    `filterset_fields` could already match a category, a schedule and the
    prescription flag exactly, but the filter screen also asks three questions
    a field match cannot answer: a price range, "only what you can order now",
    and more than one schedule at once. Those are declared here; the exact
    matches stay in `Meta.fields` so the existing query strings keep working.
    """

    price_min = filters.NumberFilter(field_name="price", lookup_expr="gte")
    price_max = filters.NumberFilter(field_name="price", lookup_expr="lte")

    # "In stock" is `stock > 0` — the same test as `Medicine.in_stock`, which
    # is a property and so cannot be filtered on in the database.
    in_stock = filters.BooleanFilter(method="filter_in_stock")

    # The screen offers the schedules as a multi-select ("H, H1"), which a
    # single `schedule=` match cannot express.
    schedule_in = filters.BaseInFilter(field_name="schedule", lookup_expr="in")

    class Meta:
        model = Medicine
        fields = [
            "is_active",
            "name",
            "category",
            "schedule",
            "is_prescription_required",
        ]

    def filter_in_stock(self, queryset, name, value):
        if value is None:
            return queryset
        return queryset.filter(stock__gt=0) if value else queryset.filter(stock=0)
