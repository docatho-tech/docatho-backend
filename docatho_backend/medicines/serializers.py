from rest_framework import serializers
from docatho_backend.medicines.models import Category


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "image_url", "is_active", "created_at", "updated_at"]
