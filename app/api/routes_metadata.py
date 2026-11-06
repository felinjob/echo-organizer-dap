"""
Endpoints REST de enriquecimento de metadados e letras (/api/musicbrainz e /api/musicbrainz/release-batch).
"""

import os
from typing import Any, Dict
from fastapi import APIRouter, HTTPException

import app.services.musicbrainz as musicbrainz_service
from app.core.normalizer import normalize_metadata_tags
from app.schemas import MusicBrainzQuery, ReleaseBatchRequest

router = APIRouter(prefix="/api/musicbrainz", tags=["Metadata & Lyrics Enrichment"])


@router.post("")
def fetch_musicbrainz_tags(query: MusicBrainzQuery) -> Dict[str, Any]:
    """
    Consulta metadados no MusicBrainz e letras sincronizadas no LRCLib
    (filtradas pela duração física `duration`) para uma faixa individual.
    """
    search_artist = query.artist or ""
    search_title = query.title or ""

    if not search_artist or not search_title:
        if query.filename:
            _, heur_artist, heur_title = musicbrainz_service.clean_filename(
                query.filename
            )
            search_artist = search_artist or heur_artist or ""
            search_title = search_title or heur_title or ""

    if not search_title:
        raise HTTPException(
            status_code=400, detail="Título ou nome do arquivo é obrigatório."
        )

    mb_tags = musicbrainz_service.search_musicbrainz(
        search_artist, search_title, duration=query.duration
    )

    lyrics = None
    if search_artist and search_title:
        album_name = (mb_tags.get("album") if mb_tags else None) or query.album
        lyrics = musicbrainz_service.fetch_lyrics_from_lrclib(
            search_artist,
            search_title,
            album=album_name,
            duration=query.duration,
        )

    if mb_tags:
        mb_tags = normalize_metadata_tags(mb_tags, filename=query.filename)
        response_data: Dict[str, Any] = {"success": True, "tags": mb_tags}
        if lyrics:
            response_data["lyrics"] = lyrics
        return response_data

    if lyrics:
        heur_tags = normalize_metadata_tags(
            {
                "title": search_title,
                "artist": search_artist,
                "source": "Local Heuristics",
            },
            filename=query.filename,
        )
        heur_tags["source"] = "Local Heuristics"
        return {"success": True, "tags": heur_tags, "lyrics": lyrics}

    return {
        "success": False,
        "message": "Nenhum resultado encontrado no MusicBrainz ou LRCLib.",
    }


@router.post("/release-batch")
def fetch_musicbrainz_release_batch(request: ReleaseBatchRequest) -> Dict[str, Any]:
    """
    Agrupa as faixas enviadas por Álbum/Pasta, realiza 1 única consulta de Release por álbum
    no MusicBrainz, reconcilia localmente via Multi-Fator (Título + Faixa + Duração +-3s)
    e busca letras sincronizadas no LRCLib com validação de `duration`.
    """
    raw_items = [
        {
            "source": it.source,
            "filename": it.filename or (os.path.basename(it.source) if it.source else ""),
            "proposed": it.proposed,
        }
        for it in request.items
    ]
    enriched = musicbrainz_service.enrich_batch_release_centric(
        raw_items, fetch_lyrics=bool(request.fetch_lyrics)
    )
    return {"success": True, "items": enriched}

