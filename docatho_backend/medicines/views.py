from rest_framework import viewsets
from rest_framework.pagination import PageNumberPagination

from docatho_backend.masters.permissions import IsAdmin
from docatho_backend.masters.permissions import ReadOnlyOrAdmin
from docatho_backend.medicines.models import Category
from docatho_backend.medicines.models import Medicine
from docatho_backend.medicines.serializers import CategorySerializer
from docatho_backend.medicines.serializers import MedicineSerializer


class GenericPaginationClass(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100


class CategoryViewset(viewsets.ModelViewSet):
    serializer_class = CategorySerializer
    pagination_class = GenericPaginationClass
    queryset = Category.objects.all()
    permission_classes = [ReadOnlyOrAdmin]
    filterset_fields = ["is_active", "name"]
    search_fields = ["name", "description"]
    ordering_fields = ["created_at", "updated_at", "name"]


class MedicineViewset(viewsets.ModelViewSet):
    """Patient/browse-facing catalogue. Public reads, admin-only writes."""

    serializer_class = MedicineSerializer
    pagination_class = GenericPaginationClass
    queryset = Medicine.objects.prefetch_related("category").all()
    permission_classes = [ReadOnlyOrAdmin]
    filterset_fields = [
        "is_active",
        "name",
        "category",
        "schedule",
        "is_prescription_required",
    ]
    search_fields = ["name", "brand", "manufacturer", "description"]
    ordering_fields = ["created_at", "updated_at", "name", "price"]


class AdminMedicineViewset(viewsets.ModelViewSet):
    """Admin catalogue management (EP-09). Staff only, all methods."""

    serializer_class = MedicineSerializer
    pagination_class = GenericPaginationClass
    queryset = Medicine.objects.prefetch_related("category").all()
    permission_classes = [IsAdmin]
    filterset_fields = [
        "is_active",
        "name",
        "category",
        "schedule",
        "is_prescription_required",
    ]
    search_fields = ["name", "brand", "manufacturer", "description"]
    ordering_fields = ["created_at", "updated_at", "name", "price"]
