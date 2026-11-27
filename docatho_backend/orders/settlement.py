"""The side effects a pharmacy order owes once its money is actually in.

Either path can be the first — or the only — one to learn an order is paid:
`confirm-payment` runs on a client that may lose the network right after the
gateway sheet closes, and the webhook arrives independently. Previously only
the view reserved stock and emptied the cart, so a patient whose confirm
dropped kept a full cart, checked out again and paid twice while the pharmacy
oversold unreserved stock. Both paths now call this.
"""

from docatho_backend.cart.models import Cart
from docatho_backend.notifications.models import Notification
from docatho_backend.notifications.models import NotificationType
from docatho_backend.notifications.services import notify

from .models import Order


def settle_paid_order(order: Order) -> None:
    """Reserve stock, empty the cart and tell the patient. Idempotent."""
    try:
        order.reserve_stock()  # no-ops if already reserved
    except ValueError:
        # Payment is already captured, so flag the shortage for ops rather
        # than failing the caller.
        order.update_status(
            Order.Status.PROCESSING,
            notes="Stock shortage after payment; needs manual review.",
        )

    cart = Cart.objects.filter(user=order.user).first()
    if cart and cart.items.exists():
        cart.clear()

    # One payment is one push, whichever path (or both) got here.
    already_told = Notification.objects.filter(
        recipient=order.user,
        notification_type=NotificationType.PAYMENT,
        order=order,
    ).exists()
    if not already_told:
        notify(
            order.user,
            NotificationType.PAYMENT,
            "Payment received",
            f"We received your payment for order {order.order_number}.",
            order=order,
        )
