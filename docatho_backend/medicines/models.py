from os import name
from django.db import models
from django.utils.text import slugify
from decimal import Decimal

from docatho_backend.masters.models import BaseModel


class Category(BaseModel):
    name = models.CharField(max_length=255)
    image_url = models.URLField(blank=True, null=True)
    is_active = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Medicine(BaseModel):
    name = models.CharField(max_length=255)
    category = models.ManyToManyField(Category, related_name="medicines")
    content = models.TextField(blank=True, null=True)
    image_url = models.URLField(blank=True, null=True)
    manufacturer = models.CharField(max_length=255, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    price = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    stock = models.PositiveIntegerField(default=0)
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    mrp = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
