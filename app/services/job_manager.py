"""
Gerenciador de Jobs em Background e transmissão de eventos via Server-Sent Events (SSE).

Coordena a execução assíncrona do lote completo (Two-Pass FAT32 Write), mantendo o estado
de cada job (`running`, `paused`, `cancelled`, `completed`, `error`) de forma thread-safe.
"""

import asyncio
import json
import os
import threading
import time
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional

import app.services.fat32_writer as tagger_service
from app.config import JOB_RETENTION_SECONDS, SUPPORTED_AUDIO_EXTENSIONS, get_logger
from app.core.normalizer import build_destination_path, normalize_metadata_tags
from app.schemas import FileTagMapping
from app.services.indexer import build_library_index

logger = get_logger(__name__)

_jobs_lock = threading.RLock()
_jobs_store: Dict[str, Dict[str, Any]] = {}


def get_jobs_store() -> Dict[str, Dict[str, Any]]:
    """Retorna a referência ao dicionário de jobs (útil para inspeção e testes)."""
    return _jobs_store


def append_job_event(job_id: str, event_payload: Dict[str, Any]) -> None:
    """Adiciona um evento estruturado ao histórico de streaming SSE de um job."""
    with _jobs_lock:
        job = _jobs_store.get(job_id)
        if not job:
            return
        event_copy = dict(event_payload)
        event_copy["timestamp"] = time.time()
        job["events"].append(event_copy)


def prepare_files_mapping(
    dest_dir: str, mappings: List[FileTagMapping]
) -> List[Dict[str, Any]]:
    """
    Normaliza os metadados de cada item da fila e calcula seu caminho absoluto de destino
    respeitando as regras de Album Artist, Multi-Disco e o limite de 200 caracteres.
    """
    files_mapping: List[Dict[str, Any]] = []
    for item in mappings:
        src = item.source
        tags = normalize_metadata_tags(item.tags, filename=os.path.basename(src))
        ext = os.path.splitext(src)[1].lower()
        if ext not in SUPPORTED_AUDIO_EXTENSIONS:
            ext = ".mp3"
        dest_path = build_destination_path(dest_dir, tags, ext)
        files_mapping.append(
            {
                "source": src,
                "dest": dest_path,
                "tags": tags,
            }
        )
    return files_mapping


