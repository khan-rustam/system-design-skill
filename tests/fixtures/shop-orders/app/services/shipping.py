import requests
from flask import current_app

from ..db import execute, query


def create_label(order_id):
    order = query(
        "SELECT o.id, o.quantity, a.line1, a.city, a.postcode, a.country "
        "FROM orders o JOIN addresses a ON a.id = o.address_id WHERE o.id = %s",
        (order_id,),
    )[0]
    resp = requests.post(
        current_app.config["CARRIER_API_URL"] + "/labels",
        headers={"Authorization": "Bearer " + current_app.config["CARRIER_API_KEY"]},
        json={
            "reference": str(order["id"]),
            "parcels": order["quantity"],
            "address": {
                "line1": order["line1"],
                "city": order["city"],
                "postcode": order["postcode"],
                "country": order["country"],
            },
        },
    )
    resp.raise_for_status()
    label = resp.json()
    execute(
        "UPDATE orders SET tracking_number = %s, label_url = %s WHERE id = %s",
        (label["tracking_number"], label["label_url"], order_id),
    )
    return label
