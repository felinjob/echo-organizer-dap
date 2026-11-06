"""
Cliente HTTP para o MusicBrainz e Cover Art Archive com suporte a buscas
Release-Centric (1 requisição por álbum), cache SQLite (positivo e negativo),
retentativa automática em HTTP 503/429 e interrupção limpa via `SHUTDOWN_EVENT`.
"""

import os
import threading
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple
import requests

import app.services.cache as cache_service
from app.config import (
    DURATION_MATCH_TOLERANCE_SECONDS,
    MUSICBRAINZ_RATE_LIMIT_SECONDS,
    SHUTDOWN_EVENT,
    USER_AGENT,
    get_logger,
)
from app.core.normalizer import (
    clean_semantic_junk,
    normalize_metadata_tags,
    normalize_unicode,
    parse_and_clean_filename,
    strip_leading_track_number,
)
from app.core.reconciler import (
    extract_mb_duration_seconds,
    normalize_genre,
    reconcile_album_tracks,
    score_track_match,
)
from app.services.lrclib import fetch_lyrics_from_lrclib

__all__ = [
    "USER_AGENT",
    "rate_limited_get",
    "clean_filename",
    "get_cover_art_url",
    "fetch_lyrics_from_lrclib",
    "normalize_genre",
    "score_track_match",
    "search_musicbrainz_release",
    "reconcile_album_tracks",
    "search_musicbrainz",
    "enrich_batch_release_centric",
    "get_proposed_metadata",
]

logger = get_logger(__name__)

LAST_REQUEST_TIME: float = 0.0
_mb_rate_lock = threading.Lock()

PLACEHOLDER_METADATA_TERMS = frozenset(
    {
        "",
        "artista desconhecido",
        "unknown artist",
        "unknown",
        "álbum desconhecido",
        "unknown album",
    }
)


