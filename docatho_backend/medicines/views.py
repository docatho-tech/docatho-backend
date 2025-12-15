from django.shortcuts import render
from rest_framework import viewsets

from docatho_backend.medicines.models import Category
from docatho_backend.medicines.serializers import CategorySerializer
from rest_framework.pagination import PageNumberPagination


class CategoryPaginaionClass(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100


class CategoryViewset(viewsets.ModelViewSet):

    serializer_class = CategorySerializer
    pagination_class = CategoryPaginaionClass
    queryset = Category.objects.all()
    filterset_fields = ["is_active", "name"]
    search_fields = ["name", "description"]
    ordering_fields = ["created_at", "updated_at", "name"]
