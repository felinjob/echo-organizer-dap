"""
Serviço de extração de metadados físicos (incluindo `duration`), resolução de capas
com deduplicação em lote e gravação limpa de containers (MP3 ID3v2.3, FLAC 4KB padding e MP4)
via Mutagen.
"""

import os
import re
import shutil
from typing import Any, Dict, List, Optional, Tuple
import requests
from mutagen import apev2
from mutagen.flac import FLAC, Picture
from mutagen.id3 import (
    APIC,
    ID3,
    TALB,
    TCON,
    TIT2,
    TPE1,
    TPE2,
    TPOS,
    TRCK,
    TYER,
    USLT,
    error as ID3Error,
)
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover

import app.services.cache as cache_service
from app.config import (
    MAX_BYPASS_COVER_BYTES,
    MAX_COVER_DIMENSIONS,
    MAX_FLAC_PADDING_BYTES,
    get_logger,
)
from app.core.cover_processor import is_hardware_safe_jpeg, resize_cover_image
from app.core.normalizer import (
    normalize_metadata_tags,
    normalize_unicode,
    strip_leading_track_number,
)
from app.services.fat32_writer import (
    cleanup_temp_files,
    copy_sequentially,
    fsync_file,
    write_sidecar_lrc_atomic,
)

__all__ = [
    "MAX_COVER_DIMENSIONS",
    "MAX_BYPASS_COVER_BYTES",
    "MAX_FLAC_PADDING_BYTES",
    "is_hardware_safe_jpeg",
    "resize_cover_image",
    "fsync_file",
    "cleanup_temp_files",
    "strip_lrc_timestamps",
    "resolve_album_cover",
    "extract_metadata",
    "write_sidecar_lrc_atomic",
    "clean_and_tag_file",
    "clean_and_tag_mp3",
    "copy_sequentially",
]

logger = get_logger(__name__)

LOCAL_COVER_FILENAMES = (
    "cover.jpg",
    "cover.jpeg",
    "cover.png",
    "folder.jpg",
    "folder.jpeg",
    "folder.png",
    "album.jpg",
    "album.jpeg",
    "album.png",
    "front.jpg",
    "front.jpeg",
    "front.png",
)


def strip_lrc_timestamps(lrc_text: Optional[str]) -> str:
    """
    Remove timestamps LRC e cabeçalhos de metadados para gerar letra limpa em texto plano (NFC).
    """
    if not lrc_text:
        return ""
    norm_text = normalize_unicode(lrc_text, preserve_newlines=True)
    lines: List[str] = []
    for line in norm_text.splitlines():
        if re.match(r"^\[[a-zA-Z]+:.*\]$", line.strip()):
            continue
        cleaned = re.sub(r"\[\d{2,}:\d{2}(?:\.\d{2,3})?\]", "", line).strip()
        if cleaned:
            lines.append(normalize_unicode(cleaned))
    return "\n".join(lines)


