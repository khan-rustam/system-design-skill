from ..db import execute, query


def get_balance(user_id):
    rows = query("SELECT balance FROM wallets WHERE user_id = %s", (user_id,))
    return rows[0]["balance"] if rows else 0.0


def add_cashback(user_id, amount):
    balance = get_balance(user_id)
    execute("UPDATE wallets SET balance = %s WHERE user_id = %s", (balance + amount, user_id))


def spend(user_id, amount):
    balance = get_balance(user_id)
    if balance < amount:
        raise ValueError("insufficient wallet balance")
    execute("UPDATE wallets SET balance = %s WHERE user_id = %s", (balance - amount, user_id))
