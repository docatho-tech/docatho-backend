from rest_framework import serializers

from docatho_backend.medicines.models import Category
from docatho_backend.medicines.models import Medicine


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = [
            "id",
            "name",
            "description",
            "image_url",
            "is_active",
            "created_at",
            "updated_at",
        ]


class MedicineSerializer(serializers.ModelSerializer):
    # Nested categories for reads; write via `category_ids` (PKs).
    category = CategorySerializer(many=True, read_only=True)
    category_ids = serializers.PrimaryKeyRelatedField(
        many=True,
        write_only=True,
        queryset=Category.objects.all(),
        source="category",
        required=False,
    )

    class Meta:
        model = Medicine
        fields = [
            "id",
            "name",
            "brand",
            "category",
            "category_ids",
            "content",
            "image_url",
            "manufacturer",
            "description",
            "price",
            "mrp",
            "stock",
            "schedule",
            "is_prescription_required",
            "is_active",
            "created_at",
            "updated_at",
        ]
