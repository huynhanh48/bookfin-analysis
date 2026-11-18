"""Middleware gắn request id cho mỗi request.

Request id lấy từ header `X-Request-ID` nếu client gửi, ngược lại sinh mới. Id
được đặt vào `request_id_var` để mọi bản ghi log trong cùng request đều kèm theo,
và được trả lại trong response header để client đối chiếu.
"""

import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import request_id_var

REQUEST_ID_HEADER = "X-Request-ID"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Gắn và lan truyền request id cho từng request."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Xử lý một request với request id tương ứng.

        Args:
            request: Request đến từ client.
            call_next: Hàm gọi tầng xử lý phía trong.

        Returns:
            Response của ứng dụng, đã có header `X-Request-ID`.
        """
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = incoming.strip() if incoming and incoming.strip() else uuid.uuid4().hex

        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)

        response.headers[REQUEST_ID_HEADER] = request_id
        return response
