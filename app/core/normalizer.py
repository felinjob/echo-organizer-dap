"""
Motor determinístico de normalização textual (Unicode NFC), higienização semântica,
extração de Featuring, deduplicação de faixa (correção 01-01) e sanitização FAT32.
"""

import os
import re
import unicodedata
from typing import Any, Dict, FrozenSet, List, Optional, Pattern, Tuple

from app.config import MAX_RELATIVE_PATH_LENGTH

# Palavras reservadas proibidas como nomes de arquivos/pastas no DOS / Windows / FAT32
RESERVED_FAT_NAMES: FrozenSet[str] = frozenset(
    {
        "CON", "PRN", "AUX", "NUL",
        "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    }
)

# Caracteres estritamente ilegais em tabelas de diretório FAT32 / exFAT / NTFS
FAT32_FORBIDDEN_CHARS_RE: Pattern[str] = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')

# Padrões de lixo semântico entre parênteses ou colchetes oriundos de downloads/rips
BRACKETED_JUNK_PATTERNS: Tuple[Pattern[str], ...] = (
    re.compile(
        r"[\(\[]\s*(?:official\s+)?(?:music\s+)?(?:video|audio|lyric\s+video|lyrics?|"
        r"visualizer|clip|clipe\s+oficial|v[íi]deo\s+oficial|[áa]udio\s+oficial)\s*[\)\]]",
        re.IGNORECASE,
    ),
    re.compile(
        r"[\(\[]\s*(?:legendado|tradu[çc][ãa]o|hd|hq|4k|1080p|720p|480p|uhd)\s*[\)\]]",
        re.IGNORECASE,
    ),
    re.compile(
        r"[\(\[]\s*(?:\d{4}\s+)?(?:digital\s+)?remaster(?:ed|izado)?(?:\s+\d{4})?\s*[\)\]]",
        re.IGNORECASE,
    ),
    re.compile(r"[\(\[]\s*\d{2,4}\s*kbps\s*[\)\]]", re.IGNORECASE),
    re.compile(
        r"[\(\[]\s*(?:flac|mp3|wav|aac|m4a|cbr|vbr|web|cd\s*rip|vinyl\s*rip)\s*[\)\]]",
        re.IGNORECASE,
    ),
)

# Padrões de lixo semântico soltos no final da string
TRAILING_JUNK_PATTERNS: Tuple[Pattern[str], ...] = (
    re.compile(
        r"(?:\s*[-–—]\s*|\s+)(?:official\s+(?:music\s+)?(?:video|audio|lyric\s+video|"
        r"visualizer)|lyric\s+video|clipe\s+oficial|v[íi]deo\s+oficial|[áa]udio\s+oficial)\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:\s*[-–—]\s*|\s+)(?:\d{4}\s+remaster(?:ed)?|remaster(?:ed|izado)?(?:\s+\d{4})?)\s*$",
        re.IGNORECASE,
    ),
    re.compile(r"(?:\s*[-–—]\s*|\s+)(?:\d{2,4}\s*kbps)\s*$", re.IGNORECASE),
)

# Expressões regulares para captura de participações especiais (Featuring)
FEAT_BRACKET_RE: Pattern[str] = re.compile(
    r"\s*[\(\[]\s*(?:feat\.?|ft\.?|featuring|part\.?|"
    r"participa[çc][ãa]o(?:\s+especial)?(?:\s+de)?|with)\s+([^\)\]]+?)\s*[\)\]]",
    re.IGNORECASE,
)
FEAT_INLINE_RE: Pattern[str] = re.compile(
    r"(?:\s+[-–—]\s+|\s+)(?:feat\.?|ft\.?|featuring|part\.?)\s+([^(\[\n]+)$",
    re.IGNORECASE,
)
ARTIST_FEAT_SPLIT_RE: Pattern[str] = re.compile(
    r"^(.+?)\s+(?:[\(\[]\s*)?(?:feat\.?|ft\.?|featuring|part\.?)\s+(.+?)(?:[\)\]])?$",
    re.IGNORECASE,
)

# Expressões regulares para remoção de prefixos numéricos redundantes no título
TRACK_PREFIX_PATTERNS: Tuple[Pattern[str], ...] = (
    re.compile(r"^(?:[\[\(]?\d{1,2}[-.]\d{1,3}[\]\)]?)\s*(?:[-–—._:]+\s*|\s+)(.+)$"),
    re.compile(
        r"^[\[\(]\s*(?:track\s*|faixa\s*|#)?\d{1,3}\s*[\]\)]\s*[-–—._:]*\s*(.+)$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:track|faixa|#)\s*\d{1,3}\s*(?:[-–—._:]+\s*|\s+)(.+)$",
        re.IGNORECASE,
    ),
    re.compile(r"^\d{1,3}\s*[-–—._:]+\s*(.+)$"),
    re.compile(r"^0+\d{1,2}\s+(.+)$"),
)

UNKNOWN_ARTIST_PLACEHOLDERS: FrozenSet[str] = frozenset(
    {"", "artista desconhecido", "unknown artist", "unknown"}
)

VARIOUS_ARTISTS_ALIASES: FrozenSet[str] = frozenset(
    {
        "various artists",
        "various",
        "varios artistas",
        "varios",
        "vários artistas",
        "va",
        "v.a.",
    }
)


def normalize_unicode(text: Any, preserve_newlines: bool = False) -> str:
    """
    Converte qualquer entrada textual para Unicode NFC (pré-composto),
    removendo caracteres de controle ASCII invisíveis.
    """
    if text is None:
        return ""
    norm_str = unicodedata.normalize("NFC", str(text))
    if preserve_newlines:
        norm_str = norm_str.replace("\r\n", "\n").replace("\r", "\n")
        norm_str = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", norm_str)
        return norm_str.strip()
    norm_str = re.sub(r"[\x00-\x1f\x7f]", " ", norm_str)
    norm_str = re.sub(r"\s+", " ", norm_str)
    return norm_str.strip()


def extract_featuring(
    title: Any, artist: Any = "", album_artist: Any = ""
) -> Tuple[str, str, str]:
    """
    Detecta padrões como `(feat. X)` ou `ft. X` no título da faixa,
    removendo-os do título e migrando o artista convidado para a tag `artist` da faixa,
    enquanto preserva `album_artist` intacto.

    Retorna `(clean_title, updated_artist, updated_album_artist)`.
    """
    clean_title = normalize_unicode(title)
    curr_artist = normalize_unicode(artist)
    curr_album_artist = normalize_unicode(album_artist)

    if curr_artist and curr_artist.lower() not in UNKNOWN_ARTIST_PLACEHOLDERS:
        artist_feat_match = ARTIST_FEAT_SPLIT_RE.match(curr_artist)
        if artist_feat_match and (
            not curr_album_artist
            or curr_album_artist.lower() in UNKNOWN_ARTIST_PLACEHOLDERS
            or curr_album_artist.lower() == curr_artist.lower()
        ):
            curr_album_artist = normalize_unicode(artist_feat_match.group(1))

    featured_guests: List[str] = []

    def _collect_bracket_feat(match: re.Match) -> str:
        guest = normalize_unicode(match.group(1))
        if guest:
            featured_guests.append(guest)
        return ""

    clean_title = FEAT_BRACKET_RE.sub(_collect_bracket_feat, clean_title)

    inline_match = FEAT_INLINE_RE.search(clean_title)
    if inline_match:
        guest = normalize_unicode(inline_match.group(1))
        if guest:
            featured_guests.append(guest)
        clean_title = clean_title[: inline_match.start()].strip()

    clean_title = normalize_unicode(clean_title)

    if featured_guests:
        if (
            not curr_album_artist
            or curr_album_artist.lower() in UNKNOWN_ARTIST_PLACEHOLDERS
        ) and curr_artist and curr_artist.lower() not in UNKNOWN_ARTIST_PLACEHOLDERS:
            curr_album_artist = curr_artist

        for guest in featured_guests:
            if not curr_artist or curr_artist.lower() in UNKNOWN_ARTIST_PLACEHOLDERS:
                curr_artist = guest
            elif guest.lower() not in curr_artist.lower():
                curr_artist = f"{curr_artist} feat. {guest}"

    return (
        clean_title,
        normalize_unicode(curr_artist),
        normalize_unicode(curr_album_artist),
    )


def clean_semantic_junk(text: Any) -> str:
    """
    Remove sufixos residuais de downloads/rips em títulos e álbuns:
    `(Official Video)`, `[Remastered 2011]`, `(320kbps)`, `[HQ]`, etc.
    """
    cleaned = normalize_unicode(text)
    if not cleaned:
        return ""

    for pattern in BRACKETED_JUNK_PATTERNS:
        cleaned = pattern.sub("", cleaned)

    for pattern in TRAILING_JUNK_PATTERNS:
        cleaned = pattern.sub("", cleaned)

    cleaned = re.sub(r"\(\s*\)|\[\s*\]", "", cleaned)
    cleaned = re.sub(r"^[\s\-–—_:|]+|[\s\-–—_:|]+$", "", cleaned)
    return normalize_unicode(cleaned)


def strip_leading_track_number(
    title: Any, known_track: Optional[Any] = None
) -> str:
    """
    Remove prefixos numéricos redundantes no início do título da música
    (`01 - `, `01. `, `1-01 `, `[01] `, `01 - 01 - `) em loop até estabilizar,
    evitando o bug `01 - 01 - Nome da Música`.
    Preserva títulos que são legitimamente apenas um número (ex: `1979`, `505`).
    """
    cleaned = normalize_unicode(title)
    if not cleaned:
        return ""

    known_track_int: Optional[int] = None
    if known_track is not None:
        m_track = re.match(r"^(\d+)", str(known_track).strip())
        if m_track:
            try:
                known_track_int = int(m_track.group(1))
            except ValueError:
                known_track_int = None

    changed = True
    while changed and cleaned:
        changed = False
        for pat in TRACK_PREFIX_PATTERNS:
            match = pat.match(cleaned)
            if match:
                candidate = normalize_unicode(match.group(1))
                if candidate:
                    cleaned = candidate
                    changed = True
                    break

        if not changed and known_track_int is not None:
            m_known = re.match(r"^(\d{1,3})\s+(.+)$", cleaned)
            if m_known:
                try:
                    prefix_num = int(m_known.group(1))
                    candidate = normalize_unicode(m_known.group(2))
                    if prefix_num == known_track_int and candidate:
                        cleaned = candidate
                        changed = True
                except ValueError:
                    pass

    return cleaned


def parse_and_clean_filename(
    filename: str,
) -> Tuple[Optional[str], Optional[str], str]:
    """
    Analisa um nome de arquivo bruto e extrai `(track_num, artist, title)` higienizados.
    """
    norm_filename = normalize_unicode(filename)
    base = (
        os.path.splitext(norm_filename)[0]
        if "." in norm_filename
        else norm_filename
    )

    base_no_feat, feat_artist, _ = extract_featuring(base)
    cleaned = clean_semantic_junk(base_no_feat)

    track: Optional[str] = None
    artist: Optional[str] = None
    title: str = cleaned

    match_full = re.match(
        r"^(?:(\d{1,2})[-.])?(\d{1,3})\s*(?:[-–—._:]+\s*|\s+)([^-–—]+?)\s*[-–—]+\s*(.+)$",
        cleaned,
    )
    if match_full:
        track = match_full.group(2).strip()
        artist = match_full.group(3).strip()
        title = match_full.group(4).strip()
    else:
        match_track_title = re.match(
            r"^(?:(\d{1,2})[-.])?(\d{1,3})\s*(?:[-–—._:]+\s*|\s+)(.+)$",
            cleaned,
        )
        if match_track_title:
            track = match_track_title.group(2).strip()
            title = match_track_title.group(3).strip()
        else:
            match_artist_title = re.match(
                r"^([^-–—]+?)\s*[-–—]+\s*(.+)$", cleaned
            )
            if match_artist_title:
                artist = match_artist_title.group(1).strip()
                title = match_artist_title.group(2).strip()

    title = strip_leading_track_number(title, known_track=track)
    title = clean_semantic_junk(title)
    title = strip_leading_track_number(title, known_track=track)

    if feat_artist:
        if artist and feat_artist.lower() not in artist.lower():
            artist = f"{artist} feat. {feat_artist}"
        elif not artist:
            artist = feat_artist

    if track:
        try:
            track = f"{int(track):02d}"
        except ValueError:
            pass

    return (
        normalize_unicode(track) if track else None,
        normalize_unicode(artist) if artist else None,
        normalize_unicode(title),
    )


def normalize_metadata_tags(
    tags: Dict[str, Any], filename: Optional[str] = None
) -> Dict[str, Any]:
    """
    Executa o pipeline determinístico completo de higienização e normalização de tags:
    1. Normalização Unicode NFC em todas as strings.
    2. Extração de Featuring do título para o artista da faixa, preservando Album Artist.
    3. Remoção de lixo semântico (`(Official Video)`, `[Remastered]`, `(320kbps)`, `[HQ]`).
    4. Remoção de prefixos numéricos redundantes no título (correção do bug 01 01).
    5. Padronização numérica de faixa (`02d`) e disco.
    """
    normalized = dict(tags) if tags else {}

    raw_title = normalize_unicode(normalized.get("title", ""))
    raw_artist = normalize_unicode(normalized.get("artist", ""))
    raw_album_artist = normalize_unicode(normalized.get("album_artist", ""))
    raw_album = normalize_unicode(normalized.get("album", ""))
    raw_track = normalize_unicode(normalized.get("track", ""))
    raw_disc = normalize_unicode(normalized.get("disc", "1")) or "1"
    raw_year = normalize_unicode(normalized.get("year", ""))
    raw_genre = normalize_unicode(normalized.get("genre", ""))

    if filename and (not raw_title or not raw_artist or not raw_track):
        heur_track, heur_artist, heur_title = parse_and_clean_filename(filename)
        if not raw_title and heur_title:
            raw_title = heur_title
        if not raw_artist and heur_artist:
            raw_artist = heur_artist
        if not raw_track and heur_track:
            raw_track = heur_track

    if raw_track:
        m_tr = re.match(r"^(\d+)", raw_track)
        if m_tr:
            raw_track = f"{int(m_tr.group(1)):02d}"

    if raw_disc:
        m_dc = re.match(r"^(\d+)", raw_disc)
        if m_dc:
            raw_disc = str(int(m_dc.group(1)))

    clean_title, updated_artist, updated_album_artist = extract_featuring(
        raw_title, raw_artist, raw_album_artist
    )

    clean_title = strip_leading_track_number(clean_title, known_track=raw_track)
    clean_title = clean_semantic_junk(clean_title)
    clean_title = strip_leading_track_number(clean_title, known_track=raw_track)

    clean_album = clean_semantic_junk(raw_album)

    if not updated_album_artist:
        m_art = ARTIST_FEAT_SPLIT_RE.match(updated_artist)
        if m_art:
            updated_album_artist = normalize_unicode(m_art.group(1))
        else:
            updated_album_artist = updated_artist

    if raw_year:
        m_yr = re.search(r"(\d{4})", raw_year)
        if m_yr:
            raw_year = m_yr.group(1)

    normalized["title"] = clean_title
    normalized["artist"] = updated_artist
    normalized["album_artist"] = updated_album_artist
    normalized["album"] = clean_album
    normalized["track"] = raw_track
    normalized["disc"] = raw_disc
    normalized["year"] = raw_year
    normalized["genre"] = raw_genre

    if normalized.get("lyrics") and isinstance(normalized["lyrics"], dict):
        lyrics_copy = dict(normalized["lyrics"])
        if lyrics_copy.get("synced"):
            lyrics_copy["synced"] = normalize_unicode(
                lyrics_copy["synced"], preserve_newlines=True
            )
        if lyrics_copy.get("plain"):
            lyrics_copy["plain"] = normalize_unicode(
                lyrics_copy["plain"], preserve_newlines=True
            )
        normalized["lyrics"] = lyrics_copy

    return normalized


def sanitize_fat32_component(name: Any, default: str = "Unknown") -> str:
    """
    Sanitiza um nome de pasta ou arquivo para compatibilidade estrita com FAT32/exFAT/Windows:
    - Converte para Unicode NFC
    - Remove caracteres proibidos (`<, >, :, ", /, \\, |, ?, *`) e caracteres de controle
    - Remove pontos (`.`) e espaços nas extremidades
    - Intercepta e renomeia palavras reservadas do DOS/Windows/FAT (`CON`, `AUX`, `NUL`, `PRN`, `COM1-9`, `LPT1-9`)
    """
    norm = normalize_unicode(name)
    if not norm:
        return default

    sanitized = FAT32_FORBIDDEN_CHARS_RE.sub("", norm)
    sanitized = re.sub(r"\s+", " ", sanitized).strip(". ")

    if not sanitized:
        return default

    stem = sanitized.split(".")[0].strip().upper()
    if stem in RESERVED_FAT_NAMES:
        sanitized = f"_{sanitized}"

    return sanitized if sanitized else default


def _truncate_clean(text: str, max_chars: int) -> str:
    """Trunca uma string removendo pontuação ou espaços pendurados no final."""
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars].rstrip(". -_–—")
    return truncated if truncated else text[:max_chars].rstrip(". ")


