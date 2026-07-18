from rest_framework import status
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from docatho_backend.orders.paginators import GenericPaginationClass

from .models import Notification
from .serializers import NotificationSerializer


class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    """The current user's notifications.

    * GET  /api/notifications/                 - list (own only)
    * GET  /api/notifications/{pk}/            - retrieve (own only)
    * GET  /api/notifications/unread_count/    - unread badge count
    * POST /api/notifications/{pk}/mark_read/  - mark one read
    * POST /api/notifications/mark_all_read/   - mark all read
    """

    serializer_class = NotificationSerializer
    permission_classes = (IsAuthenticated,)
    pagination_class = GenericPaginationClass
    filterset_fields = ["is_read", "notification_type"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Notification.objects.none()
        return Notification.objects.filter(recipient=self.request.user)

    @action(detail=False, methods=["get"], url_path="unread_count")
    def unread_count(self, request):
        count = self.get_queryset().filter(is_read=False).count()
        return Response({"unread": count})

    @action(detail=True, methods=["post"], url_path="mark_read")
    def mark_read(self, request, pk=None):
        notification = self.get_queryset().filter(pk=pk).first()
        if notification is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if not notification.is_read:
            notification.is_read = True
            notification.save(update_fields=["is_read", "updated_at"])
        return Response(NotificationSerializer(notification).data)

    @action(detail=False, methods=["post"], url_path="mark_all_read")
    def mark_all_read(self, request):
        updated = self.get_queryset().filter(is_read=False).update(is_read=True)
        return Response({"marked_read": updated})