def resolve_album_cover(
    source_path: str,
    existing_cover_data: Optional[bytes],
    existing_cover_mime: Optional[str],
    norm_tags: Dict[str, Any],
    resize_cover: bool = True,
) -> Tuple[Optional[bytes], Optional[str]]:
    """
    Resolve e processa a capa do álbum utilizando cache em memória + SQLite (indexado por
    cover_url, hash da imagem embutida ou caminho da imagem local da pasta) para garantir
    que um álbum de N faixas faça apenas 1 download HTTP e 1 redimensionamento no Pillow.
    """
    album_key = cache_service.make_key(
        "album_cover",
        os.path.dirname(os.path.abspath(source_path)),
        norm_tags.get("album_artist", ""),
        norm_tags.get("album", ""),
        norm_tags.get("cover_url", ""),
    )

    if existing_cover_data:
        if not resize_cover:
            return existing_cover_data, existing_cover_mime or "image/jpeg"
        processed = resize_cover_image(
            existing_cover_data, max_size=MAX_COVER_DIMENSIONS
        )
        if processed:
            return processed, "image/jpeg"
        return existing_cover_data, existing_cover_mime or "image/jpeg"

    cover_url = (norm_tags.get("cover_url") or "").strip()
    if cover_url and cover_url.lower().startswith(("http://", "https://")):
        url_cache_key = f"url:{cover_url}"
        cached_url_bytes = cache_service.get_cached_cover_bytes(url_cache_key)
        if cached_url_bytes:
            return cached_url_bytes, "image/jpeg"

        try:
            logger.info("Baixando capa online de %s", cover_url)
            cache_service.record_http_download()
            img_resp = requests.get(cover_url, timeout=10)
            if img_resp.status_code == 200 and img_resp.content:
                raw_online = img_resp.content
                if resize_cover:
                    processed_online = resize_cover_image(
                        raw_online, max_size=MAX_COVER_DIMENSIONS
                    )
                    if processed_online:
                        cache_service.set_cached_cover_bytes(
                            url_cache_key, processed_online
                        )
                        cache_service.set_cached_cover_bytes(
                            album_key, processed_online
                        )
                        return processed_online, "image/jpeg"
                else:
                    cache_service.set_cached_cover_bytes(url_cache_key, raw_online)
                    return raw_online, "image/jpeg"
        except Exception as img_err:
            logger.warning("Falha ao baixar capa online (%s): %s", cover_url, img_err)

    cached_album_bytes = cache_service.get_cached_cover_bytes(album_key)
    if cached_album_bytes:
        return cached_album_bytes, "image/jpeg"

    try:
        source_dir = os.path.dirname(source_path)
        if source_dir and os.path.isdir(source_dir):
            for file_in_dir in os.listdir(source_dir):
                if file_in_dir.lower() in LOCAL_COVER_FILENAMES:
                    local_cover_path = os.path.join(source_dir, file_in_dir)
                    if os.path.isfile(local_cover_path):
                        local_key = f"local:{os.path.abspath(local_cover_path)}"
                        cached_local = cache_service.get_cached_cover_bytes(local_key)
                        if cached_local:
                            return cached_local, "image/jpeg"

                        with open(local_cover_path, "rb") as cover_file:
                            raw_local = cover_file.read()
                        if resize_cover:
                            processed_local = resize_cover_image(
                                raw_local, max_size=MAX_COVER_DIMENSIONS
                            )
                            if processed_local:
                                cache_service.set_cached_cover_bytes(
                                    local_key, processed_local
                                )
                                cache_service.set_cached_cover_bytes(
                                    album_key, processed_local
                                )
                                return processed_local, "image/jpeg"
                        mime = (
                            "image/png"
                            if file_in_dir.lower().endswith(".png")
                            else "image/jpeg"
                        )
                        return raw_local, mime
    except Exception as local_err:
        logger.warning("Falha ao buscar capa local em %s: %s", source_path, local_err)

    return None, None


