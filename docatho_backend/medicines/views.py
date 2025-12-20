from django.shortcuts import render
from rest_framework import viewsets

from docatho_backend.medicines.models import Category, Medicine
from docatho_backend.medicines.serializers import CategorySerializer, MedicineSerializer
from rest_framework.pagination import PageNumberPagination
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters


class GenericPaginationClass(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100


class CategoryViewset(viewsets.ModelViewSet):

    serializer_class = CategorySerializer
    pagination_class = GenericPaginationClass
    queryset = Category.objects.all()
    filterset_fields = ["is_active", "name"]
    search_fields = ["name", "description"]
    ordering_fields = ["created_at", "updated_at", "name"]


class MedicineViewset(viewsets.ModelViewSet):
    serializer_class = MedicineSerializer
    pagination_class = GenericPaginationClass
    queryset = Medicine.objects.all()
    filter_backends = (DjangoFilterBackend, filters.SearchFilter)

    filterset_fields = ["is_active", "name", "category"]
    search_fields = ["name", "manufacturer", "description"]
    ordering_fields = ["created_at", "updated_at", "name", "price"]


class AdminMedicineViewset(viewsets.ModelViewSet):
    serializer_class = MedicineSerializer
    pagination_class = GenericPaginationClass
    queryset = Medicine.objects.all()
    filter_backends = (DjangoFilterBackend, filters.SearchFilter)

    filterset_fields = ["is_active", "name", "category"]
    search_fields = ["name", "manufacturer", "description"]
    ordering_fields = ["created_at", "updated_at", "name", "price"]
