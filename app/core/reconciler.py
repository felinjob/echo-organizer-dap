"""
Motor heurístico Multi-Fator de reconciliação de faixas de álbuns.

Cruza os arquivos físicos locais com a lista de faixas oficial de uma Release
do MusicBrainz ponderando Similaridade de Título (Fuzzy Matching), Índice de
Faixa/Disco e Proximidade de Duração Física (`duration` +-3s).
"""

import difflib
import re
from typing import Any, Dict, List, Set, Tuple

from app.config import DURATION_MATCH_TOLERANCE_SECONDS
from app.core.normalizer import (
    clean_semantic_junk,
    normalize_metadata_tags,
    normalize_unicode,
    strip_leading_track_number,
)

__all__ = [
    "normalize_genre",
    "extract_mb_duration_seconds",
    "score_track_match",
    "reconcile_album_tracks",
]


def normalize_genre(genre_raw: Any) -> str:
    """
    Normaliza gêneros complexos do MusicBrainz em categorias amplas para navegação no DAP.
    """
    if not genre_raw:
        return ""
    genre_lower = normalize_unicode(genre_raw).lower().strip()
    if not genre_lower:
        return ""

    if "metal" in genre_lower:
        return "Metal"
    if "punk" in genre_lower:
        return "Punk"
    if "rock" in genre_lower:
        return "Rock"
    if "jazz" in genre_lower:
        return "Jazz"
    if "blues" in genre_lower:
        return "Blues"
    if "pop" in genre_lower:
        return "Pop"
    if "rap" in genre_lower or "hip" in genre_lower:
        return "Hip-Hop"
    if any(
        k in genre_lower
        for k in (
            "house",
            "techno",
            "electronic",
            "synth",
            "electro",
            "dance",
            "edm",
        )
    ):
        return "Eletrônica"
    if any(
        k in genre_lower
        for k in ("classical", "clássica", "symphony", "baroque", "opera")
    ):
        return "Clássica"
    if "folk" in genre_lower:
        return "Folk"
    if "reggae" in genre_lower:
        return "Reggae"
    if "samba" in genre_lower or "pagode" in genre_lower:
        return "Samba"
    if "bossa" in genre_lower:
        return "Bossa Nova"
    if any(k in genre_lower for k in ("soundtrack", "ost", "trilha")):
        return "Trilha Sonora"

    return normalize_unicode(genre_raw).title()


def extract_mb_duration_seconds(mb_track: Dict[str, Any]) -> int:
    """
    Converte a duração de uma faixa do MusicBrainz (normalmente em milissegundos) para segundos inteiros.
    """
    raw_len = mb_track.get("length")
    if raw_len is None and isinstance(mb_track.get("recording"), dict):
        raw_len = mb_track["recording"].get("length")
    if raw_len is None:
        return 0
    try:
        val = float(raw_len)
        if val <= 0:
            return 0
        if val >= 1000:
            return int(round(val / 1000.0))
        return int(round(val))
    except (ValueError, TypeError):
        return 0


def _title_similarity(title_a: str, title_b: str) -> float:
    """
    Calcula similaridade entre dois títulos higienizados (`0.0` a `1.0`) usando Fuzzy Matching.
    """
    a_clean = strip_leading_track_number(clean_semantic_junk(title_a)).lower().strip()
    b_clean = strip_leading_track_number(clean_semantic_junk(title_b)).lower().strip()
    if not a_clean or not b_clean:
        return 0.0
    if a_clean == b_clean:
        return 1.0
    ratio = difflib.SequenceMatcher(None, a_clean, b_clean).ratio()
    if a_clean in b_clean or b_clean in a_clean:
        ratio = max(ratio, 0.85)
    return ratio


def score_track_match(local_item: Dict[str, Any], mb_track: Dict[str, Any]) -> float:
    """
    Calcula o score ponderado multi-fator entre um arquivo de áudio local e uma faixa oficial da Release:
    1. Proximidade de Duração (`duration`): Prioriza faixas dentro da janela de `+-3` segundos (`+35` pts)
       e penaliza versões divergentes (Radio Edit, Extended, Live com diferença `> 12s`).
    2. Similaridade de Título (Fuzzy Matching / SequenceMatcher): até `+50` pts.
    3. Índice de Faixa e Disco: até `+20` pts (`+15` para faixa exata, `+5` para disco exato).
    """
    score = 0.0

    local_title = local_item.get("title") or ""
    mb_title = (
        mb_track.get("title") or mb_track.get("recording", {}).get("title") or ""
    )
    sim = _title_similarity(local_title, mb_title)
    score += sim * 50.0

    local_dur = 0
    try:
        local_dur = int(round(float(local_item.get("duration") or 0)))
    except (ValueError, TypeError):
        local_dur = 0

    mb_dur = extract_mb_duration_seconds(mb_track)
    if local_dur > 0 and mb_dur > 0:
        diff = abs(local_dur - mb_dur)
        if diff <= DURATION_MATCH_TOLERANCE_SECONDS:
            score += 35.0 - (diff * 1.5)
        elif diff <= 7:
            score += 12.0
        elif diff > 12:
            score -= min(35.0, float(diff))

    local_track_num = 0
    m_lt = re.match(r"^(\d+)", str(local_item.get("track") or "").strip())
    if m_lt:
        local_track_num = int(m_lt.group(1))

    mb_track_num = int(mb_track.get("resolved_track_num") or 0)
    if local_track_num > 0 and mb_track_num > 0:
        if local_track_num == mb_track_num:
            score += 15.0
        elif abs(local_track_num - mb_track_num) == 1:
            score += 3.0

    local_disc_num = 1
    m_ld = re.match(r"^(\d+)", str(local_item.get("disc") or "1").strip())
    if m_ld:
        local_disc_num = int(m_ld.group(1))

    mb_disc_num = int(mb_track.get("resolved_disc_num") or 1)
    if local_disc_num == mb_disc_num:
        score += 5.0

    return score


