from unicodedata import category
from rest_framework import serializers
from docatho_backend.medicines.models import Category, Medicine


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "image_url", "is_active", "created_at", "updated_at"]


class MedicineSerializer(serializers.ModelSerializer):
    category = CategorySerializer(many=True, read_only=True)

    class Meta:
        model = Medicine
        fields = [
            "id",
            "name",
            "category",
            "content",
            "image_url",
            "manufacturer",
            "description",
            "price",
            "mrp",
            "stock",
            "is_active",
            "created_at",
            "updated_at",
        ]
