import hashlib
import hmac
import json
from decimal import Decimal
from typing import Any

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.encoding import force_bytes

from .models import Order
from .models import Transaction

RAZORPAY_API_BASE = "https://api.razorpay.com/v1"


class RazorpayClient:
    def __init__(self, key_id: str | None = None, key_secret: str | None = None):
        self.key_id = key_id or settings.RAZORPAY_KEY_ID  # set in settings
        self.key_secret = key_secret or settings.RAZORPAY_KEY_SECRET

    def _auth(self):
        return (self.key_id, self.key_secret)

    def create_order(
        self,
        order: Order,
        receipt: str | None = None,
        notes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Create a Razorpay order for the provided Order model.
        Saves a Transaction row with transaction_order_id (razorpay order id).
        Amount must be in paisa (integer).
        """
        amount_paisa = (
            int((order.total * 100).to_integral_value())
            if hasattr(order.total, "quantize")
            else int(order.total * 100)
        )
        payload = {
            "amount": amount_paisa,
            "currency": "INR",
            "receipt": receipt or order.order_number,
            "payment_capture": 1,
            "notes": notes
            or {"order_id": str(order.pk), "user_id": str(order.user_id)},
        }
        resp = requests.post(
            f"{RAZORPAY_API_BASE}/orders",
            auth=self._auth(),
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        # persist transaction record
        Transaction.objects.create(
            order=order,
            provider="razorpay",
            transaction_order_id=data.get("id"),
            amount=order.total,
            succeeded=False,
            raw_response=data,
        )
        return data

    def refund(
        self,
        razorpay_payment_id: str,
        amount: Decimal,
        notes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return ``amount`` of a captured payment to the payer.

        Amount is sent in paisa, like every other figure the gateway takes.
        Raises on a non-2xx so the caller can record the failure.
        """
        amount_paisa = int((Decimal(amount) * 100).to_integral_value())
        resp = requests.post(
            f"{RAZORPAY_API_BASE}/payments/{razorpay_payment_id}/refund",
            auth=self._auth(),
            json={
                "amount": amount_paisa,
                "speed": "normal",
                "notes": notes or {},
            },
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _verify_signature(message: bytes, signature: str, secret: str) -> bool:
        # A blank secret still produces a valid HMAC, one anybody can compute,
        # so an unconfigured deployment would accept forged signatures and mark
        # orders paid. Every signature check routes through here: refuse.
        if not secret:
            return False
        mac = hmac.new(force_bytes(secret), msg=message, digestmod=hashlib.sha256)
        expected = mac.hexdigest()
        return hmac.compare_digest(expected, signature)

    def verify_payment_signature(
        self,
        razorpay_order_id: str,
        razorpay_payment_id: str,
        razorpay_signature: str,
    ) -> bool:
        """
        Verify signature returned by client after checkout:
        hmac_sha256(order_id + "|" + payment_id, key_secret)
        """
        if not (razorpay_order_id and razorpay_payment_id and razorpay_signature):
            return False
        msg = f"{razorpay_order_id}|{razorpay_payment_id}".encode()
        return self._verify_signature(msg, razorpay_signature, self.key_secret)

    @transaction.atomic
    def confirm_payment(
        self,
        razorpay_order_id: str,
        razorpay_payment_id: str,
        razorpay_signature: str | None = None,
        raw_response: dict[str, Any] | None = None,
        user=None,
    ) -> Transaction:
        """
        Mark a Transaction as succeeded after verifying signature.
        Updates linked Order.payment_status to PAID.
        Raises ValueError on verification failure or if matching transaction/order not found.

        ``user`` scopes the lookup to that account's own orders. The gateway
        triple is not a secret between patients — without the filter, anyone
        holding one got the other patient's order back, name, phone, address
        and medicines included. Only the webhook (no user) may go unscoped.
        """
        qs = Transaction.objects.select_for_update().filter(
            transaction_order_id=razorpay_order_id,
        )
        if user is not None:
            qs = qs.filter(order__user=user)
        tr = qs.first()
        if tr is None:
            raise ValueError("No Transaction found for given razorpay_order_id")

        # Unconditional: an absent signature must fail, never skip the check.
        ok = self.verify_payment_signature(
            razorpay_order_id,
            razorpay_payment_id,
            razorpay_signature or "",
        )
        if not ok:
            # record failure
            tr.raw_response = (raw_response or {}) | {"signature_verified": False}
            tr.succeeded = False
            tr.razorpay_payment_id = razorpay_payment_id
            tr.razorpay_signature = razorpay_signature
            tr.save(
                update_fields=[
                    "raw_response",
                    "succeeded",
                    "razorpay_payment_id",
                    "razorpay_signature",
                    "updated_at",
                ],
            )
            raise ValueError("Invalid signature")

        # mark success
        tr.razorpay_payment_id = razorpay_payment_id
        tr.razorpay_signature = razorpay_signature
        tr.succeeded = True
        tr.paid_at = timezone.now()
        if raw_response is not None:
            tr.raw_response = raw_response
        tr.save(
            update_fields=[
                "razorpay_payment_id",
                "razorpay_signature",
                "succeeded",
                "paid_at",
                "raw_response",
                "updated_at",
            ],
        )

        # update order
        order = tr.order
        order.payment_status = order.PaymentStatus.PAID
        order.status = order.Status.CONFIRMED
        order.save(update_fields=["payment_status", "status", "updated_at"])

        return tr

    def handle_webhook(
        self,
        body_bytes: bytes,
        signature_header: str,
    ) -> dict[str, Any]:
        """
        Verify webhook using RAZORPAY_WEBHOOK_SECRET and return parsed payload.
        Additionally updates Transaction/Order for payment events (payment.captured / payment.failed).
        Returns parsed JSON on success. Raises ValueError if signature invalid.
        """
        webhook_secret = getattr(settings, "RAZORPAY_WEBHOOK_SECRET", None)
        if not webhook_secret:
            raise ValueError("Webhook secret not configured")

        valid = self._verify_signature(body_bytes, signature_header, webhook_secret)
        if not valid:
            raise ValueError("Invalid webhook signature")

        payload = json.loads(body_bytes.decode("utf-8"))
        event = payload.get("event")
        entity = payload.get("payload", {})

        # handle payment captured / failed
        if event in ("payment.captured", "payment.failed", "payment.authorized"):
            payment_entity = entity.get("payment", {}).get("entity") or {}
            rp_payment_id = payment_entity.get("id")
            rp_order_id = payment_entity.get("order_id")
            amount = payment_entity.get("amount")
            # find matching transaction
            tr = None
            if rp_order_id:
                tr = Transaction.objects.filter(
                    transaction_order_id=rp_order_id,
                ).first()
            if not tr and rp_payment_id:
                tr = Transaction.objects.filter(
                    razorpay_payment_id=rp_payment_id,
                ).first()

            # Appointments keep their ledger in AppointmentPaymentTransaction,
            # so every appointment payment used to reach the acknowledge-and-
            # ignore branch below and nothing reconciled it: confirm-payment
            # runs on a 10s client timeout, so a confirm that dropped after
            # Razorpay captured the money left the appointment pending forever
            # with the patient charged. This is the reconciliation.
            # Only captures are reconciled here — a late `payment.failed` from
            # an abandoned earlier attempt must not unpay a consultation the
            # patient went on to pay for, exactly as it must not for an order —
            # so failures fall through to the no-op below.
            if tr is None and event == "payment.captured":
                # Imported here, not at module scope: healthcare's payment
                # helpers import this module, so a top-level import is circular.
                from docatho_backend.healthcare.appointment_payments import (
                    settle_appointment_payment,
                )
                from docatho_backend.healthcare.models import (
                    AppointmentPaymentTransaction,
                )

                appt_tr = None
                if rp_order_id:
                    appt_tr = AppointmentPaymentTransaction.objects.filter(
                        transaction_order_id=rp_order_id,
                    ).first()
                if appt_tr is None:
                    # `create_appointment_checkout` stamps `appointment_id` into
                    # the order's notes, which Razorpay echoes on the payment.
                    appointment_id = (payment_entity.get("notes") or {}).get(
                        "appointment_id",
                    )
                    if appointment_id:
                        appt_tr = (
                            AppointmentPaymentTransaction.objects.filter(
                                appointment_id=appointment_id,
                                succeeded=False,
                            )
                            .order_by("-created_at")
                            .first()
                        )
                if appt_tr is not None:
                    settle_appointment_payment(
                        appt_tr,
                        rp_payment_id or "",
                        raw_response=payment_entity,
                    )
                    return payload

            # No Transaction row means this payment is not a pharmacy order —
            # appointment checkouts keep their own ledger. Acknowledge it rather
            # than inventing a Transaction with no Order (the FK is NOT NULL, so
            # that raised and Razorpay retried the delivery forever).
            if tr:
                tr.razorpay_payment_id = rp_payment_id or tr.razorpay_payment_id
                tr.amount = (Decimal(amount) / 100) if amount else tr.amount
                tr.raw_response = payment_entity
                if event == "payment.captured":
                    tr.succeeded = True
                    tr.paid_at = timezone.now()
                elif event == "payment.failed":
                    tr.succeeded = False
                # payment.authorized leaves `succeeded` alone: events can arrive
                # out of order, and it must not undo a capture already recorded.
                tr.save()

            # update order status
            if tr and tr.order:
                order = tr.order
                already_paid = order.payment_status == order.PaymentStatus.PAID
                if event == "payment.captured":
                    order.payment_status = order.PaymentStatus.PAID
                    order.status = order.Status.CONFIRMED
                    order.save(
                        update_fields=["payment_status", "status", "updated_at"],
                    )
                    # Imported here, not at module scope: views imports this
                    # module, and settlement reaches back into orders.
                    from .settlement import settle_paid_order

                    settle_paid_order(order)
                elif event == "payment.failed" and not already_paid:
                    # A late failure from an earlier attempt must not unpay an
                    # order the patient went on to pay for.
                    order.payment_status = order.PaymentStatus.FAILED
                    order.save(update_fields=["payment_status", "updated_at"])

        return payload
