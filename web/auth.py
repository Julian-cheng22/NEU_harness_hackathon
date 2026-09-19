"""Optional shared password for a trusted-LAN demo, including static files.

HTTP Basic authentication is not encryption: use HTTPS on untrusted networks.
Pure ASGI middleware leaves the live SSE response unbuffered.
"""

import base64
import binascii
import os
import secrets

from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse


class DashboardPassword:
    def __init__(self, app, password: str | None = None):
        self.app = app
        # Snapshot once at server startup, including a password entered by main().
        self.password = (os.getenv("DASHBOARD_PASSWORD", "")
                         if password is None else password).encode("utf-8")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.password:
            await self.app(scope, receive, send)
            return

        authorization = Headers(scope=scope).get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        username, password = b"", b""
        if scheme.lower() == "basic":
            try:
                decoded = base64.b64decode(token, validate=True)
                username, _, password = decoded.partition(b":")
            except (ValueError, binascii.Error):
                pass
        valid_user = secrets.compare_digest(username, b"teammate")
        valid_password = secrets.compare_digest(password, self.password)
        if not (valid_user and valid_password):
            response = PlainTextResponse(
                "Dashboard login required. Username: teammate",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Harness dashboard", charset="UTF-8"',
                         "Cache-Control": "no-store"},
            )
            await response(scope, receive, send)
            return

        async def private_send(message):
            if message["type"] == "http.response.start":
                message = dict(message)
                message["headers"] = [
                    (key, value) for key, value in message.get("headers", [])
                    if key.lower() != b"cache-control"
                ] + [(b"cache-control", b"no-store")]
            await send(message)

        await self.app(scope, receive, private_send)
