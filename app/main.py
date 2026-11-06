"""
Ponto de entrada da aplicação FastAPI do EchoOrganize.

Registra os roteadores modulares de exploração de arquivos, varredura de biblioteca,
enriquecimento de metadados (MusicBrainz/LRCLib) e processamento em lote com SSE,
além de servir a interface web estática e aplicar políticas de segurança local.
"""

from contextlib import asynccontextmanager
import os
from typing import AsyncGenerator
from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.routes_fs import (
    list_directory_contents,
    list_filesystem_roots,
    router as fs_router,
)
from app.api.routes_jobs import (
    cancel_job,
    get_job_status,
    pause_job,
    process_files,
    resume_job,
    router as jobs_router,
    start_process_job,
    stream_job_events,
)
from app.api.routes_library import (
    generate_library_index,
    preview_safe_path,
    router as library_router,
    scan_directory,
)
from app.api.routes_metadata import (
    fetch_musicbrainz_release_batch,
    fetch_musicbrainz_tags,
    router as metadata_router,
)
from app.config import FRONTEND_DIR, SHUTDOWN_EVENT
from app.schemas import (
    FileTagMapping,
    IndexRequest,
    MusicBrainzQuery,
    PreviewPathRequest,
    ProcessRequest,
    ReleaseBatchItem,
    ReleaseBatchRequest,
    ScanRequest,
)
from app.security import SecurityMiddleware
from app.services.cache import init_db
from app.services.job_manager import get_jobs_store

__all__ = [
    "app",
    "create_app",
    "ScanRequest",
    "PreviewPathRequest",
    "MusicBrainzQuery",
    "ReleaseBatchItem",
    "ReleaseBatchRequest",
    "FileTagMapping",
    "ProcessRequest",
    "IndexRequest",
    "list_filesystem_roots",
    "list_directory_contents",
    "scan_directory",
    "preview_safe_path",
    "generate_library_index",
    "fetch_musicbrainz_tags",
    "fetch_musicbrainz_release_batch",
    "start_process_job",
    "get_job_status",
    "stream_job_events",
    "pause_job",
    "resume_job",
    "cancel_job",
    "process_files",
]

# Referência ao armazenamento em memória dos jobs ativos
_jobs_store = get_jobs_store()

# Favicon SVG Cyberpunk embutido para eliminar erros 404 no navegador
_FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" fill="#07080a"/>'
    '<rect x="2" y="6" width="28" height="20" fill="none" stroke="#fcee09" stroke-width="2"/>'
    '<circle cx="11" cy="16" r="3" fill="none" stroke="#00f0ff" stroke-width="2"/>'
    '<circle cx="21" cy="16" r="3" fill="none" stroke="#00f0ff" stroke-width="2"/>'
    '<path d="M8 22h16" stroke="#fcee09" stroke-width="2"/>'
    "</svg>"
)


@asynccontextmanager
async def _app_lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Gerencia o ciclo de vida da aplicação FastAPI:
    - No startup: limpa o evento de shutdown e garante a criação das tabelas SQLite.
    - No shutdown (CTRL+C): sinaliza `SHUTDOWN_EVENT` e cancela jobs ativos para
      liberar threads de rede/disco imediatamente sem travar o Uvicorn.
    """
    SHUTDOWN_EVENT.clear()
    init_db()
    try:
        yield
    finally:
        SHUTDOWN_EVENT.set()
        for job in list(_jobs_store.values()):
            try:
                job.request_cancel()
            except Exception:
                pass


def create_app() -> FastAPI:
    """
    Factory de criação e configuração da instância FastAPI do EchoOrganize.
    """
    application = FastAPI(
        title="EchoOrganize // Cyberpunk DAP Mastering Deck",
        version=__version__,
        description=(
            "Sanitização determinística de metadados, codificação de capas JPEG Baseline 4:2:0 "
            "e gravação física sequencial em Duas Passadas para sistemas FAT32 embarcados."
        ),
        lifespan=_app_lifespan,
    )

    application.add_middleware(SecurityMiddleware)

    application.include_router(fs_router)
    application.include_router(library_router)
    application.include_router(metadata_router)
    application.include_router(jobs_router)

    if os.path.exists(FRONTEND_DIR):
        application.mount(
            "/static", StaticFiles(directory=FRONTEND_DIR), name="static"
        )

    @application.get("/", include_in_schema=False)
    def read_index() -> FileResponse:
        """Serve a Single-Page Application (SPA) do EchoOrganize."""
        return FileResponse(
            os.path.join(FRONTEND_DIR, "index.html"),
            headers={"Cache-Control": "no-cache"},
        )

    @application.get("/styles.css", include_in_schema=False)
    def read_styles() -> FileResponse:
        """Serve a folha de estilos principal do frontend."""
        return FileResponse(
            os.path.join(FRONTEND_DIR, "styles.css"),
            media_type="text/css",
            headers={"Cache-Control": "no-cache"},
        )

    @application.get("/app.js", include_in_schema=False)
    def read_script() -> FileResponse:
        """Serve o controlador JavaScript principal da SPA."""
        return FileResponse(
            os.path.join(FRONTEND_DIR, "app.js"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache"},
        )

    @application.get("/favicon.ico", include_in_schema=False)
    def read_favicon() -> Response:
        """Serve o ícone vetorial da aplicação sem gerar erro 404."""
        return Response(content=_FAVICON_SVG, media_type="image/svg+xml")

    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