def _run_job_worker(
    job_id: str,
    dest_dir: str,
    files_mapping: List[Dict[str, Any]],
    generate_index: bool = True,
) -> None:
    """
    Worker em thread dedicada que executa `tagger_service.copy_sequentially`
    com callbacks em tempo real para alimentar o canal SSE e atender comandos
    de pausa, retomada e cancelamento.
    """
    total = len(files_mapping)

    def _check_control() -> str:
        while True:
            with _jobs_lock:
                job = _jobs_store.get(job_id)
                if not job:
                    return "cancelled"
                status = job["status"]
            if status == "cancelled":
                return "cancelled"
            if status == "paused":
                time.sleep(0.15)
                continue
            return "running"

    def _on_progress(prog: Dict[str, Any]) -> None:
        processed = prog.get("processed", 0)
        tot = prog.get("total", total) or 1
        percent = int(round((processed / tot) * 100))

        with _jobs_lock:
            job = _jobs_store.get(job_id)
            if not job:
                return
            job["album"] = prog.get("album", "")
            job["current_file"] = prog.get("current_file", "")
            job["stage"] = prog.get("stage", "pass_1_audio")
            job["processed"] = processed
            job["percent"] = percent
            current_status = job["status"]

        ev: Dict[str, Any] = {
            "type": "progress",
            "job_id": job_id,
            "status": current_status,
            "stage": prog.get("stage", "pass_1_audio"),
            "album": prog.get("album", ""),
            "current_file": prog.get("current_file", ""),
            "processed": processed,
            "total": tot,
            "percent": percent,
        }

        if "item_result" in prog:
            res = prog["item_result"]
            ev["item_result"] = res
            if res["status"] == "success":
                parts = res["dest"].replace("\\", "/").split("/")
                short_dest = "/".join(parts[-3:]) if len(parts) >= 3 else res["dest"]
                ev["log"] = {
                    "level": "success",
                    "message": f"✓ Gravado (Passada 1 Áudio): {short_dest}",
                }
            else:
                ev["log"] = {
                    "level": "error",
                    "message": (
                        f"✗ Erro ao gravar {os.path.basename(res['source'])}: "
                        f"{res.get('message', '')}"
                    ),
                }
        elif prog.get("stage") == "pass_2_lrc":
            ev["log"] = {
                "level": "info",
                "message": (
                    f"📝 Gravando letra sincronizada (Passada 2 .lrc): "
                    f"{prog.get('current_file', '')}"
                ),
            }

        append_job_event(job_id, ev)

    try:
        append_job_event(
            job_id,
            {
                "type": "started",
                "job_id": job_id,
                "status": "running",
                "stage": "pass_1_audio",
                "processed": 0,
                "total": total,
                "percent": 0,
                "log": {
                    "level": "info",
                    "message": (
                        f"🚀 Iniciando gravação atômica FAT32 em duas passadas "
                        f"({total} faixas)..."
                    ),
                },
            },
        )

        results = tagger_service.copy_sequentially(
            files_mapping,
            on_progress=_on_progress,
            check_control=_check_control,
        )

        with _jobs_lock:
            current_status = _jobs_store[job_id]["status"]

        if current_status == "cancelled":
            tagger_service.cleanup_temp_files(dest_dir)
            append_job_event(
                job_id,
                {
                    "type": "cancelled",
                    "job_id": job_id,
                    "status": "cancelled",
                    "stage": "done",
                    "processed": len(results),
                    "total": total,
                    "percent": int(round((len(results) / max(1, total)) * 100)),
                    "log": {
                        "level": "error",
                        "message": (
                            "🛑 Processamento cancelado pelo usuário. "
                            "Arquivos temporários (.tmp) foram limpos."
                        ),
                    },
                },
            )
            return

        index_summary = None
        if generate_index and results:
            append_job_event(
                job_id,
                {
                    "type": "progress",
                    "job_id": job_id,
                    "status": "running",
                    "stage": "indexing",
                    "processed": len(results),
                    "total": total,
                    "percent": 99,
                    "log": {
                        "level": "info",
                        "message": (
                            "📄 Gerando índice estrutural da biblioteca "
                            "(library_index.txt)..."
                        ),
                    },
                },
            )
            try:
                idx_data = build_library_index(dest_dir)
                index_summary = idx_data["summary"]
                append_job_event(
                    job_id,
                    {
                        "type": "log",
                        "job_id": job_id,
                        "status": "running",
                        "stage": "indexing",
                        "log": {
                            "level": "success",
                            "message": (
                                f"✓ Relatório library_index.txt gerado "
                                f"({index_summary['tracks']} músicas organizadas)."
                            ),
                        },
                    },
                )
            except Exception as idx_err:
                logger.warning("Falha ao gerar library_index.txt: %s", idx_err)
                append_job_event(
                    job_id,
                    {
                        "type": "log",
                        "job_id": job_id,
                        "status": "running",
                        "stage": "indexing",
                        "log": {
                            "level": "error",
                            "message": f"⚠ Aviso ao gerar library_index.txt: {idx_err}",
                        },
                    },
                )

        success_count = sum(1 for r in results if r["status"] == "success")
        error_count = len(results) - success_count
        summary = {
            "total": total,
            "processed": len(results),
            "success": success_count,
            "error": error_count,
            "index": index_summary,
        }

        with _jobs_lock:
            job = _jobs_store[job_id]
            job["status"] = "completed"
            job["stage"] = "done"
            job["processed"] = len(results)
            job["percent"] = 100
            job["results"] = results
            job["summary"] = summary

        append_job_event(
            job_id,
            {
                "type": "completed",
                "job_id": job_id,
                "status": "completed",
                "stage": "done",
                "processed": len(results),
                "total": total,
                "percent": 100,
                "summary": summary,
                "log": {
                    "level": "info",
                    "message": (
                        f"🏁 Processamento Concluído! {success_count} de {total} "
                        f"arquivos gravados fisicamente em ordem para o DAP."
                    ),
                },
            },
        )
    except Exception as exc:
        logger.exception("Erro fatal no worker do job %s: %s", job_id, exc)
        with _jobs_lock:
            if job_id in _jobs_store:
                _jobs_store[job_id]["status"] = "error"
                _jobs_store[job_id]["error"] = str(exc)
        append_job_event(
            job_id,
            {
                "type": "error",
                "job_id": job_id,
                "status": "error",
                "stage": "done",
                "message": str(exc),
                "log": {
                    "level": "error",
                    "message": f"💥 Erro fatal no processamento do lote: {exc}",
                },
            },
        )


