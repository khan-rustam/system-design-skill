from flask import Blueprint, abort, g, jsonify, request

from ..auth import login_required
from ..db import query, transaction
from ..services import paygate

bp = Blueprint("orders", __name__)


@bp.post("/orders")
@login_required
def create_order():
    body = request.get_json(force=True)
    product_id = int(body["product_id"])
    address_id = int(body["address_id"])
    qty = int(body["quantity"])
    if qty < 1 or qty > 20:
        abort(400, "quantity must be between 1 and 20")

    address = query(
        "SELECT id FROM addresses WHERE id = %s AND user_id = %s", (address_id, g.user_id)
    )
    if not address:
        abort(404)

    product = query("SELECT id, name, price, stock FROM products WHERE id = %s", (product_id,))
    if not product:
        abort(404)
    product = product[0]
    if product["stock"] < qty:
        abort(409, "out of stock")

    total = float(product["price"]) * qty

    with transaction() as cur:
        cur.execute(
            "UPDATE products SET stock = %s WHERE id = %s",
            (product["stock"] - qty, product_id),
        )
        cur.execute(
            "INSERT INTO orders (user_id, product_id, address_id, quantity, total, status) "
            "VALUES (%s, %s, %s, %s, %s, 'pending_payment') RETURNING id",
            (g.user_id, product_id, address_id, qty, total),
        )
        order_id = cur.fetchone()["id"]

    email = query("SELECT email FROM users WHERE id = %s", (g.user_id,))[0]["email"]
    checkout_url = paygate.create_checkout(order_id, total, email)
    return jsonify({"order_id": order_id, "total": total, "checkout_url": checkout_url}), 201


@bp.get("/orders")
@login_required
def list_orders():
    rows = query(
        "SELECT o.id, o.quantity, o.total, o.status, o.tracking_number, o.created_at, p.name "
        "FROM orders o JOIN products p ON p.id = o.product_id "
        "WHERE o.user_id = %s ORDER BY o.created_at DESC",
        (g.user_id,),
    )
    return jsonify(rows)


@bp.get("/orders/<int:order_id>")
@login_required
def get_order(order_id):
    rows = query(
        "SELECT id, product_id, quantity, total, status, tracking_number, label_url, created_at "
        "FROM orders WHERE id = %s AND user_id = %s",
        (order_id, g.user_id),
    )
    if not rows:
        abort(404)
    return jsonify(rows[0])
