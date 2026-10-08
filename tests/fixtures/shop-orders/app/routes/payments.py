import hashlib
import hmac
import json
import logging

from flask import Blueprint, abort, current_app, request

from ..db import transaction
from ..services import emailer, paygate, shipping, wallet

bp = Blueprint("payments", __name__)
log = logging.getLogger(__name__)


def _verify(payload, signature):
    secret = current_app.config["PAYGATE_WEBHOOK_SECRET"].encode()
    expected = hmac.new(secret, payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


@bp.post("/webhooks/paygate")
def paygate_webhook():
    payload = request.get_data()
    if not _verify(payload, request.headers.get("X-PayGate-Signature")):
        abort(401)

    event = json.loads(payload)
    if event["type"] != "payment.succeeded":
        return "", 204

    payment = event["data"]
    order_id = int(payment["metadata"]["order_id"])
    amount = payment["amount"] / 100

    with transaction() as cur:
        cur.execute(
            "UPDATE orders SET status = 'paid', paid_at = now() WHERE id = %s RETURNING user_id",
            (order_id,),
        )
        user_id = cur.fetchone()["user_id"]
        cur.execute(
            "INSERT INTO payments (order_id, provider_ref, amount, event_id) VALUES (%s, %s, %s, %s)",
            (order_id, payment["id"], amount, event["id"]),
        )

    wallet.add_cashback(user_id, round(amount * 0.02, 2))
    label = shipping.create_label(order_id)

    try:
        emailer.send_order_confirmation(order_id, label)
    except Exception:
        log.exception("confirmation email failed for order %s, cancelling", order_id)
        with transaction() as cur:
            cur.execute("UPDATE orders SET status = 'failed' WHERE id = %s", (order_id,))
        paygate.refund(payment["id"])

    return "", 204
