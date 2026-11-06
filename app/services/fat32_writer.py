"""
Motor de gravação física sequencial em Duas Passadas (Two-Pass Album Write),
desfragmentação por staging e flush explícito (`os.fsync`) para sistemas FAT32 embarcados.
"""

import os
import re
import shutil
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.config import SUPPORTED_AUDIO_EXTENSIONS, get_logger
from app.core.normalizer import normalize_metadata_tags, normalize_unicode

__all__ = [
    "fsync_file",
    "cleanup_temp_files",
    "write_sidecar_lrc_atomic",
    "copy_sequentially",
]

logger = get_logger(__name__)


def fsync_file(file_path: str) -> None:
    """
    Força o descarregamento do buffer de escrita do sistema operacional (Write Cache)
    diretamente para o dispositivo físico (cartão SD / tabela FAT32).
    """
    if not file_path or not os.path.exists(file_path):
        return
    try:
        with open(file_path, "r+b") as file_obj:
            file_obj.flush()
            os.fsync(file_obj.fileno())
    except Exception:
        try:
            fd = os.open(file_path, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except Exception:
            pass


def cleanup_temp_files(directory: str) -> None:
    """
    Remove quaisquer arquivos temporários (.tmp) ou diretórios de staging residuais.
    """
    if not directory or not os.path.exists(directory):
        return
    try:
        for root, dirs, files in os.walk(directory):
            for fname in files:
                if fname.endswith(".tmp"):
                    try:
                        os.remove(os.path.join(root, fname))
                    except Exception:
                        pass
            for dname in list(dirs):
                if dname.startswith(".__echo_stage_"):
                    try:
                        shutil.rmtree(os.path.join(root, dname), ignore_errors=True)
                    except Exception:
                        pass
    except Exception:
        pass


def write_sidecar_lrc_atomic(
    dest_audio_path: str, tags: Dict[str, Any]
) -> Optional[str]:
    """
    Grava atomicamente o arquivo `.lrc` externo correspondente a um arquivo de áudio
    em UTF-8 com BOM (`utf-8-sig`), utilizando escrita transacional (`.lrc.tmp` -> `fsync` -> `replace` -> `fsync`).
    Retorna o caminho do `.lrc` gravado ou `None` se não houver letras.
    """
    if not tags or not isinstance(tags.get("lyrics"), dict):
        return None

    lyrics_dict = tags["lyrics"]
    lyrics_to_save = lyrics_dict.get("synced") or lyrics_dict.get("plain")
    if not lyrics_to_save:
        return None

    lyrics_norm = normalize_unicode(lyrics_to_save, preserve_newlines=True)
    if not lyrics_norm:
        return None

    lrc_path = os.path.splitext(dest_audio_path)[0] + ".lrc"
    lrc_tmp_path = lrc_path + ".tmp"

    try:
        with open(lrc_tmp_path, "w", encoding="utf-8-sig") as lrc_file:
            lrc_file.write(lyrics_norm)
            lrc_file.flush()
            os.fsync(lrc_file.fileno())
        os.replace(lrc_tmp_path, lrc_path)
        fsync_file(lrc_path)
        return lrc_path
    except Exception as lrc_err:
        logger.warning("Falha ao gravar arquivo .lrc %s: %s", lrc_path, lrc_err)
        if os.path.exists(lrc_tmp_path):
            try:
                os.remove(lrc_tmp_path)
            except Exception:
                pass
        return None


def _parse_sort_numbers(tags: Dict[str, Any], filename: str = "") -> Tuple[int, int]:
    """
    Extrai a tupla numérica estrita `(disc_number, track_number)` a partir das tags ou do nome de arquivo.
    """
    disc_num = 1
    track_num = 999

    if tags:
        m_d = re.match(r"^(\d+)", str(tags.get("disc", "1")).strip())
        if m_d:
            disc_num = int(m_d.group(1))
        m_t = re.match(r"^(\d+)", str(tags.get("track", "")).strip())
        if m_t:
            track_num = int(m_t.group(1))

    if track_num == 999 and filename:
        m_fn = re.match(r"^(?:(\d+)-)?(\d+)", os.path.basename(filename).strip())
        if m_fn:
            if m_fn.group(1):
                disc_num = int(m_fn.group(1))
            track_num = int(m_fn.group(2))

    return (disc_num, track_num)


def copy_sequentially(
    files_mapping: List[Dict[str, Any]],
    on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
    check_control: Optional[Callable[[], str]] = None,
) -> List[Dict[str, Any]]:
    """
    Processa o lote completo agrupando por diretório de álbum e executando a gravação
    FAT32 em Duas Passadas (Two-Pass Album Write):
    - Passada 1 (Exclusiva de Áudio): Grava todos os arquivos de áudio em sequência
      estrita `(disc_number, track_number)` usando arquivos `.tmp` -> injeção de tags ->
      `os.fsync()` -> `os.replace()` para ocupar os primeiros slots físicos contíguos da tabela FAT32.
    - Passada 2 (Exclusiva de Sidecars `.lrc`): Somente após todas as faixas de áudio do álbum
      estarem gravadas, grava e dá `os.fsync()` nos arquivos `.lrc` correspondentes ao final da tabela.
    - Para pastas de álbum que já existiam parcialmente no destino, monta o álbum completo
      em uma pasta temporária oculta no mesmo volume de destino e substitui ao final,
      eliminando fragmentação de slots deletados na tabela FAT32.
    """
    from app.services.audio_tagger import clean_and_tag_file

    results: List[Dict[str, Any]] = []
    if not files_mapping:
        return results

    albums_map: Dict[str, List[Dict[str, Any]]] = {}
    for item in files_mapping:
        album_dir = os.path.abspath(os.path.dirname(item["dest"]))
        albums_map.setdefault(album_dir, []).append(item)

    total_files = len(files_mapping)
    processed_count = 0

    for album_dir in sorted(albums_map.keys(), key=lambda p: p.lower()):
        album_items = albums_map[album_dir]

        sorted_items = sorted(
            album_items,
            key=lambda x: (
                *_parse_sort_numbers(x.get("tags", {}), x.get("dest", "")),
                os.path.basename(x["dest"]).lower(),
            ),
        )

        album_name = os.path.basename(album_dir)
        parent_dir = os.path.dirname(album_dir)
        os.makedirs(parent_dir, exist_ok=True)

        use_staging = False
        existing_kept_audio: List[Tuple[int, int, str, str]] = []
        existing_kept_lrc: List[Tuple[int, int, str, str]] = []

        incoming_basenames_lower = {
            os.path.basename(it["dest"]).lower() for it in sorted_items
        }
        incoming_stems_lower = {
            os.path.splitext(os.path.basename(it["dest"]))[0].lower()
            for it in sorted_items
        }

        if os.path.exists(album_dir) and os.path.isdir(album_dir):
            try:
                existing_entries = [
                    e for e in os.listdir(album_dir) if not e.endswith(".tmp")
                ]
                if existing_entries:
                    use_staging = True
                    for entry in existing_entries:
                        full_existing = os.path.join(album_dir, entry)
                        if not os.path.isfile(full_existing):
                            continue
                        ext_low = os.path.splitext(entry)[1].lower()
                        stem_low = os.path.splitext(entry)[0].lower()
                        if (
                            ext_low in SUPPORTED_AUDIO_EXTENSIONS
                            and entry.lower() not in incoming_basenames_lower
                        ):
                            d_num, t_num = _parse_sort_numbers({}, entry)
                            existing_kept_audio.append(
                                (d_num, t_num, entry, full_existing)
                            )
                        elif (
                            ext_low == ".lrc"
                            and stem_low not in incoming_stems_lower
                        ):
                            d_num, t_num = _parse_sort_numbers({}, entry)
                            existing_kept_lrc.append(
                                (d_num, t_num, entry, full_existing)
                            )
            except Exception:
                use_staging = False

        staging_dir: Optional[str] = None
        work_dir = album_dir
        if use_staging:
            staging_dir = os.path.join(
                parent_dir, f".__echo_stage_{uuid.uuid4().hex[:8]}"
            )
            os.makedirs(staging_dir, exist_ok=True)
            work_dir = staging_dir
        else:
            os.makedirs(album_dir, exist_ok=True)

        pass1_queue: List[Dict[str, Any]] = []
        for it in sorted_items:
            d_num, t_num = _parse_sort_numbers(it.get("tags", {}), it["dest"])
            pass1_queue.append(
                {
                    "kind": "new",
                    "disc": d_num,
                    "track": t_num,
                    "filename": os.path.basename(it["dest"]),
                    "item": it,
                }
            )
        for d_num, t_num, entry_name, full_existing in existing_kept_audio:
            pass1_queue.append(
                {
                    "kind": "existing",
                    "disc": d_num,
                    "track": t_num,
                    "filename": entry_name,
                    "source_existing": full_existing,
                }
            )
        pass1_queue.sort(key=lambda x: (x["disc"], x["track"], x["filename"].lower()))

        album_aborted = False
        successful_new_items: List[Tuple[Dict[str, Any], str]] = []

        try:
            # =========================================================================
            # PASSADA 1 (EXCLUSIVA DE ÁUDIO): Aloca todas as faixas nos primeiros slots
            # =========================================================================
            for q_entry in pass1_queue:
                if check_control:
                    ctrl = check_control()
                    if ctrl == "cancelled":
                        album_aborted = True
                        break

                if q_entry["kind"] == "existing":
                    src_ex = q_entry["source_existing"]
                    dst_ex = os.path.join(work_dir, q_entry["filename"])
                    tmp_ex = dst_ex + ".tmp"
                    shutil.copy2(src_ex, tmp_ex)
                    fsync_file(tmp_ex)
                    os.replace(tmp_ex, dst_ex)
                    fsync_file(dst_ex)
                    continue

                item = q_entry["item"]
                src = item["source"]
                final_dst = item["dest"]
                work_dst = os.path.join(work_dir, os.path.basename(final_dst))
                tags = normalize_metadata_tags(
                    item.get("tags", {}), filename=os.path.basename(src)
                )

                if on_progress:
                    on_progress(
                        {
                            "stage": "pass_1_audio",
                            "album": album_name,
                            "current_file": os.path.basename(final_dst),
                            "processed": processed_count,
                            "total": total_files,
                        }
                    )

                try:
                    clean_and_tag_file(
                        source_path=src,
                        dest_path=work_dst,
                        clean_tags=tags,
                        resize_cover=True,
                        write_lrc=False,
                    )
                    successful_new_items.append((item, work_dst))
                    processed_count += 1
                    results.append(
                        {"source": src, "dest": final_dst, "status": "success"}
                    )
                    if on_progress:
                        on_progress(
                            {
                                "stage": "pass_1_audio",
                                "album": album_name,
                                "current_file": os.path.basename(final_dst),
                                "processed": processed_count,
                                "total": total_files,
                                "item_result": {
                                    "source": src,
                                    "dest": final_dst,
                                    "status": "success",
                                },
                            }
                        )
                except Exception as exc:
                    processed_count += 1
                    err_res = {
                        "source": src,
                        "dest": final_dst,
                        "status": "error",
                        "message": str(exc),
                    }
                    results.append(err_res)
                    if on_progress:
                        on_progress(
                            {
                                "stage": "pass_1_audio",
                                "album": album_name,
                                "current_file": os.path.basename(final_dst),
                                "processed": processed_count,
                                "total": total_files,
                                "item_result": err_res,
                            }
                        )

            # =========================================================================
            # PASSADA 2 (EXCLUSIVA DE SIDECARS .LRC): Aloca .lrc após todos os áudios
            # =========================================================================
            if not album_aborted:
                for item, work_dst in successful_new_items:
                    if check_control and check_control() == "cancelled":
                        album_aborted = True
                        break

                    tags = normalize_metadata_tags(
                        item.get("tags", {}), filename=os.path.basename(item["source"])
                    )
                    if tags.get("lyrics") and (
                        tags["lyrics"].get("synced") or tags["lyrics"].get("plain")
                    ):
                        if on_progress:
                            on_progress(
                                {
                                    "stage": "pass_2_lrc",
                                    "album": album_name,
                                    "current_file": os.path.splitext(
                                        os.path.basename(work_dst)
                                    )[0]
                                    + ".lrc",
                                    "processed": processed_count,
                                    "total": total_files,
                                }
                            )
                        write_sidecar_lrc_atomic(work_dst, tags)

                if not album_aborted and use_staging:
                    for _, _, entry_name, full_existing_lrc in sorted(
                        existing_kept_lrc, key=lambda x: (x[0], x[1], x[2].lower())
                    ):
                        dst_lrc = os.path.join(work_dir, entry_name)
                        tmp_lrc = dst_lrc + ".tmp"
                        shutil.copy2(full_existing_lrc, tmp_lrc)
                        fsync_file(tmp_lrc)
                        os.replace(tmp_lrc, dst_lrc)
                        fsync_file(dst_lrc)

            if use_staging and staging_dir:
                if not album_aborted:
                    backup_dir = os.path.join(
                        parent_dir, f".__echo_old_{uuid.uuid4().hex[:8]}"
                    )
                    try:
                        os.replace(album_dir, backup_dir)
                        os.replace(staging_dir, album_dir)
                        shutil.rmtree(backup_dir, ignore_errors=True)
                    except Exception as swap_err:
                        logger.warning(
                            "Fallback de troca de pasta staging para %s: %s",
                            album_dir,
                            swap_err,
                        )
                        if os.path.exists(backup_dir) and not os.path.exists(album_dir):
                            os.replace(backup_dir, album_dir)
                else:
                    shutil.rmtree(staging_dir, ignore_errors=True)
                    break

            if album_aborted:
                cleanup_temp_files(work_dir)
                break

        finally:
            if staging_dir and os.path.exists(staging_dir):
                shutil.rmtree(staging_dir, ignore_errors=True)
            cleanup_temp_files(album_dir)

    return results