def extract_metadata(file_path: str) -> Dict[str, Any]:
    """
    Extrai metadados essenciais (incluindo Album Artist, Disc Number, Lyrics e Duration real)
    de arquivos MP3, FLAC e M4A/MP4, normalizando todas as strings para Unicode NFC.
    """
    filename = normalize_unicode(os.path.basename(file_path))
    ext = os.path.splitext(filename)[1].lower()

    metadata: Dict[str, Any] = {
        "title": "",
        "artist": "",
        "album_artist": "",
        "album": "",
        "track": "",
        "disc": "1",
        "year": "",
        "genre": "",
        "duration": 0,
        "has_cover": False,
        "filename": filename,
        "file_path": file_path,
        "extension": ext,
        "lyrics": None,
    }

    embedded_plain = ""

    try:
        if ext == ".mp3":
            audio = MP3(file_path)
            if getattr(audio, "info", None) and getattr(audio.info, "length", None):
                metadata["duration"] = int(round(audio.info.length))

            tags = audio.tags
            if tags is not None:
                if "TIT2" in tags and tags["TIT2"].text:
                    metadata["title"] = normalize_unicode(tags["TIT2"].text[0])
                if "TPE1" in tags and tags["TPE1"].text:
                    metadata["artist"] = normalize_unicode(tags["TPE1"].text[0])
                if "TPE2" in tags and tags["TPE2"].text:
                    metadata["album_artist"] = normalize_unicode(tags["TPE2"].text[0])
                if "TALB" in tags and tags["TALB"].text:
                    metadata["album"] = normalize_unicode(tags["TALB"].text[0])
                if "TRCK" in tags and tags["TRCK"].text:
                    track_str = normalize_unicode(tags["TRCK"].text[0])
                    if "/" in track_str:
                        track_str = track_str.split("/")[0]
                    metadata["track"] = track_str.strip()
                if "TPOS" in tags and tags["TPOS"].text:
                    disc_str = normalize_unicode(tags["TPOS"].text[0])
                    if "/" in disc_str:
                        disc_str = disc_str.split("/")[0]
                    metadata["disc"] = disc_str.strip() or "1"
                if "TYER" in tags and tags["TYER"].text:
                    metadata["year"] = normalize_unicode(tags["TYER"].text[0])[:4]
                elif "TDRC" in tags and tags["TDRC"].text:
                    metadata["year"] = normalize_unicode(tags["TDRC"].text[0])[:4]
                if "TCON" in tags and tags["TCON"].text:
                    metadata["genre"] = normalize_unicode(tags["TCON"].text[0])

                for tag in tags.values():
                    if isinstance(tag, APIC):
                        metadata["has_cover"] = True
                    elif isinstance(tag, USLT):
                        embedded_plain = normalize_unicode(
                            tag.text, preserve_newlines=True
                        )

        elif ext == ".flac":
            audio_flac = FLAC(file_path)
            if getattr(audio_flac, "info", None) and getattr(
                audio_flac.info, "length", None
            ):
                metadata["duration"] = int(round(audio_flac.info.length))

            metadata["title"] = normalize_unicode(audio_flac.get("title", [""])[0])
            metadata["artist"] = normalize_unicode(audio_flac.get("artist", [""])[0])
            metadata["album_artist"] = normalize_unicode(
                audio_flac.get("albumartist", audio_flac.get("album_artist", [""]))[0]
            )
            metadata["album"] = normalize_unicode(audio_flac.get("album", [""])[0])
            track_list = audio_flac.get("tracknumber", [""])
            if track_list:
                t_val = normalize_unicode(track_list[0])
                if "/" in t_val:
                    t_val = t_val.split("/")[0]
                metadata["track"] = t_val.strip()
            disc_list = audio_flac.get("discnumber", [""])
            if disc_list:
                d_val = normalize_unicode(disc_list[0])
                if "/" in d_val:
                    d_val = d_val.split("/")[0]
                metadata["disc"] = d_val.strip() or "1"
            date_list = audio_flac.get("date", [""])
            if date_list:
                metadata["year"] = normalize_unicode(date_list[0])[:4]
            metadata["genre"] = normalize_unicode(audio_flac.get("genre", [""])[0])
            metadata["has_cover"] = len(audio_flac.pictures) > 0
            if "lyrics" in audio_flac and audio_flac["lyrics"]:
                embedded_plain = normalize_unicode(
                    audio_flac["lyrics"][0], preserve_newlines=True
                )

        elif ext in (".m4a", ".mp4"):
            audio_mp4 = MP4(file_path)
            if getattr(audio_mp4, "info", None) and getattr(
                audio_mp4.info, "length", None
            ):
                metadata["duration"] = int(round(audio_mp4.info.length))

            mp4_tags = audio_mp4.tags or {}
            if "\xa9nam" in mp4_tags and mp4_tags["\xa9nam"]:
                metadata["title"] = normalize_unicode(mp4_tags["\xa9nam"][0])
            if "\xa9ART" in mp4_tags and mp4_tags["\xa9ART"]:
                metadata["artist"] = normalize_unicode(mp4_tags["\xa9ART"][0])
            if "aART" in mp4_tags and mp4_tags["aART"]:
                metadata["album_artist"] = normalize_unicode(mp4_tags["aART"][0])
            if "\xa9alb" in mp4_tags and mp4_tags["\xa9alb"]:
                metadata["album"] = normalize_unicode(mp4_tags["\xa9alb"][0])
            if "trkn" in mp4_tags and mp4_tags["trkn"]:
                metadata["track"] = str(mp4_tags["trkn"][0][0])
            if "disk" in mp4_tags and mp4_tags["disk"]:
                metadata["disc"] = str(mp4_tags["disk"][0][0])
            if "\xa9day" in mp4_tags and mp4_tags["\xa9day"]:
                metadata["year"] = normalize_unicode(mp4_tags["\xa9day"][0])[:4]
            if "\xa9gen" in mp4_tags and mp4_tags["\xa9gen"]:
                metadata["genre"] = normalize_unicode(mp4_tags["\xa9gen"][0])
            metadata["has_cover"] = "covr" in mp4_tags and len(mp4_tags["covr"]) > 0
            if "\xa9lyr" in mp4_tags and mp4_tags["\xa9lyr"]:
                embedded_plain = normalize_unicode(
                    mp4_tags["\xa9lyr"][0], preserve_newlines=True
                )

    except Exception as exc:
        logger.warning("Erro ao ler tags de %s: %s", file_path, exc)

    external_content = ""
    lrc_path = os.path.splitext(file_path)[0] + ".lrc"
    if os.path.exists(lrc_path):
        try:
            with open(lrc_path, "r", encoding="utf-8-sig", errors="ignore") as lrc_f:
                external_content = normalize_unicode(
                    lrc_f.read(), preserve_newlines=True
                )
        except Exception as exc:
            logger.warning("Erro ao ler sidecar .lrc de %s: %s", file_path, exc)

    lyrics_data: Optional[Dict[str, Optional[str]]] = None

    def _is_synced(content: str) -> bool:
        return bool(re.search(r"\[\d{2,}:\d{2}", content))

    if external_content:
        lyrics_data = {"synced": None, "plain": None}
        if _is_synced(external_content):
            lyrics_data["synced"] = external_content
        else:
            lyrics_data["plain"] = external_content

    if embedded_plain:
        if not lyrics_data:
            lyrics_data = {"synced": None, "plain": None}
        if _is_synced(embedded_plain):
            if not lyrics_data["synced"]:
                lyrics_data["synced"] = embedded_plain
        else:
            if not lyrics_data["plain"]:
                lyrics_data["plain"] = embedded_plain

    metadata["lyrics"] = lyrics_data
    return metadata


