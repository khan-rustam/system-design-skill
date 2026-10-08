from dataclasses import dataclass
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import settings

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    subject: str
    role: str  # "service" or "seller"
    seller_id: Optional[int]

    @property
    def seller_scope(self) -> Optional[int]:
        """Sellers see only their own accounts; services are unscoped."""
        return self.seller_id if self.role == "seller" else None


def require_principal(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> Principal:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    try:
        claims = jwt.decode(
            creds.credentials,
            settings.jwt_public_key,
            algorithms=["RS256"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            leeway=30,
            options={"require": ["exp", "sub", "role"]},
        )
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
    role = claims["role"]
    if role not in ("service", "seller"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "unknown role")
    seller_id = claims.get("seller_id")
    if role == "seller" and not isinstance(seller_id, int):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "seller token without seller_id")
    return Principal(subject=claims["sub"], role=role, seller_id=seller_id)


def require_service(principal: Principal = Depends(require_principal)) -> Principal:
    if principal.role != "service":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "service token required")
    return principal
