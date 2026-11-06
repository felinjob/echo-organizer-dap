"""
Endpoints REST de exploração nativa do sistema de arquivos (/api/fs/roots e /api/fs/ls).
"""

from typing import Any, Dict
from fastapi import APIRouter, HTTPException, Query

from app.services.filesystem import discover_filesystem_roots, list_directory_safe

router = APIRouter(prefix="/api/fs", tags=["Filesystem Explorer"])


@router.get("/roots")
def list_filesystem_roots() -> Dict[str, Any]:
    """
    Lista unidades de disco disponíveis no Windows (C:\\, D:\\, cartões SD montados)
    e pontos de montagem no Linux/macOS (/media, /mnt, ~),
    incluindo informações de espaço livre e total em disco.
    """
    return discover_filesystem_roots()


@router.get("/ls")
def list_directory_contents(
    path: str = Query(..., description="Caminho absoluto do diretório a explorar"),
    page: int = Query(1, ge=1, description="Página da listagem"),
    page_size: int = Query(200, ge=10, le=1000, description="Itens por página"),
) -> Dict[str, Any]:
    """
    Retorna subpastas diretas de forma rápida, paginada e segura contra Path Traversal,
    além da contagem de arquivos de áudio compatíveis presentes na pasta.
    """
    try:
        return list_directory_safe(raw_path=path, page=page, page_size=page_size)
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err)) from val_err
    except FileNotFoundError as fnf_err:
        raise HTTPException(status_code=404, detail=str(fnf_err)) from fnf_err
    except PermissionError as perm_err:
        raise HTTPException(
            status_code=403,
            detail="Sem permissão de leitura para acessar esta pasta.",
        ) from perm_err
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Erro ao listar diretório: {exc}"
        ) from exc

