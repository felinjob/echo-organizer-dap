"""
Middleware ASGI de segurança do EchoOrganize.

O servidor tem acesso de leitura/escrita ao sistema de arquivos do usuário, portanto
nenhuma página web de terceiros pode conseguir conversar com ele. Este middleware:

1. Valida o cabeçalho `Host` contra `ALLOWED_HOSTS` (bloqueia DNS Rebinding).
2. Rejeita chamadas `/api/*` disparadas por outras origens (CSRF / leitura cross-site),
   comparando `Origin` com `Host` e respeitando `Sec-Fetch-Site`.
3. Injeta cabeçalhos defensivos (CSP restritiva, anti-clickjacking, nosniff, etc.).

Implementado como ASGI puro para não interferir no streaming SSE.
"""

import json
from typing import Awaitable, Callable, Dict, FrozenSet, List, MutableMapping, Optional, Tuple
from urllib.parse import urlsplit

from app.config import ALLOWED_HOSTS, get_logger

__all__ = ["SecurityMiddleware"]

logger = get_logger(__name__)

Scope = MutableMapping[str, object]
Message = MutableMapping[str, object]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

# Rotas de documentação interativa (Swagger/ReDoc) carregam assets de CDN.
_DOCS_PATHS: Tuple[str, ...] = ("/docs", "/redoc", "/openapi.json")

_CONTENT_SECURITY_POLICY: str = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com data:",
        "img-src 'self' data: https://coverartarchive.org https://*.archive.org",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    )
)

_BASE_SECURITY_HEADERS: Tuple[Tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
)


def _split_host(host_header: str) -> str:
    """Remove a porta do cabeçalho `Host`, preservando literais IPv6 (`[::1]`)."""
    host = host_header.strip().lower()
    if host.startswith("["):
        closing = host.find("]")
        return host[: closing + 1] if closing != -1 else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def _is_host_allowed(host_header: str, allowed: FrozenSet[str]) -> bool:
    """Verifica se o host (sem porta) está na lista de hosts permitidos."""
    if "*" in allowed:
        return True
    host = _split_host(host_header)
    return host in allowed or host.strip("[]") in allowed


class SecurityMiddleware:
    """Middleware ASGI de validação de Host/Origin e cabeçalhos defensivos."""

    def __init__(
        self, app: ASGIApp, allowed_hosts: Optional[FrozenSet[str]] = None
    ) -> None:
        self.app = app
        self.allowed_hosts = allowed_hosts if allowed_hosts is not None else ALLOWED_HOSTS

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers: Dict[str, str] = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])  # type: ignore[union-attr]
        }
        path = str(scope.get("path", ""))
        host_header = headers.get("host", "")

        if not _is_host_allowed(host_header, self.allowed_hosts):
            logger.warning("Requisição bloqueada: Host não permitido (%s)", host_header)
            await self._reject(send, 400, "Host não permitido.")
            return

        if path.startswith("/api/") and not self._is_same_origin(headers, host_header):
            logger.warning("Requisição cross-site bloqueada em %s", path)
            await self._reject(send, 403, "Requisições de outras origens não são permitidas.")
            return

        apply_csp = not path.startswith(_DOCS_PATHS)

        async def send_with_headers(message: Message) -> None:
            if message.get("type") == "http.response.start":
                raw_headers: List[Tuple[bytes, bytes]] = list(
                    message.get("headers", [])  # type: ignore[arg-type]
                )
                raw_headers.extend(_BASE_SECURITY_HEADERS)
                if apply_csp:
                    raw_headers.append(
                        (b"content-security-policy", _CONTENT_SECURITY_POLICY.encode())
                    )
                message["headers"] = raw_headers
            await send(message)

        await self.app(scope, receive, send_with_headers)

    @staticmethod
    def _is_same_origin(headers: Dict[str, str], host_header: str) -> bool:
        """
        Aceita a requisição se o navegador a declarou same-origin ou se não há
        indícios de origem externa (clientes não-navegador como curl/testes).
        """
        fetch_site = headers.get("sec-fetch-site", "")
        if fetch_site and fetch_site not in ("same-origin", "none"):
            return False
        origin = headers.get("origin", "")
        if origin == "null":
            return False
        if not origin:
            return True
        return urlsplit(origin).netloc.lower() == host_header.strip().lower()

    @staticmethod
    async def _reject(send: Send, status: int, detail: str) -> None:
        """Emite uma resposta JSON de erro sem acionar a aplicação."""
        body = json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                    *_BASE_SECURITY_HEADERS,
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
