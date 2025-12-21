from decimal import Decimal

from django.shortcuts import render, get_object_or_404

from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from docatho_backend.medicines.models import Medicine
from .models import Cart, CartItem
from docatho_backend.cart.serializers import (
    CartSerializer,
    CartItemSerializer,
    MedicineLiteSerializer,
)


class CartViewSet(viewsets.ViewSet):
    """
    Simple Cart API:
    - GET    /api/cart/            -> current open cart
    - POST   /api/cart/add/        -> add item {medicine_id, quantity}
    - PATCH  /api/cart/update/     -> update item quantity {medicine_id, quantity}
    - POST   /api/cart/remove/     -> remove item {medicine_id}
    """

    permission_classes = (IsAuthenticated,)

    def _get_open_cart(self, user):
        cart, _ = Cart.objects.get_or_create(user=user)
        return cart

    def list(self, request):
        # return current cart (list endpoint used for simplicity)
        cart = self._get_open_cart(request.user)
        serializer = CartSerializer(cart, context={"request": request})
        return Response(serializer.data)

    def retrieve(self, request, pk=None):
        # allow retrieving by id if needed
        cart = get_object_or_404(Cart, pk=pk, user=request.user)
        serializer = CartSerializer(cart, context={"request": request})
        return Response(serializer.data)

    # api for cart items count
    @action(detail=False, methods=["get"])
    def get_cart_items_count(self, request):
        cart = self._get_open_cart(request.user)
        return Response({"count": cart.items.count()})

    @action(detail=False, methods=["post"])
    def add(self, request):
        medicine_id = request.data.get("medicine_id")
        quantity = int(request.data.get("quantity", 1) or 1)
        print(medicine_id, quantity)
        if not medicine_id:
            return Response(
                {"detail": "medicine_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        medicine = get_object_or_404(Medicine, pk=medicine_id)
        cart = self._get_open_cart(request.user)
        try:
            item = cart.add_item(medicine, quantity=quantity)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        serializer = CartSerializer(cart, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["patch"], url_path="update-item")
    def update_item(self, request):
        """
        Update quantity for an item in the cart.
        PATCH /api/cart/update-item/  { medicine_id, quantity }
        """
        medicine_id = request.data.get("medicine_id")
        if not medicine_id:
            return Response(
                {"detail": "medicine_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            quantity = int(request.data.get("quantity", 1))
        except Exception:
            return Response(
                {"detail": "quantity must be an integer"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        medicine = get_object_or_404(Medicine, pk=medicine_id)
        cart = self._get_open_cart(request.user)
        print(medicine, quantity)
        item = cart.update_item_quantity(medicine, quantity)
        if item is None:
            return Response(
                {"detail": "item not found in cart"}, status=status.HTTP_404_NOT_FOUND
            )
        serializer = CartSerializer(cart, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="remove-item")
    def remove_item(self, request):
        """
        Remove an item from the cart.
        POST /api/cart/remove-item/  { medicine_id }
        """
        medicine_id = request.data.get("medicine_id")
        if not medicine_id:
            return Response(
                {"detail": "medicine_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        medicine = get_object_or_404(Medicine, pk=medicine_id)
        cart = self._get_open_cart(request.user)
        cart.remove_item(medicine)
        serializer = CartSerializer(cart, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)
