"""
Endpoints REST e SSE de execução de Jobs em Lote (/api/jobs/* e /api/process).
"""

import os
from typing import Any, Dict
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

import app.services.fat32_writer as tagger_service
from app.schemas import ProcessRequest
from app.services.job_manager import (
    create_and_start_job,
    get_job_snapshot,
    iter_job_sse_events,
    prepare_files_mapping,
    transition_job_state,
)

router = APIRouter(prefix="/api", tags=["Batch Processing & SSE Jobs"])


@router.post("/jobs/process")
def start_process_job(request: ProcessRequest) -> Dict[str, Any]:
    """
    Inicia um Background Job para gravar fisicamente a fila inteira de arquivos selecionados
    em uma única chamada, transmitindo eventos em tempo real via Server-Sent Events (SSE).
    """
    if not request.dest_dir or not request.dest_dir.strip():
        raise HTTPException(status_code=400, detail="Diretório de destino é obrigatório.")
    if not request.mappings:
        raise HTTPException(status_code=400, detail="Nenhum arquivo enviado para processamento.")

    dest_dir = os.path.abspath(os.path.expanduser(request.dest_dir.strip()))
    try:
        os.makedirs(dest_dir, exist_ok=True)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Não foi possível criar o diretório de destino: {exc}",
        ) from exc

    return create_and_start_job(
        dest_dir=dest_dir,
        mappings=request.mappings,
        generate_index=bool(request.generate_index),
    )


@router.get("/jobs/{job_id}")
def get_job_status(job_id: str) -> Dict[str, Any]:
    """Retorna o snapshot atual de progresso e estado de um Job."""
    snapshot = get_job_snapshot(job_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Job não encontrado.")
    return snapshot


@router.get("/jobs/{job_id}/stream")
async def stream_job_events(job_id: str) -> StreamingResponse:
    """
    Canal Server-Sent Events (SSE) para acompanhamento em tempo real do lote
    (álbum atual, faixa atual, Passada 1 Áudio vs Passada 2 .lrc, percentual e logs).
    """
    if get_job_snapshot(job_id) is None:
        raise HTTPException(status_code=404, detail="Job não encontrado.")

    return StreamingResponse(
        iter_job_sse_events(job_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/jobs/{job_id}/pause")
def pause_job(job_id: str) -> Dict[str, Any]:
    """Pausa a execução do lote entre a gravação de um arquivo e outro."""
    result = transition_job_state(job_id, "pause")
    if result is None:
        raise HTTPException(status_code=404, detail="Job não encontrado.")
    return result


@router.post("/jobs/{job_id}/resume")
def resume_job(job_id: str) -> Dict[str, Any]:
    """Retoma a execução de um lote pausado."""
    result = transition_job_state(job_id, "resume")
    if result is None:
        raise HTTPException(status_code=404, detail="Job não encontrado.")
    return result


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> Dict[str, Any]:
    """Cancela a execução do lote e aciona a limpeza de arquivos temporários (.tmp)."""
    result = transition_job_state(job_id, "cancel")
    if result is None:
        raise HTTPException(status_code=404, detail="Job não encontrado.")
    return result


@router.post("/process")
def process_files(request: ProcessRequest) -> Dict[str, Any]:
    """
    Endpoint síncrono de compatibilidade que executa a engine FAT32 de duas passadas.
    """
    dest_dir = os.path.abspath(os.path.expanduser(request.dest_dir.strip()))
    try:
        os.makedirs(dest_dir, exist_ok=True)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Não foi possível criar o diretório de destino: {exc}",
        ) from exc

    files_mapping = prepare_files_mapping(dest_dir, request.mappings)
    results = tagger_service.copy_sequentially(files_mapping)

    success_count = sum(1 for r in results if r["status"] == "success")
    error_count = len(results) - success_count

    return {
        "success": True,
        "results": results,
        "summary": {
            "total": len(results),
            "success": success_count,
            "error": error_count,
        },
    }

