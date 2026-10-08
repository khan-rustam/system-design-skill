import smtplib
from email.message import EmailMessage

from flask import current_app

from ..db import query


def send_order_confirmation(order_id, label):
    row = query(
        "SELECT u.email, o.total FROM orders o JOIN users u ON u.id = o.user_id WHERE o.id = %s",
        (order_id,),
    )[0]
    msg = EmailMessage()
    msg["From"] = current_app.config["SMTP_FROM"]
    msg["To"] = row["email"]
    msg["Subject"] = "Order #%d confirmed" % order_id
    msg.set_content(
        "Thanks for your order! Total: $%.2f\nTracking number: %s"
        % (row["total"], label["tracking_number"])
    )
    with smtplib.SMTP(current_app.config["SMTP_HOST"]) as smtp:
        smtp.send_message(msg)
