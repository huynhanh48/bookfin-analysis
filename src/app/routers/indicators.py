"""Technical indicator endpoints.

Route handlers are added together with the indicator services; this module only
declares the router prefix at initialization stage.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/indicators", tags=["indicators"])
