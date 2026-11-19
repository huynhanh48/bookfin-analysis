"""Bound RAG request bodies before multipart parsing writes temporary files."""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class RagBodyLimitMiddleware:
    """Buffer bounded new-module request bodies only, leaving market streams untouched."""

    def __init__(self, app: ASGIApp, limit: int, prefix: str):
        self.app, self.limit, self.prefix = app, limit, prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Reject oversized fixed/chunked payloads with the existing error envelope."""
        if scope["type"] != "http" or not any(
            scope["path"].startswith(self.prefix + path) for path in ("/rag", "/analysis")
        ):
            await self.app(scope, receive, send)
            return
        from app.core.logging import request_id_var

        async def reject() -> None:
            await JSONResponse(
                {
                    "error": {
                        "code": "payload_too_large",
                        "message": "Request exceeds upload limit.",
                        "details": {},
                        "request_id": request_id_var.get(),
                    }
                },
                status_code=413,
            )(scope, receive, send)

        headers = dict(scope.get("headers", []))
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            length = self.limit + 1
        if length > self.limit:
            await reject()
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.limit:
                await reject()
                return
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay() -> dict:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
