import asyncpg

from .config import settings

# Every connection gets server-side deadlines, so a stuck query or a client
# that forgets to commit can never hold locks or a pool slot indefinitely.
SERVER_SETTINGS = {
    "statement_timeout": "10000",
    "lock_timeout": "5000",
    "idle_in_transaction_session_timeout": "30000",
    "application_name": "ledger",
}


async def create_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(
        dsn=str(settings.database_url),
        min_size=2,
        max_size=settings.db_pool_max,
        timeout=5,  # connect timeout
        command_timeout=15,
        max_inactive_connection_lifetime=300,
        server_settings=SERVER_SETTINGS,
    )
