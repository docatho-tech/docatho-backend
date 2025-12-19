import hmac
import hashlib
import json
from typing import Any, Dict, Optional

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.encoding import force_bytes

from .models import Order, Transaction


RAZORPAY_API_BASE = "https://api.razorpay.com/v1"


class RazorpayClient:
    def __init__(self, key_id: Optional[str] = None, key_secret: Optional[str] = None):
        self.key_id = key_id or settings.RAZORPAY_KEY_ID  # set in settings
        self.key_secret = key_secret or settings.RAZORPAY_KEY_SECRET

    def _auth(self):
        return (self.key_id, self.key_secret)

    def create_order(
        self,
        order: Order,
        receipt: Optional[str] = None,
        notes: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
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
            f"{RAZORPAY_API_BASE}/orders", auth=self._auth(), json=payload, timeout=15
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

    @staticmethod
    def _verify_signature(message: bytes, signature: str, secret: str) -> bool:
        mac = hmac.new(force_bytes(secret), msg=message, digestmod=hashlib.sha256)
        expected = mac.hexdigest()
        return hmac.compare_digest(expected, signature)

    def verify_payment_signature(
        self, razorpay_order_id: str, razorpay_payment_id: str, razorpay_signature: str
    ) -> bool:
        """
        Verify signature returned by client after checkout:
        hmac_sha256(order_id + "|" + payment_id, key_secret)
        """
        if not (razorpay_order_id and razorpay_payment_id and razorpay_signature):
            return False
        msg = f"{razorpay_order_id}|{razorpay_payment_id}".encode("utf-8")
        return self._verify_signature(msg, razorpay_signature, self.key_secret)

    @transaction.atomic
    def confirm_payment(
        self,
        razorpay_order_id: str,
        razorpay_payment_id: str,
        razorpay_signature: Optional[str] = None,
        raw_response: Optional[Dict[str, Any]] = None,
    ) -> Transaction:
        """
        Mark a Transaction as succeeded after verifying signature.
        Updates linked Order.payment_status to PAID.
        Raises ValueError on verification failure or if matching transaction/order not found.
        """
        tr = (
            Transaction.objects.select_for_update()
            .filter(transaction_order_id=razorpay_order_id)
            .first()
        )
        if tr is None:
            raise ValueError("No Transaction found for given razorpay_order_id")

        if razorpay_signature:
            ok = self.verify_payment_signature(
                razorpay_order_id, razorpay_payment_id, razorpay_signature
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
                    ]
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
            ]
        )

        # update order
        order = tr.order
        order.payment_status = order.PaymentStatus.PAID
        order.status = order.Status.CONFIRMED
        order.save(update_fields=["payment_status", "status", "updated_at"])

        return tr

    def handle_webhook(
        self, body_bytes: bytes, signature_header: str
    ) -> Dict[str, Any]:
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
                    transaction_order_id=rp_order_id
                ).first()
            if not tr and rp_payment_id:
                tr = Transaction.objects.filter(
                    razorpay_payment_id=rp_payment_id
                ).first()
            # create or update transaction record
            if tr is None and rp_order_id:
                # try to attach to order by razorpay order id -> find order
                order = Order.objects.filter(
                    order_number=rp_order_id
                ).first()  # fallback; normally transaction_order_id stores rp order id
                tr = Transaction.objects.create(
                    order=order,
                    provider="razorpay",
                    transaction_order_id=rp_order_id,
                    razorpay_payment_id=rp_payment_id,
                    amount=(Decimal(amount) / 100) if amount else Decimal("0.00"),
                    succeeded=(event == "payment.captured"),
                    raw_response=payment_entity,
                )
            elif tr:
                tr.razorpay_payment_id = rp_payment_id or tr.razorpay_payment_id
                tr.amount = (Decimal(amount) / 100) if amount else tr.amount
                tr.raw_response = payment_entity
                tr.succeeded = event == "payment.captured"
                if tr.succeeded:
                    tr.paid_at = timezone.now()
                tr.save()

            # update order status
            if tr and tr.order:
                if event == "payment.captured":
                    tr.order.payment_status = tr.order.PaymentStatus.PAID
                    tr.order.status = tr.order.Status.CONFIRMED
                elif event == "payment.failed":
                    tr.order.payment_status = tr.order.PaymentStatus.FAILED
                tr.order.save(update_fields=["payment_status", "status", "updated_at"])

        return payload
