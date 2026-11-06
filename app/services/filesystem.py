"""
Serviço de navegação nativa no sistema de arquivos (Windows, Linux e macOS).

Substitui o uso de subprocessos gráficos (PowerShell, Zenity, Tkinter) e heurísticas
de busca cega por inspeção direta de pontos de montagem e listagem segura com os.scandir.
"""

import ctypes
import os
import platform
import shutil
import string
from typing import Any, Dict, List, Optional, Set

from app.config import BASE_DIR, SUPPORTED_AUDIO_EXTENSIONS
from app.core.normalizer import normalize_unicode


def get_disk_space_info(path: str) -> Dict[str, Any]:
    """
    Calcula o espaço total e livre do volume onde `path` está montado.
    """
    try:
        usage = shutil.disk_usage(path)
        return {
            "total_bytes": usage.total,
            "free_bytes": usage.free,
            "total_gb": round(usage.total / (1024 ** 3), 2),
            "free_gb": round(usage.free / (1024 ** 3), 2),
        }
    except Exception:
        return {"total_bytes": 0, "free_bytes": 0, "total_gb": 0.0, "free_gb": 0.0}


def get_windows_drive_info(drive_root: str) -> Dict[str, str]:
    """
    Obtém o tipo de unidade (Removível/SD Card, Fixo, Rede) e o rótulo de volume
    no Windows utilizando chamadas nativas Win32 via `ctypes`.
    """
    drive_type_str = "drive"
    label = ""
    try:
        kernel32 = ctypes.windll.kernel32
        dtype = kernel32.GetDriveTypeW(ctypes.c_wchar_p(drive_root))
        if dtype == 2:
            drive_type_str = "removable"
        elif dtype == 3:
            drive_type_str = "fixed"
        elif dtype == 4:
            drive_type_str = "network"

        vol_name_buf = ctypes.create_unicode_buffer(261)
        fs_name_buf = ctypes.create_unicode_buffer(261)
        serial_num = ctypes.c_ulong(0)
        max_comp_len = ctypes.c_ulong(0)
        fs_flags = ctypes.c_ulong(0)

        res = kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(drive_root),
            vol_name_buf,
            ctypes.sizeof(vol_name_buf),
            ctypes.byref(serial_num),
            ctypes.byref(max_comp_len),
            ctypes.byref(fs_flags),
            fs_name_buf,
            ctypes.sizeof(fs_name_buf),
        )
        if res:
            label = vol_name_buf.value.strip()
            fs_name = fs_name_buf.value.strip()
            if fs_name:
                label = f"{label} ({fs_name})" if label else fs_name
    except Exception:
        pass
    return {"type": drive_type_str, "label": label}


def discover_filesystem_roots() -> Dict[str, Any]:
    """
    Descobre todas as raízes navegáveis do sistema operacional:
    - Unidades de disco e cartões SD montados no Windows (C:\\ .. Z:\\)
    - Pontos de montagem no Linux/macOS (/, /media, /mnt, /run/media, /Volumes)
    - Diretórios padrão do usuário (~/Music, ~/Downloads, ~/Desktop)
    """
    roots: List[Dict[str, Any]] = []
    seen_paths: Set[str] = set()
    system_os = platform.system()

    def _add_root(name: str, path: str, kind: str = "folder") -> None:
        if not path:
            return
        try:
            abs_path = os.path.abspath(path)
            if not os.path.exists(abs_path) or not os.path.isdir(abs_path):
                return
            norm_key = abs_path.lower() if system_os == "Windows" else abs_path
            if norm_key in seen_paths:
                return
            seen_paths.add(norm_key)
            space = get_disk_space_info(abs_path)
            roots.append(
                {
                    "name": name,
                    "path": abs_path,
                    "kind": kind,
                    **space,
                }
            )
        except Exception:
            pass

    # 1. Unidades físicas e removíveis no Windows
    if system_os == "Windows":
        for letter in string.ascii_uppercase:
            drive_path = f"{letter}:\\"
            if os.path.exists(drive_path):
                win_info = get_windows_drive_info(drive_path)
                kind = win_info["type"]
                lbl = win_info["label"]
                prefix = "💾 Cartão SD / Removível" if kind == "removable" else "🖴 Disco Local"
                display_name = f"{prefix} ({letter}:) {lbl}".strip()
                _add_root(display_name, drive_path, kind=kind)
    else:
        # 2. Pontos de montagem no Linux e macOS
        _add_root("Raiz do Sistema (/)", "/", kind="fixed")
        for mount_base in ("/media", "/mnt", "/run/media", "/Volumes"):
            if os.path.exists(mount_base) and os.path.isdir(mount_base):
                _add_root(f"Montagens ({mount_base})", mount_base, kind="removable")
                try:
                    for sub in sorted(os.listdir(mount_base)):
                        sub_p = os.path.join(mount_base, sub)
                        if os.path.isdir(sub_p):
                            _add_root(f"Mídia: {sub}", sub_p, kind="removable")
                except Exception:
                    pass

    # 3. Atalhos de diretórios do usuário
    home = os.path.expanduser("~")
    if os.path.exists(home):
        _add_root("Pasta do Usuário (~)", home, kind="home")
        user_shortcuts = (
            ("Músicas", "Music"),
            ("Músicas (PT)", "Música"),
            ("Downloads", "Downloads"),
            ("Área de Trabalho", "Desktop"),
            ("Documentos", "Documents"),
        )
        for folder_label, folder_sub in user_shortcuts:
            candidate = os.path.join(home, folder_sub)
            if os.path.exists(candidate) and os.path.isdir(candidate):
                _add_root(f"Atalho: {folder_label}", candidate, kind="shortcut")

    _add_root("Diretório do Projeto", BASE_DIR, kind="project")
    return {"roots": roots, "os": system_os}


def list_directory_safe(
    raw_path: str, page: int = 1, page_size: int = 200
) -> Dict[str, Any]:
    """
    Lista subpastas diretas e conta arquivos de áudio compatíveis em `raw_path`
    de forma paginada, rápida (`os.scandir`) e segura contra Path Traversal.

    Levanta ValueError, FileNotFoundError ou PermissionError para tratamento HTTP.
    """
    if not raw_path or "\x00" in raw_path:
        raise ValueError("Caminho de diretório inválido.")

    target_path = os.path.abspath(os.path.expanduser(raw_path.strip()))
    if not os.path.exists(target_path):
        raise FileNotFoundError("Diretório não encontrado.")
    if not os.path.isdir(target_path):
        raise ValueError("O caminho informado não é um diretório.")

    parent_path: Optional[str] = os.path.dirname(target_path)
    if parent_path == target_path:
        parent_path = None

    directories: List[Dict[str, str]] = []
    audio_files_count = 0

    with os.scandir(target_path) as entries:
        for entry in entries:
            try:
                name = normalize_unicode(entry.name)
                if name.startswith(".__echo_"):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    directories.append({"name": name, "path": entry.path})
                elif entry.is_file(follow_symlinks=False):
                    if name.lower().endswith(SUPPORTED_AUDIO_EXTENSIONS):
                        audio_files_count += 1
            except (PermissionError, OSError):
                continue

    directories.sort(key=lambda d: d["name"].lower())
    total_dirs = len(directories)
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    paginated_dirs = directories[start_idx:end_idx]

    space_info = get_disk_space_info(target_path)

    return {
        "path": target_path,
        "parent": parent_path,
        "directories": paginated_dirs,
        "total_directories": total_dirs,
        "page": page,
        "page_size": page_size,
        "audio_files_count": audio_files_count,
        **space_info,
    }