def clean_and_tag_file(
    source_path: str,
    dest_path: str,
    clean_tags: Dict[str, Any],
    resize_cover: bool = True,
    write_lrc: bool = True,
) -> bool:
    """
    Grava o arquivo de áudio de forma transacional/atômica:
    1. Copia para arquivo temporário (`dest_path + '.tmp'`)
    2. Injeta as tags higienizadas (NFC), limpa estruturas legadas (ID3v1/APEv2/FLAC pictures/padding)
       e aplica capa 500x500 4:2:0 com cache por álbum
    3. Executa `os.fsync()` no descritor do arquivo `.tmp`
    4. Renomeia atomicamente (`os.replace`) para a extensão final (`.mp3`, `.flac`, `.m4a`) e executa `fsync`
    5. Se `write_lrc=True`, grava o sidecar `.lrc` atomicamente em `utf-8-sig` (quando operado em modo
       Two-Pass via `copy_sequentially`, `write_lrc=False` na Passada 1 e gravado separadamente na Passada 2).
    """
    dest_dir = os.path.dirname(os.path.abspath(dest_path))
    os.makedirs(dest_dir, exist_ok=True)

    ext = os.path.splitext(dest_path)[1].lower()
    norm_tags = normalize_metadata_tags(clean_tags)
    tmp_path = dest_path + ".tmp"

    try:
        shutil.copy2(source_path, tmp_path)

        existing_cover_data: Optional[bytes] = None
        existing_cover_mime: Optional[str] = None

        if ext == ".mp3":
            try:
                audio_id3 = ID3(tmp_path)
                for tag in audio_id3.values():
                    if isinstance(tag, APIC):
                        existing_cover_data = tag.data
                        existing_cover_mime = tag.mime
                        break
            except ID3Error:
                audio_id3 = ID3()
                audio_id3.save(tmp_path, v2_version=3, v1=0)
        elif ext == ".flac":
            audio_flac = FLAC(tmp_path)
            if audio_flac.pictures:
                existing_cover_data = audio_flac.pictures[0].data
                existing_cover_mime = audio_flac.pictures[0].mime
        elif ext in (".m4a", ".mp4"):
            audio_mp4 = MP4(tmp_path)
            if audio_mp4.tags and "covr" in audio_mp4.tags and audio_mp4.tags["covr"]:
                existing_cover_data = bytes(audio_mp4.tags["covr"][0])
                existing_cover_mime = "image/jpeg"

        cover_data, cover_mime = resolve_album_cover(
            source_path=source_path,
            existing_cover_data=existing_cover_data,
            existing_cover_mime=existing_cover_mime,
            norm_tags=norm_tags,
            resize_cover=resize_cover,
        )

        plain_lyrics = ""
        if norm_tags.get("lyrics") and isinstance(norm_tags["lyrics"], dict):
            plain_lyrics = norm_tags["lyrics"].get("plain") or ""
            if not plain_lyrics and norm_tags["lyrics"].get("synced"):
                plain_lyrics = strip_lrc_timestamps(norm_tags["lyrics"]["synced"])
            plain_lyrics = normalize_unicode(plain_lyrics, preserve_newlines=True)

        title_to_write = strip_leading_track_number(
            norm_tags.get("title", ""), known_track=norm_tags.get("track")
        )
        artist_to_write = normalize_unicode(norm_tags.get("artist", ""))
        album_artist_to_write = normalize_unicode(norm_tags.get("album_artist", ""))
        album_to_write = normalize_unicode(norm_tags.get("album", ""))
        year_to_write = normalize_unicode(norm_tags.get("year", ""))
        genre_to_write = normalize_unicode(norm_tags.get("genre", ""))

        if ext == ".mp3":
            try:
                apev2.delete(tmp_path)
            except Exception:
                pass

            try:
                audio_id3 = ID3(tmp_path)
                audio_id3.clear()
            except ID3Error:
                audio_id3 = ID3()

            if title_to_write:
                audio_id3.add(TIT2(encoding=1, text=[title_to_write]))
            if artist_to_write:
                audio_id3.add(TPE1(encoding=1, text=[artist_to_write]))
            if album_artist_to_write:
                audio_id3.add(TPE2(encoding=1, text=[album_artist_to_write]))
            if album_to_write:
                audio_id3.add(TALB(encoding=1, text=[album_to_write]))
            if norm_tags.get("track"):
                try:
                    track_val = int(norm_tags["track"])
                    audio_id3.add(TRCK(encoding=1, text=[f"{track_val:02d}"]))
                except (ValueError, TypeError):
                    audio_id3.add(
                        TRCK(encoding=1, text=[normalize_unicode(norm_tags["track"])])
                    )
            if norm_tags.get("disc"):
                try:
                    disc_val = int(norm_tags["disc"])
                    audio_id3.add(TPOS(encoding=1, text=[str(disc_val)]))
                except (ValueError, TypeError):
                    audio_id3.add(
                        TPOS(encoding=1, text=[normalize_unicode(norm_tags["disc"])])
                    )
            if year_to_write:
                audio_id3.add(TYER(encoding=1, text=[year_to_write]))
            if genre_to_write:
                audio_id3.add(TCON(encoding=1, text=[genre_to_write]))

            if plain_lyrics:
                audio_id3.add(
                    USLT(encoding=1, lang="eng", desc="Lyrics", text=plain_lyrics)
                )

            if cover_data:
                audio_id3.add(
                    APIC(
                        encoding=3,
                        mime=cover_mime or "image/jpeg",
                        type=3,
                        desc="Cover",
                        data=cover_data,
                    )
                )

            audio_id3.save(
                tmp_path,
                v2_version=3,
                v1=0,
                padding=lambda info: min(info.size, MAX_FLAC_PADDING_BYTES),
            )
            try:
                apev2.delete(tmp_path)
            except Exception:
                pass

        elif ext == ".flac":
            audio_flac = FLAC(tmp_path)
            audio_flac.clear()
            audio_flac.clear_pictures()

            if title_to_write:
                audio_flac["title"] = [title_to_write]
            if artist_to_write:
                audio_flac["artist"] = [artist_to_write]
            if album_artist_to_write:
                audio_flac["albumartist"] = [album_artist_to_write]
            if album_to_write:
                audio_flac["album"] = [album_to_write]
            if norm_tags.get("track"):
                try:
                    audio_flac["tracknumber"] = [f"{int(norm_tags['track']):02d}"]
                except (ValueError, TypeError):
                    audio_flac["tracknumber"] = [normalize_unicode(norm_tags["track"])]
            if norm_tags.get("disc"):
                try:
                    audio_flac["discnumber"] = [str(int(norm_tags["disc"]))]
                except (ValueError, TypeError):
                    audio_flac["discnumber"] = [normalize_unicode(norm_tags["disc"])]
            if year_to_write:
                audio_flac["date"] = [year_to_write]
            if genre_to_write:
                audio_flac["genre"] = [genre_to_write]

            if plain_lyrics:
                audio_flac["lyrics"] = [plain_lyrics]

            if cover_data:
                pic = Picture()
                pic.data = cover_data
                pic.mime = cover_mime or "image/jpeg"
                pic.type = 3
                pic.desc = "Cover"
                audio_flac.add_picture(pic)

            audio_flac.save(
                tmp_path,
                deleteid3=True,
                padding=lambda info: min(info.size, MAX_FLAC_PADDING_BYTES),
            )

        elif ext in (".m4a", ".mp4"):
            audio_mp4 = MP4(tmp_path)
            audio_mp4.clear()
            if title_to_write:
                audio_mp4["\xa9nam"] = [title_to_write]
            if artist_to_write:
                audio_mp4["\xa9ART"] = [artist_to_write]
            if album_artist_to_write:
                audio_mp4["aART"] = [album_artist_to_write]
            if album_to_write:
                audio_mp4["\xa9alb"] = [album_to_write]
            if norm_tags.get("track"):
                try:
                    audio_mp4["trkn"] = [(int(norm_tags["track"]), 0)]
                except (ValueError, TypeError):
                    pass
            if norm_tags.get("disc"):
                try:
                    audio_mp4["disk"] = [(int(norm_tags["disc"]), 0)]
                except (ValueError, TypeError):
                    pass
            if year_to_write:
                audio_mp4["\xa9day"] = [year_to_write]
            if genre_to_write:
                audio_mp4["\xa9gen"] = [genre_to_write]

            if plain_lyrics:
                audio_mp4["\xa9lyr"] = [plain_lyrics]

            if cover_data:
                fmt = MP4Cover.FORMAT_JPEG
                if cover_mime == "image/png":
                    fmt = MP4Cover.FORMAT_PNG
                audio_mp4["covr"] = [MP4Cover(cover_data, imageformat=fmt)]
            audio_mp4.save(tmp_path)

        fsync_file(tmp_path)
        os.replace(tmp_path, dest_path)
        fsync_file(dest_path)

        if write_lrc:
            write_sidecar_lrc_atomic(dest_path, norm_tags)

        return True
    except Exception as exc:
        logger.error("Erro ao gravar arquivo %s: %s", dest_path, exc)
        for path_to_clean in (tmp_path, dest_path):
            if os.path.exists(path_to_clean):
                try:
                    os.remove(path_to_clean)
                except Exception:
                    pass
        raise exc


def clean_and_tag_mp3(
    source_path: str,
    dest_path: str,
    clean_tags: Dict[str, Any],
    resize_cover: bool = True,
) -> bool:
    """Alias de compatibilidade retroativa para `clean_and_tag_file`."""
    return clean_and_tag_file(source_path, dest_path, clean_tags, resize_cover)