def reconcile_album_tracks(
    local_tracks: List[Dict[str, Any]], release_data: Dict[str, Any]
) -> Dict[int, Dict[str, Any]]:
    """
    Cruza localmente todos os arquivos de um álbum com as faixas oficiais da Release do MusicBrainz
    usando o score ponderado Multi-Fator (Título + Faixa/Disco + Duração Real `+-3s`).
    Retorna um dicionário `{indice_local: tags_sugeridas}`.
    """
    if not release_data or not local_tracks:
        return {}

    album_title = normalize_unicode(release_data.get("title", ""))
    rel_artists = release_data.get("artist-credit", [])
    album_artist = ", ".join(
        [
            normalize_unicode(ac.get("name", ""))
            for ac in rel_artists
            if isinstance(ac, dict) and ac.get("name")
        ]
    )
    date_str = normalize_unicode(release_data.get("date", ""))
    year_str = date_str[:4] if date_str else ""
    cover_url = release_data.get("cover_url") or ""

    raw_tags = release_data.get("tags", []) or release_data.get(
        "release-group", {}
    ).get("tags", [])
    genre_str = ""
    if raw_tags:
        sorted_tags = sorted(raw_tags, key=lambda x: x.get("count", 0), reverse=True)
        if sorted_tags:
            genre_str = normalize_genre(sorted_tags[0].get("name", ""))

    mb_flat_tracks: List[Dict[str, Any]] = []
    for m_idx, medium in enumerate(release_data.get("media", [])):
        disc_pos = int(medium.get("position", m_idx + 1) or (m_idx + 1))
        for t_idx, trk in enumerate(medium.get("tracks", [])):
            trk_copy = dict(trk)
            num_raw = str(trk.get("number") or trk.get("position") or (t_idx + 1))
            m_num = re.match(r"^(\d+)", num_raw.strip())
            trk_copy["resolved_track_num"] = (
                int(m_num.group(1)) if m_num else (t_idx + 1)
            )
            trk_copy["resolved_disc_num"] = disc_pos
            mb_flat_tracks.append(trk_copy)

    if not mb_flat_tracks:
        return {}

    candidate_pairs: List[Tuple[float, int, int]] = []
    for l_idx, local_item in enumerate(local_tracks):
        for m_idx, mb_trk in enumerate(mb_flat_tracks):
            sc = score_track_match(local_item, mb_trk)
            candidate_pairs.append((sc, l_idx, m_idx))

    candidate_pairs.sort(key=lambda x: x[0], reverse=True)

    assigned_local: Set[int] = set()
    assigned_mb: Set[int] = set()
    matched_results: Dict[int, Dict[str, Any]] = {}

    for sc, l_idx, m_idx in candidate_pairs:
        if l_idx in assigned_local or m_idx in assigned_mb:
            continue
        if sc < 15.0:
            continue

        assigned_local.add(l_idx)
        assigned_mb.add(m_idx)

        mb_trk = mb_flat_tracks[m_idx]
        rec = (
            mb_trk.get("recording", {})
            if isinstance(mb_trk.get("recording"), dict)
            else {}
        )
        official_title = normalize_unicode(
            mb_trk.get("title")
            or rec.get("title")
            or local_tracks[l_idx].get("title", "")
        )

        trk_credits = mb_trk.get("artist-credit") or rec.get("artist-credit") or []
        track_artist = ", ".join(
            [
                normalize_unicode(ac.get("name", ""))
                for ac in trk_credits
                if isinstance(ac, dict) and ac.get("name")
            ]
        )
        if not track_artist:
            track_artist = album_artist or local_tracks[l_idx].get("artist", "")

        track_genre = genre_str
        if not track_genre and rec.get("tags"):
            rec_tags = sorted(
                rec["tags"], key=lambda x: x.get("count", 0), reverse=True
            )
            if rec_tags:
                track_genre = normalize_genre(rec_tags[0].get("name", ""))

        suggested = normalize_metadata_tags(
            {
                "title": official_title,
                "artist": track_artist,
                "album_artist": album_artist or track_artist,
                "album": album_title or local_tracks[l_idx].get("album", ""),
                "track": f"{int(mb_trk['resolved_track_num']):02d}",
                "disc": str(int(mb_trk["resolved_disc_num"])),
                "year": year_str or local_tracks[l_idx].get("year", ""),
                "genre": track_genre or local_tracks[l_idx].get("genre", ""),
                "duration": local_tracks[l_idx].get("duration")
                or extract_mb_duration_seconds(mb_trk),
                "cover_url": cover_url,
                "has_cover": bool(cover_url)
                or bool(local_tracks[l_idx].get("has_cover")),
                "source": "MusicBrainz",
            }
        )
        suggested["cover_url"] = cover_url
        suggested["has_cover"] = bool(cover_url) or bool(
            local_tracks[l_idx].get("has_cover")
        )
        suggested["source"] = "MusicBrainz"
        matched_results[l_idx] = suggested

    return matched_results

