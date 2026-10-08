import requests
from flask import current_app

API = "https://api.paygate.example/v1"


def _headers():
    return {"Authorization": "Bearer " + current_app.config["PAYGATE_API_KEY"]}


def create_checkout(order_id, total, customer_email):
    resp = requests.post(
        API + "/checkouts",
        headers=_headers(),
        timeout=10,
        json={
            "amount": int(total * 100),
            "currency": "usd",
            "metadata": {"order_id": order_id},
            "customer_email": customer_email,
        },
    )
    resp.raise_for_status()
    return resp.json()["url"]


def refund(payment_ref):
    resp = requests.post(
        API + "/refunds",
        headers=_headers(),
        timeout=10,
        json={"payment": payment_ref},
    )
    resp.raise_for_status()
