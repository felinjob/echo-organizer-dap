"""
Endpoints REST de varredura de biblioteca (/api/scan), pré-visualização (/api/preview-path)
e geração de relatório estrutural (/api/generate-index).
"""

import os
from typing import Any, Dict, List
from fastapi import APIRouter, HTTPException

import app.services.audio_tagger as tagger_service
import app.services.musicbrainz as musicbrainz_service
from app.config import MAX_SCAN_FILES, SUPPORTED_AUDIO_EXTENSIONS, get_logger
from app.core.normalizer import (
    build_destination_path,
    build_safe_relative_path,
    normalize_metadata_tags,
)
from app.schemas import IndexRequest, PreviewPathRequest, ScanRequest
from app.services.indexer import build_library_index

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["Library & Scan"])


@router.post("/scan")
def scan_directory(request: ScanRequest) -> Dict[str, Any]:
    """
    Varre recursivamente o diretório em busca de arquivos MP3, FLAC e M4A/MP4,
    extraindo metadados reais (incluindo `duration` em segundos) e aplicando o
    pipeline determinístico de normalização NFC, extração de `feat.` e deduplicação de faixa.
    """
    source_dir = os.path.abspath(os.path.expanduser(request.source_dir.strip()))
    if not os.path.exists(source_dir):
        raise HTTPException(status_code=400, detail="Diretório de origem não existe.")
    if not os.path.isdir(source_dir):
        raise HTTPException(
            status_code=400, detail="O caminho especificado não é um diretório."
        )

    audio_files: List[str] = []
    for root, dirs, files in os.walk(source_dir, followlinks=False):
        dirs[:] = [
            d
            for d in dirs
            if not d.startswith(".__echo_")
            and not os.path.islink(os.path.join(root, d))
        ]
        for filename in sorted(files):
            if filename.lower().endswith(SUPPORTED_AUDIO_EXTENSIONS):
                full_p = os.path.join(root, filename)
                if not os.path.islink(full_p):
                    audio_files.append(full_p)
                    if len(audio_files) >= MAX_SCAN_FILES:
                        break
        if len(audio_files) >= MAX_SCAN_FILES:
            break

    results: List[Dict[str, Any]] = []
    for path in audio_files:
        try:
            original = tagger_service.extract_metadata(path)
            heur_track, heur_artist, heur_title = musicbrainz_service.clean_filename(
                original["filename"]
            )

            raw_proposed = {
                "title": original["title"] or heur_title or "",
                "artist": original["artist"] or heur_artist or "Artista Desconhecido",
                "album_artist": original["album_artist"] or "",
                "album": original["album"] or "Álbum Desconhecido",
                "track": original["track"] or heur_track or "01",
                "disc": original["disc"] or "1",
                "year": original["year"] or "",
                "genre": original["genre"] or "",
                "duration": original.get("duration", 0),
                "has_cover": original["has_cover"],
                "lyrics": original.get("lyrics"),
            }

            proposed = normalize_metadata_tags(
                raw_proposed, filename=original["filename"]
            )
            if not proposed.get("artist"):
                proposed["artist"] = "Artista Desconhecido"
            if not proposed.get("album_artist"):
                proposed["album_artist"] = proposed["artist"]
            if not proposed.get("album"):
                proposed["album"] = "Álbum Desconhecido"
            if not proposed.get("track"):
                proposed["track"] = "01"
            if not proposed.get("disc"):
                proposed["disc"] = "1"
            proposed["duration"] = original.get("duration", 0)

            ext = os.path.splitext(path)[1].lower()
            if ext not in SUPPORTED_AUDIO_EXTENSIONS:
                ext = ".mp3"

            proposed["relative_path"] = build_safe_relative_path(proposed, ext)

            already_organized = False
            if request.dest_dir:
                dest_path = build_destination_path(request.dest_dir, proposed, ext)
                if os.path.exists(dest_path):
                    already_organized = True

            results.append(
                {
                    "source": path,
                    "original": original,
                    "proposed": proposed,
                    "already_organized": already_organized,
                }
            )
        except Exception as exc:
            logger.warning("Erro ao escanear arquivo %s: %s", path, exc)

    return {"files": results}


@router.post("/preview-path")
def preview_safe_path(request: PreviewPathRequest) -> Dict[str, Any]:
    """
    Calcula o caminho relativo final (`Artista/Álbum/Faixa.ext` <= 200 caracteres)
    para exibição de Live Preview.
    """
    rel_path = build_safe_relative_path(request.tags, request.extension or ".mp3")
    return {"relative_path": rel_path, "length": len(rel_path)}


@router.post("/generate-index")
def generate_library_index(request: IndexRequest) -> Dict[str, Any]:
    """
    Gera o arquivo `library_index.txt` na raiz da pasta de destino organizada.
    """
    dest_dir = os.path.abspath(os.path.expanduser(request.dest_dir.strip()))
    if not os.path.exists(dest_dir) or not os.path.isdir(dest_dir):
        raise HTTPException(
            status_code=400, detail="Diretório de destino inválido ou inexistente."
        )

    try:
        idx_data = build_library_index(dest_dir)
        return {
            "success": True,
            "file_path": idx_data["file_path"],
            "summary": idx_data["summary"],
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Erro ao gerar índice: {exc}"
        ) from exc