def build_safe_relative_path(
    tags: Dict[str, Any],
    ext: str,
    max_length: int = MAX_RELATIVE_PATH_LENGTH,
) -> str:
    """
    Constrói o caminho relativo padronizado `Artista/Álbum/Faixa.ext` garantindo:
    - Regras de Album Artist, Various Artists e Multi-Disco
    - Deduplicação de número de faixa no título (sem `01 - 01`)
    - Sanitização estrita FAT32 e palavras reservadas
    - Comprimento total máximo <= 200 caracteres (protegendo `char[256]` de firmwares em C),
      truncando de forma inteligente o título da música sem afetar o prefixo numérico nem a extensão.
    """
    norm_tags = normalize_metadata_tags(tags)

    ext_clean = normalize_unicode(ext).lower()
    if not ext_clean.startswith("."):
        ext_clean = f".{ext_clean}" if ext_clean else ".mp3"

    album_artist = (
        norm_tags.get("album_artist")
        or norm_tags.get("artist")
        or "Artista Desconhecido"
    )
    if album_artist.strip().lower() in VARIOUS_ARTISTS_ALIASES:
        album_artist = "Various Artists"

    artist_folder = sanitize_fat32_component(
        album_artist, default="Artista Desconhecido"
    )
    album_folder = sanitize_fat32_component(
        norm_tags.get("album") or "Álbum Desconhecido",
        default="Álbum Desconhecido",
    )

    track_raw = norm_tags.get("track") or "01"
    try:
        track_num = f"{int(track_raw):02d}"
    except (ValueError, TypeError):
        track_num = sanitize_fat32_component(track_raw, default="01")

    disc_raw = norm_tags.get("disc") or "1"
    try:
        disc_int = int(disc_raw)
        track_prefix = f"{disc_int}-{track_num}" if disc_int > 1 else track_num
    except (ValueError, TypeError):
        track_prefix = track_num

    raw_title = strip_leading_track_number(
        norm_tags.get("title") or "Faixa", known_track=track_num
    )
    title_clean = sanitize_fat32_component(raw_title, default="Faixa")

    track_artist = norm_tags.get("artist") or ""
    is_diff_artist = False
    if track_artist and album_artist:
        t_art = re.sub(r"\s+", " ", track_artist).strip().lower()
        a_art = re.sub(r"\s+", " ", album_artist).strip().lower()
        if t_art != a_art and a_art not in UNKNOWN_ARTIST_PLACEHOLDERS:
            is_diff_artist = True

    track_artist_clean = (
        sanitize_fat32_component(track_artist, default="")
        if is_diff_artist
        else ""
    )

    def _assemble(art_f: str, alb_f: str, trk_art: str, ttl: str) -> str:
        if is_diff_artist and trk_art:
            fname = f"{track_prefix} - {trk_art} - {ttl}{ext_clean}"
        else:
            fname = f"{track_prefix} - {ttl}{ext_clean}"
        return f"{art_f}/{alb_f}/{fname}"

    rel_path = _assemble(
        artist_folder, album_folder, track_artist_clean, title_clean
    )
    if len(rel_path) <= max_length:
        return rel_path

    if is_diff_artist and track_artist_clean:
        prefix_before_title = f"{track_prefix} - {track_artist_clean} - "
    else:
        prefix_before_title = f"{track_prefix} - "

    fixed_overhead = (
        len(artist_folder)
        + 1
        + len(album_folder)
        + 1
        + len(prefix_before_title)
        + len(ext_clean)
    )
    available_for_title = max_length - fixed_overhead

    if available_for_title < 15:
        artist_folder = sanitize_fat32_component(
            _truncate_clean(artist_folder, 50), default="Artista"
        )
        album_folder = sanitize_fat32_component(
            _truncate_clean(album_folder, 50), default="Album"
        )
        if is_diff_artist and track_artist_clean:
            track_artist_clean = sanitize_fat32_component(
                _truncate_clean(track_artist_clean, 35), default="Artista"
            )
            prefix_before_title = f"{track_prefix} - {track_artist_clean} - "
        fixed_overhead = (
            len(artist_folder)
            + 1
            + len(album_folder)
            + 1
            + len(prefix_before_title)
            + len(ext_clean)
        )
        available_for_title = max(1, max_length - fixed_overhead)

    title_truncated = _truncate_clean(title_clean, available_for_title)
    if not title_truncated:
        title_truncated = "Faixa"[:available_for_title]

    title_truncated = sanitize_fat32_component(title_truncated, default="Faixa")
    if len(title_truncated) > available_for_title:
        title_truncated = title_truncated[:available_for_title].rstrip(". ")

    return _assemble(
        artist_folder, album_folder, track_artist_clean, title_truncated
    )


def build_destination_path(
    dest_dir: str,
    tags: Dict[str, Any],
    ext: str,
    max_relative_length: int = MAX_RELATIVE_PATH_LENGTH,
) -> str:
    """
    Retorna o caminho absoluto final combinando `dest_dir` com o caminho relativo seguro (`<= 200` chars).
    """
    rel_path = build_safe_relative_path(
        tags, ext, max_length=max_relative_length
    )
    parts = rel_path.split("/")
    return os.path.join(dest_dir, *parts)

