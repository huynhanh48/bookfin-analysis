"""Market data endpoints.

Route handlers are added together with the Market Data Engine; this module only
declares the router prefix at initialization stage.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/market", tags=["market"])
