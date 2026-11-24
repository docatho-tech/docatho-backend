"""Returning a cancelled order's money.

``Order.PaymentStatus.REFUNDED`` shipped as a choice no code ever set. Every
path that frees an order's stock — a customer cancelling, a pharmacy rejecting,
a return — left a patient who had already paid online charged in full, with the
only remedy a manual refund in the Razorpay dashboard that nobody was asked to
make. :func:`refund_order` is called from ``Order.update_status`` for all three.

COD orders have taken no money, so there is nothing to return.

A gateway failure must not undo the cancellation — the order is cancelled, the
stock is back, and the money is the part that still needs a human. So a failure
is written to the order's log (which the admin portal shows) and swallowed.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction

from .models import Order
from .models import OrderLog
from .models import Transaction
from .razorpay import RazorpayClient

logger = logging.getLogger(__name__)


def _log(order: Order, message: str, **meta) -> None:
    try:
        OrderLog.objects.create(order=order, message=message, meta=meta or None)
    except Exception:  # logging must never break a cancellation
        logger.exception("Could not write refund log for order %s", order.pk)


def _capture_to_refund(order: Order) -> Transaction | None:
    """The succeeded payment this order's money actually came in on."""
    return (
        order.transactions.filter(succeeded=True)
        .exclude(razorpay_payment_id__isnull=True)
        .exclude(razorpay_payment_id="")
        .order_by("-paid_at")
        .first()
    )


def refund_order(order_id: int, reason: str = "order_cancelled") -> Transaction | None:
    """Refund a cancelled/rejected/returned online order in full.

    Idempotent and safe against two cancellations racing: the order row is
    locked for the duration and the paid-check is re-read under that lock, so
    the second caller sees ``refunded`` and does nothing.

    Returns the Transaction the refund was recorded against, or ``None`` when
    there was nothing to refund.

    ponytail: the row lock is held across the gateway call (~1s). Fine for one
    order at a time; move to a queued refund job if cancellations ever burst.
    """
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        if order is None:
            return None
        if order.payment_method != Order.PaymentMethod.ONLINE:
            return None
        # Anything other than PAID means there is no captured money to return,
        # and REFUNDED means a previous call already returned it.
        if order.payment_status != Order.PaymentStatus.PAID:
            return None

        tr = _capture_to_refund(order)
        if tr is None:
            _log(
                order,
                "Refund NOT initiated: order is marked paid but no successful "
                "Razorpay payment is recorded against it. Needs manual review.",
                reason=reason,
            )
            return None

        amount = Decimal(tr.amount or order.total)
        try:
            payload = RazorpayClient().refund(
                tr.razorpay_payment_id,
                amount,
                notes={
                    "order_id": str(order.pk),
                    "order_number": order.order_number,
                    "reason": reason,
                },
            )
        except Exception as exc:
            logger.exception("Refund failed for order %s", order.pk)
            _log(
                order,
                f"Refund of {amount} FAILED at the gateway: {exc}. "
                "The patient is still charged — refund manually.",
                reason=reason,
                razorpay_payment_id=tr.razorpay_payment_id,
                amount=str(amount),
            )
            return None

        tr.raw_response = (tr.raw_response or {}) | {"refund": payload}
        tr.save(update_fields=["raw_response", "updated_at"])

        order.payment_status = Order.PaymentStatus.REFUNDED
        order.save(update_fields=["payment_status", "updated_at"])

        _log(
            order,
            f"Refund of {amount} initiated (razorpay refund "
            f"{payload.get('id')}, status {payload.get('status')}).",
            reason=reason,
            amount=str(amount),
        )
        return tr
