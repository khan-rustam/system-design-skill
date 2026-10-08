import hashlib
from functools import wraps

from flask import abort, g, request

from .db import query


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            abort(401)
        token_hash = hashlib.sha256(header[len("Bearer "):].encode()).hexdigest()
        rows = query(
            "SELECT user_id FROM sessions WHERE token_hash = %s AND expires_at > now()",
            (token_hash,),
        )
        if not rows:
            abort(401)
        g.user_id = rows[0]["user_id"]
        return view(*args, **kwargs)

    return wrapper
