"""
Cliente HTTP para busca de letras sincronizadas (.lrc) e planas no LRCLib.

Envia obrigatoriamente `duration` em `/api/get` e aplica fallback em `/api/search`
filtrado pela diferença absoluta de duração (`<= 3s`). Inclui cache de respostas
negativas no SQLite e interrupção imediata via `SHUTDOWN_EVENT`.
"""

import threading
import time
from typing import Any, Dict, Optional
import requests

import app.services.cache as cache_service
from app.config import (
    DURATION_MATCH_TOLERANCE_SECONDS,
    LRCLIB_RATE_LIMIT_SECONDS,
    SHUTDOWN_EVENT,
    USER_AGENT,
    get_logger,
)
from app.core.normalizer import (
    clean_semantic_junk,
    normalize_unicode,
    strip_leading_track_number,
)

__all__ = ["fetch_lyrics_from_lrclib"]

logger = get_logger(__name__)

LAST_LRCLIB_REQUEST_TIME: float = 0.0
_lrclib_rate_lock = threading.Lock()


def _wait_lrclib_rate_limit() -> bool:
    """
    Aplica o intervalo mínimo entre requisições ao LRCLib de forma thread-safe.
    Retorna `False` se o servidor estiver em processo de desligamento (`SHUTDOWN_EVENT`).
    """
    global LAST_LRCLIB_REQUEST_TIME
    if SHUTDOWN_EVENT.is_set():
        return False
    with _lrclib_rate_lock:
        now = time.time()
        elapsed = now - LAST_LRCLIB_REQUEST_TIME
        if elapsed < LRCLIB_RATE_LIMIT_SECONDS:
            wait_sec = LRCLIB_RATE_LIMIT_SECONDS - elapsed
            if SHUTDOWN_EVENT.wait(timeout=wait_sec):
                return False
        LAST_LRCLIB_REQUEST_TIME = time.time()
    return not SHUTDOWN_EVENT.is_set()


def fetch_lyrics_from_lrclib(
    artist: str,
    title: str,
    album: Optional[str] = None,
    duration: Optional[int] = None,
) -> Optional[Dict[str, Optional[str]]]:
    """
    Consulta a API do LRCLib (`/api/get`) enviando obrigatoriamente `track_name`,
    `artist_name`, `album_name` e `duration` (em segundos).
    Caso não haja correspondência exata no `/api/get`, realiza fallback no `/api/search`
    filtrando os resultados pela menor diferença absoluta de duração (`<= 3s`).
    """
    if SHUTDOWN_EVENT.is_set():
        return None

    norm_artist = normalize_unicode(artist)
    norm_title = strip_leading_track_number(clean_semantic_junk(title))
    norm_album = clean_semantic_junk(album) if album else ""

    if not norm_title:
        return None

    dur_int: Optional[int] = None
    if duration is not None:
        try:
            val = int(round(float(duration)))
            if val > 0:
                dur_int = val
        except (ValueError, TypeError):
            dur_int = None

    cache_key = cache_service.make_key(
        "lrclib", norm_artist, norm_title, norm_album, dur_int or 0
    )
    cached = cache_service.get_cached_lyrics(cache_key)
    if cached is not None:
        if not cached.get("synced") and not cached.get("plain"):
            return None
        return cached

    url = "https://lrclib.net/api/get"
    params: Dict[str, Any] = {
        "artist_name": norm_artist,
        "track_name": norm_title,
        "album_name": norm_album,
    }
    if dur_int is not None:
        params["duration"] = dur_int

    headers = {"User-Agent": USER_AGENT}

    try:
        if not _wait_lrclib_rate_limit():
            return None

        response = requests.get(url, params=params, headers=headers, timeout=6)

        if response.status_code == 200:
            try:
                data = response.json()
            except Exception as json_err:
                logger.warning("Erro JSON no LRCLib /api/get: %s", json_err)
                return None

            result_payload = {
                "synced": data.get("syncedLyrics"),
                "plain": data.get("plainLyrics"),
            }
            if result_payload["synced"] or result_payload["plain"]:
                cache_service.set_cached_lyrics(cache_key, result_payload)
                return result_payload
            if data.get("instrumental") is True:
                cache_service.set_cached_lyrics(
                    cache_key, {"synced": None, "plain": None}
                )
                return None

        # Fallback no /api/search filtrando por diferença absoluta de duração <= 3s
        if not _wait_lrclib_rate_limit():
            return None

        search_url = "https://lrclib.net/api/search"
        search_params: Dict[str, Any] = {
            "track_name": norm_title,
            "artist_name": norm_artist,
        }
        if not norm_artist:
            search_params = {"q": norm_title}

        search_resp = requests.get(
            search_url, params=search_params, headers=headers, timeout=6
        )

        if search_resp.status_code == 200:
            try:
                results = search_resp.json()
            except Exception as json_err:
                logger.warning("Erro JSON no LRCLib /api/search: %s", json_err)
                return None

            if results and isinstance(results, list):
                best_match = None
                if dur_int is not None:
                    valid_candidates = []
                    for cand in results:
                        cand_dur = cand.get("duration")
                        if cand_dur is not None:
                            try:
                                diff = abs(float(cand_dur) - float(dur_int))
                                if diff <= float(
                                    DURATION_MATCH_TOLERANCE_SECONDS
                                ) and (
                                    cand.get("syncedLyrics") or cand.get("plainLyrics")
                                ):
                                    has_synced = 0 if cand.get("syncedLyrics") else 1
                                    valid_candidates.append((diff, has_synced, cand))
                            except (ValueError, TypeError):
                                continue
                    if valid_candidates:
                        valid_candidates.sort(key=lambda x: (x[0], x[1]))
                        best_match = valid_candidates[0][2]
                else:
                    for cand in results:
                        if cand.get("syncedLyrics") or cand.get("plainLyrics"):
                            best_match = cand
                            break

                if best_match:
                    result_payload = {
                        "synced": best_match.get("syncedLyrics"),
                        "plain": best_match.get("plainLyrics"),
                    }
                    cache_service.set_cached_lyrics(cache_key, result_payload)
                    return result_payload

            # Armazena resultado negativo no SQLite para evitar novas requisições na mesma faixa
            cache_service.set_cached_lyrics(
                cache_key, {"synced": None, "plain": None}
            )
    except Exception as exc:
        logger.warning(
            "Erro ao buscar letras no LRCLib para %s - %s: %s",
            norm_artist,
            norm_title,
            exc,
        )

    return None