def _prune_expired_jobs() -> None:
    """Remove jobs finalizados que excederam o tempo de retenção em memória."""
    cutoff = time.time() - JOB_RETENTION_SECONDS
    with _jobs_lock:
        expired_ids = [
            jid
            for jid, jdata in _jobs_store.items()
            if jdata.get("status") in ("completed", "cancelled", "error")
            and jdata.get("created_at", 0) < cutoff
        ]
        for jid in expired_ids:
            _jobs_store.pop(jid, None)


def create_and_start_job(
    dest_dir: str, mappings: List[FileTagMapping], generate_index: bool = True
) -> Dict[str, Any]:
    """
    Registra um novo job de processamento em lote e dispara sua thread de execução.
    """
    _prune_expired_jobs()
    files_mapping = prepare_files_mapping(dest_dir, mappings)
    job_id = uuid.uuid4().hex

    with _jobs_lock:
        _jobs_store[job_id] = {
            "job_id": job_id,
            "status": "running",
            "stage": "pass_1_audio",
            "album": "",
            "current_file": "",
            "processed": 0,
            "total": len(files_mapping),
            "percent": 0,
            "events": [],
            "results": [],
            "summary": None,
            "created_at": time.time(),
        }

    worker = threading.Thread(
        target=_run_job_worker,
        args=(job_id, dest_dir, files_mapping, generate_index),
        daemon=True,
    )
    worker.start()

    return {
        "success": True,
        "job_id": job_id,
        "status": "running",
        "total": len(files_mapping),
    }


def get_job_snapshot(job_id: str) -> Optional[Dict[str, Any]]:
    """Retorna o estado resumido atual de um job ou None se inexistente."""
    with _jobs_lock:
        job = _jobs_store.get(job_id)
        if not job:
            return None
        return {
            "job_id": job["job_id"],
            "status": job["status"],
            "stage": job["stage"],
            "album": job["album"],
            "current_file": job["current_file"],
            "processed": job["processed"],
            "total": job["total"],
            "percent": job["percent"],
            "summary": job["summary"],
        }


def transition_job_state(job_id: str, target_action: str) -> Optional[Dict[str, Any]]:
    """
    Altera o estado de execução de um job (`pause`, `resume`, `cancel`).
    Retorna None caso o `job_id` não exista.
    """
    with _jobs_lock:
        job = _jobs_store.get(job_id)
        if not job:
            return None

        if target_action == "pause" and job["status"] == "running":
            job["status"] = "paused"
            append_job_event(
                job_id,
                {
                    "type": "paused",
                    "job_id": job_id,
                    "status": "paused",
                    "stage": job["stage"],
                    "processed": job["processed"],
                    "total": job["total"],
                    "percent": job["percent"],
                    "log": {
                        "level": "info",
                        "message": "⏸ Processamento pausado pelo usuário.",
                    },
                },
            )
        elif target_action == "resume" and job["status"] == "paused":
            job["status"] = "running"
            append_job_event(
                job_id,
                {
                    "type": "resumed",
                    "job_id": job_id,
                    "status": "running",
                    "stage": job["stage"],
                    "processed": job["processed"],
                    "total": job["total"],
                    "percent": job["percent"],
                    "log": {
                        "level": "info",
                        "message": "▶ Processamento retomado.",
                    },
                },
            )
        elif target_action == "cancel" and job["status"] in ("running", "paused"):
            job["status"] = "cancelled"

        return {"success": True, "job_id": job_id, "status": job["status"]}


async def iter_job_sse_events(job_id: str) -> AsyncGenerator[str, None]:
    """
    Gerador assíncrono que emite mensagens formatadas no protocolo Server-Sent Events (SSE).
    """
    cursor = 0
    while True:
        with _jobs_lock:
            job = _jobs_store.get(job_id)
            if not job:
                break
            events_slice = job["events"][cursor:]
            cursor += len(events_slice)
            status = job["status"]

        for event in events_slice:
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        if status in ("completed", "cancelled", "error") and not events_slice:
            break

        await asyncio.sleep(0.1)