def rate_limited_get(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Optional[Any]:
    """
    Executa uma requisição HTTP GET respeitando o limite de 1 requisição por segundo
    exigido pela API oficial do MusicBrainz, com retry automático em HTTP 503/429
    e interrupção imediata se `SHUTDOWN_EVENT` estiver sinalizado.
    """
    global LAST_REQUEST_TIME
    if SHUTDOWN_EVENT.is_set():
        return None

    req_headers = dict(headers) if headers else {}
    req_headers["User-Agent"] = USER_AGENT

    for attempt in range(2):
        if SHUTDOWN_EVENT.is_set():
            return None

        with _mb_rate_lock:
            now = time.time()
            elapsed = now - LAST_REQUEST_TIME
            if elapsed < MUSICBRAINZ_RATE_LIMIT_SECONDS:
                wait_sec = MUSICBRAINZ_RATE_LIMIT_SECONDS - elapsed
                if SHUTDOWN_EVENT.wait(timeout=wait_sec):
                    return None
            LAST_REQUEST_TIME = time.time()

        try:
            response = requests.get(
                url, headers=req_headers, params=params, timeout=8
            )
            with _mb_rate_lock:
                LAST_REQUEST_TIME = time.time()

            if response.status_code == 200:
                try:
                    return response.json()
                except Exception as json_err:
                    logger.warning(
                        "Erro ao decodificar JSON do MusicBrainz: %s", json_err
                    )
                    return None

            if response.status_code == 404 and "coverartarchive.org" in url:
                # 404 no Cover Art Archive significa apenas que o álbum não possui capa cadastrada
                return {"images": []}

            if response.status_code in (503, 429) and attempt == 0:
                # Backoff curto em sobrecarga temporária ou rate-limit do MusicBrainz
                if SHUTDOWN_EVENT.wait(timeout=1.8):
                    return None
                continue

            if response.status_code == 403:
                logger.warning(
                    "MusicBrainz retornou HTTP 403 Forbidden. Verifique o User-Agent."
                )
            else:
                logger.warning(
                    "Erro na API do MusicBrainz (%s): HTTP %s",
                    url,
                    response.status_code,
                )
            return None
        except Exception as exc:
            logger.warning(
                "Exceção de rede ao consultar MusicBrainz (%s): %s", url, exc
            )
            return None

    return None


def clean_filename(filename: str) -> Tuple[Optional[str], Optional[str], str]:
    """
    Higieniza nomes de arquivo brutos e extrai `(track_num, artist, title)`
    usando o pipeline determinístico do `app.core.normalizer`.
    """
    return parse_and_clean_filename(filename)


def get_cover_art_url(release_id: str) -> Optional[str]:
    """
    Busca a URL da capa frontal (preferencialmente miniatura de 500px) no Cover Art Archive
    para um `release_id`, utilizando cache persistente em SQLite.
    """
    if not release_id or SHUTDOWN_EVENT.is_set():
        return None

    cached_url = cache_service.get_cached_cover_url(release_id)
    if cached_url is not None:
        return cached_url if cached_url else None

    url = f"https://coverartarchive.org/release/{release_id}"
    data = rate_limited_get(url)
    if data is None:
        return None

    found_url = ""
    if "images" in data:
        for img in data["images"]:
            if img.get("front") is True:
                thumbnails = img.get("thumbnails", {})
                if "500" in thumbnails:
                    found_url = thumbnails["500"]
                elif "large" in thumbnails:
                    found_url = thumbnails["large"]
                else:
                    found_url = img.get("image", "")
                break

    cache_service.set_cached_cover_url(release_id, found_url)
    return found_url if found_url else None


def search_musicbrainz_release(
    album_artist: str,
    album: str,
    track_count: Optional[int] = None,
    sample_title: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Realiza busca centrada em Álbum (Release-Centric) no MusicBrainz, retornando
    o lançamento completo com mídias, faixas, durações, artistas e capa,
    utilizando cache SQLite local para evitar requisições repetidas.
    """
    if SHUTDOWN_EVENT.is_set():
        return None

    clean_artist = normalize_unicode(album_artist)
    clean_album = clean_semantic_junk(album)

    if clean_artist.lower() in PLACEHOLDER_METADATA_TERMS:
        clean_artist = ""
    if clean_album.lower() in PLACEHOLDER_METADATA_TERMS:
        clean_album = ""

    if not clean_album and not clean_artist and not sample_title:
        return None

    cache_key = cache_service.make_key(
        "mb_release", clean_artist, clean_album, sample_title or ""
    )
    cached_release = cache_service.get_cached_mb_release(cache_key)
    if cached_release is not None:
        return cached_release if cached_release.get("id") else None

    query_parts: List[str] = []
    if clean_album:
        query_parts.append(f'release:"{clean_album}"')
    if clean_artist:
        query_parts.append(f'artist:"{clean_artist}"')
    if not clean_album and sample_title:
        query_parts.append(f'recording:"{clean_semantic_junk(sample_title)}"')

    query = " AND ".join(query_parts)
    encoded_query = urllib.parse.quote(query)
    url = (
        f"https://musicbrainz.org/ws/2/release/?query={encoded_query}"
        "&inc=recordings+artist-credits+tags&fmt=json"
    )

    data = rate_limited_get(url)
    if data is None:
        return None

    if "releases" not in data or not data["releases"]:
        # Cache negativo quando o álbum comprovadamente não existe no MusicBrainz
        cache_service.set_cached_mb_release(cache_key, {"id": ""})
        return None

    releases = data["releases"]

    def _release_rank(rel: Dict[str, Any]) -> Tuple[int, int, int]:
        status_score = 1 if rel.get("status") == "Official" else 0
        has_tracks = 1 if any(m.get("tracks") for m in rel.get("media", [])) else 0
        total_t = sum(
            int(m.get("track-count", len(m.get("tracks", []))) or 0)
            for m in rel.get("media", [])
        )
        count_diff = abs(total_t - track_count) if (track_count and total_t) else 50
        return (-status_score, -has_tracks, count_diff)

    sorted_releases = sorted(releases, key=_release_rank)
    best_release = sorted_releases[0]
    release_id = best_release.get("id")

    has_embedded_tracks = any(
        bool(m.get("tracks")) for m in best_release.get("media", [])
    )
    if not has_embedded_tracks and release_id and not SHUTDOWN_EVENT.is_set():
        lookup_url = (
            f"https://musicbrainz.org/ws/2/release/{release_id}"
            "?inc=recordings+artist-credits+tags+release-groups&fmt=json"
        )
        lookup_data = rate_limited_get(lookup_url)
        if lookup_data and lookup_data.get("media"):
            best_release = lookup_data

    if release_id and "cover_url" not in best_release and not SHUTDOWN_EVENT.is_set():
        try:
            best_release["cover_url"] = get_cover_art_url(release_id) or ""
        except Exception:
            best_release["cover_url"] = ""

    cache_service.set_cached_mb_release(cache_key, best_release)
    return best_release


def search_musicbrainz(
    artist: str,
    title: str,
    query_str: Optional[str] = None,
    duration: Optional[int] = None,
    *,
    allow_extra_artist_lookup: bool = True,
) -> Optional[Dict[str, Any]]:
    """
    Consulta a API do MusicBrainz para uma faixa individual (com cache SQLite e ponderação por `duration`).
    """
    if SHUTDOWN_EVENT.is_set():
        return None

    clean_artist = normalize_unicode(artist)
    clean_title = strip_leading_track_number(clean_semantic_junk(title))

    cache_key = cache_service.make_key(
        "mb_recording", clean_artist, clean_title, query_str or "", duration or 0
    )
    cached = cache_service.get_cached_mb_recording(cache_key)
    if cached is not None:
        return cached if cached.get("title") else None

    if clean_artist and clean_title:
        query = f'artist:"{clean_artist}" AND recording:"{clean_title}"'
    elif clean_title:
        query = f'recording:"{clean_title}"'
    elif query_str:
        query = query_str
    else:
        return None

    encoded_query = urllib.parse.quote(query)
    url = f"https://musicbrainz.org/ws/2/recording/?query={encoded_query}&fmt=json"

    data = rate_limited_get(url)
    if data is None:
        return None

    if "recordings" not in data or not data["recordings"]:
        cache_service.set_cached_mb_recording(cache_key, {"title": ""})
        return None

    recordings = data["recordings"]

    best_recording = recordings[0]
    if duration and duration > 0:

        def _rec_rank(rec: Dict[str, Any]) -> Tuple[int, float]:
            rec_dur = extract_mb_duration_seconds(rec)
            if rec_dur > 0:
                diff = abs(rec_dur - duration)
                in_window = 0 if diff <= DURATION_MATCH_TOLERANCE_SECONDS else 1
                return (in_window, float(diff))
            return (2, 999.0)

        recordings_sorted = sorted(recordings, key=_rec_rank)
        best_recording = recordings_sorted[0]

    recording = best_recording

    suggested: Dict[str, Any] = {
        "title": normalize_unicode(recording.get("title", clean_title)),
        "artist": "",
        "album_artist": "",
        "album": "",
        "track": "",
        "disc": "1",
        "year": "",
        "genre": "",
        "cover_url": "",
    }

    artist_credits = recording.get("artist-credit", [])
    if artist_credits:
        suggested["artist"] = ", ".join(
            [
                normalize_unicode(ac.get("name", ""))
                for ac in artist_credits
                if "name" in ac
            ]
        )
    else:
        suggested["artist"] = clean_artist or ""

    releases = recording.get("releases", [])
    if releases:

        def _release_sort_key(rel: Dict[str, Any]) -> int:
            score = 0
            if rel.get("status") == "Official":
                score += 10
            if rel.get("date"):
                score += 5
            return -score

        sorted_releases = sorted(releases, key=_release_sort_key)
        best_release = sorted_releases[0]

        suggested["album"] = normalize_unicode(best_release.get("title", ""))

        rel_artists = best_release.get("artist-credit", [])
        if rel_artists:
            suggested["album_artist"] = ", ".join(
                [
                    normalize_unicode(ac.get("name", ""))
                    for ac in rel_artists
                    if "name" in ac
                ]
            )
        else:
            suggested["album_artist"] = suggested["artist"]

        date_val = best_release.get("date", "")
        if date_val:
            suggested["year"] = str(date_val)[:4]

        media = best_release.get("media", [])
        if media:
            found_track = False
            tracks: List[Dict[str, Any]] = []
            for m_idx, medium in enumerate(media):
                tracks = medium.get("tracks", [])
                for trk in tracks:
                    if trk.get("recording", {}).get("id") == recording.get("id"):
                        suggested["track"] = str(trk.get("number", ""))
                        suggested["disc"] = str(medium.get("position", m_idx + 1))
                        found_track = True
                        break
                if found_track:
                    break

            if not suggested["track"] and tracks:
                suggested["track"] = str(tracks[0].get("number", ""))
                suggested["disc"] = "1"

        release_id = best_release.get("id")
        if release_id and not SHUTDOWN_EVENT.is_set():
            try:
                suggested["cover_url"] = get_cover_art_url(release_id) or ""
            except Exception as exc:
                logger.warning(
                    "Erro ao obter capa para release %s: %s", release_id, exc
                )

    tags = recording.get("tags", [])
    if (
        not tags
        and allow_extra_artist_lookup
        and "artist-credit" in recording
        and recording["artist-credit"]
        and not SHUTDOWN_EVENT.is_set()
    ):
        artist_id = recording["artist-credit"][0].get("artist", {}).get("id")
        if artist_id:
            artist_url = f"https://musicbrainz.org/ws/2/artist/{artist_id}?fmt=json"
            artist_data = rate_limited_get(artist_url)
            if artist_data:
                tags = artist_data.get("tags", [])

    if tags:
        sorted_tags = sorted(tags, key=lambda x: x.get("count", 0), reverse=True)
        if sorted_tags:
            suggested["genre"] = normalize_genre(sorted_tags[0].get("name", ""))

    suggested = normalize_metadata_tags(suggested)
    cache_service.set_cached_mb_recording(cache_key, suggested)
    return suggested


def enrich_batch_release_centric(
    items: List[Dict[str, Any]], fetch_lyrics: bool = True
) -> List[Dict[str, Any]]:
    """
    Agrupa uma fila de arquivos por diretório de origem ou por par `(Album Artist, Album)`,
    realiza apenas 1 consulta de Release por álbum no MusicBrainz, reconcilia as faixas
    localmente via Multi-Fator (Título + Faixa + Duração Real `+-3s`) e busca letras no LRCLib
    filtradas pela duração exata.
    """
    if not items:
        return []

    groups: Dict[str, List[int]] = {}
    for idx, item in enumerate(items):
        src_dir = (
            os.path.dirname(os.path.abspath(item.get("source", "")))
            if item.get("source")
            else ""
        )
        tags = item.get("tags") or item.get("proposed") or item
        alb_artist = normalize_unicode(
            tags.get("album_artist") or tags.get("artist") or ""
        ).lower()
        alb_name = clean_semantic_junk(tags.get("album") or "").lower()

        if alb_name and alb_name not in PLACEHOLDER_METADATA_TERMS:
            group_key = f"album:{alb_artist}|{alb_name}"
        else:
            group_key = f"folder:{src_dir.lower()}|{alb_artist}"
        groups.setdefault(group_key, []).append(idx)

    enriched_results: List[Dict[str, Any]] = [dict(it) for it in items]

    for _, group_indices in groups.items():
        if SHUTDOWN_EVENT.is_set():
            break

        group_tracks: List[Dict[str, Any]] = []
        rep_artist = ""
        rep_album = ""
        sample_title = ""

        for i in group_indices:
            t_data = (
                enriched_results[i].get("tags")
                or enriched_results[i].get("proposed")
                or enriched_results[i]
            )
            norm_t = normalize_metadata_tags(
                t_data, filename=enriched_results[i].get("filename")
            )
            norm_t["duration"] = t_data.get("duration", 0)
            group_tracks.append(norm_t)

            if (
                not rep_artist
                and norm_t.get("album_artist")
                and norm_t["album_artist"].lower() not in PLACEHOLDER_METADATA_TERMS
            ):
                rep_artist = norm_t["album_artist"]
            elif (
                not rep_artist
                and norm_t.get("artist")
                and norm_t["artist"].lower() not in PLACEHOLDER_METADATA_TERMS
            ):
                rep_artist = norm_t["artist"]
            if (
                not rep_album
                and norm_t.get("album")
                and norm_t["album"].lower() not in PLACEHOLDER_METADATA_TERMS
            ):
                rep_album = norm_t["album"]
            if not sample_title and norm_t.get("title"):
                sample_title = norm_t["title"]

        release_data = search_musicbrainz_release(
            album_artist=rep_artist,
            album=rep_album,
            track_count=len(group_tracks),
            sample_title=sample_title,
        )

        reconciled_map: Dict[int, Dict[str, Any]] = {}
        if release_data:
            reconciled_map = reconcile_album_tracks(group_tracks, release_data)

        # Só faz fallback individual por gravação se for um grupo pequeno (singles/EPs <= 3 faixas)
        # ou para no máximo 2 faixas órfãs de um Release já encontrado, evitando tempestade N+1
        # quando um álbum inteiro não existe no MusicBrainz.
        unmatched_count = len(group_tracks) - len(reconciled_map)
        allow_single_fallback = (
            len(group_tracks) <= 3
            or (release_data is not None and unmatched_count <= 2)
        )

        for local_pos, item_idx in enumerate(group_indices):
            if SHUTDOWN_EVENT.is_set():
                break

            base_tags = group_tracks[local_pos]
            matched_tags = reconciled_map.get(local_pos)

            if not matched_tags and allow_single_fallback:
                single_mb = search_musicbrainz(
                    base_tags.get("artist", ""),
                    base_tags.get("title", ""),
                    duration=base_tags.get("duration", 0),
                    allow_extra_artist_lookup=False,
                )
                if single_mb:
                    matched_tags = {
                        **base_tags,
                        **{k: v for k, v in single_mb.items() if v},
                        "has_cover": bool(single_mb.get("cover_url"))
                        or bool(base_tags.get("has_cover")),
                        "source": "MusicBrainz",
                    }

            final_tags = (
                matched_tags
                if matched_tags
                else {
                    **base_tags,
                    "source": base_tags.get("source", "Local Heuristics"),
                }
            )
            final_tags["duration"] = base_tags.get("duration", 0)

            if (
                fetch_lyrics
                and not SHUTDOWN_EVENT.is_set()
                and final_tags.get("artist")
                and final_tags.get("title")
                and not final_tags.get("lyrics")
            ):
                lyrics = fetch_lyrics_from_lrclib(
                    artist=final_tags["artist"],
                    title=final_tags["title"],
                    album=final_tags.get("album"),
                    duration=final_tags.get("duration"),
                )
                if lyrics:
                    final_tags["lyrics"] = lyrics

            enriched_results[item_idx]["proposed"] = final_tags
            enriched_results[item_idx]["tags"] = final_tags

    return enriched_results


def get_proposed_metadata(file_path: str) -> Dict[str, Any]:
    """
    Extrai tags físicas, higieniza o nome do arquivo, consulta o MusicBrainz
    e retorna os metadados originais e propostos.
    """
    from app.services.audio_tagger import extract_metadata

    original = extract_metadata(file_path)
    filename = os.path.basename(file_path)
    heur_track, heur_artist, heur_title = clean_filename(filename)

    proposed = normalize_metadata_tags(
        {
            "title": original["title"] or heur_title or "",
            "artist": original["artist"] or heur_artist or "",
            "album_artist": original["album_artist"] or "",
            "album": original["album"] or "",
            "track": original["track"] or heur_track or "",
            "disc": original["disc"] or "1",
            "year": original["year"] or "",
            "genre": normalize_genre(original["genre"]) or "",
            "duration": original.get("duration", 0),
            "has_cover": original["has_cover"],
            "cover_url": "",
        },
        filename=filename,
    )

    search_artist = proposed["artist"] or heur_artist or ""
    search_title = proposed["title"] or heur_title or ""

    mb_tags = search_musicbrainz(
        search_artist, search_title, duration=original.get("duration", 0)
    )
    if mb_tags:
        proposed["title"] = mb_tags.get("title") or proposed["title"]
        proposed["artist"] = mb_tags.get("artist") or proposed["artist"]
        proposed["album_artist"] = (
            mb_tags.get("album_artist") or proposed["album_artist"]
        )
        proposed["album"] = mb_tags.get("album") or proposed["album"]
        proposed["track"] = mb_tags.get("track") or proposed["track"]
        proposed["disc"] = mb_tags.get("disc") or proposed["disc"]
        proposed["year"] = mb_tags.get("year") or proposed["year"]
        proposed["genre"] = normalize_genre(mb_tags.get("genre")) or proposed["genre"]
        proposed["cover_url"] = mb_tags.get("cover_url") or ""
        if proposed["cover_url"]:
            proposed["has_cover"] = True
        proposed = normalize_metadata_tags(proposed, filename=filename)
        proposed["source"] = "MusicBrainz"
    else:
        proposed["source"] = "Local Heuristics"

    proposed["duration"] = original.get("duration", 0)

    return {
        "original": original,
        "proposed": proposed,
    }
