"""
Configurações globais, constantes de hardware embarcado (DAP) e setup de logging.

Centraliza os limites físicos de dispositivos embarcados (SoCs Rockchip, Ingenic e
Actions, como o SnoSky Echo Mini) e os parâmetros operacionais/segurança do servidor.
Valores sensíveis ao ambiente podem ser sobrescritos por variáveis `ECHO_*`.
"""

import logging
import os
import threading
from typing import FrozenSet, Tuple

from app import __version__

APP_VERSION: str = __version__

# Evento global de encerramento para interromper sleeps e loops de rede imediatamente no CTRL+C
SHUTDOWN_EVENT: threading.Event = threading.Event()

# Diretórios base da aplicação
BASE_DIR: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR: str = os.path.join(BASE_DIR, "frontend")

# Banco de dados SQLite local para cache persistente
DEFAULT_DB_PATH: str = os.environ.get(
    "ECHO_CACHE_DB_PATH",
    os.path.join(BASE_DIR, "echo_cache.db"),
)

# Extensões de áudio suportadas pelo motor de extração e gravação
SUPPORTED_AUDIO_EXTENSIONS: Tuple[str, ...] = (".mp3", ".flac", ".m4a", ".mp4")

# Limites estritos de hardware para decodificadores de DAPs e tabelas FAT32
MAX_RELATIVE_PATH_LENGTH: int = 200
MAX_COVER_DIMENSIONS: Tuple[int, int] = (500, 500)
MAX_BYPASS_COVER_BYTES: int = 150 * 1024  # 150 KB
MAX_FLAC_PADDING_BYTES: int = 4096        # 4 KB
JPEG_QUALITY: int = 85

# Limites operacionais (proteção contra varreduras e payloads desproporcionais)
MAX_SCAN_FILES: int = int(os.environ.get("ECHO_MAX_SCAN_FILES", "20000"))
MAX_BATCH_ITEMS: int = int(os.environ.get("ECHO_MAX_BATCH_ITEMS", "20000"))
MAX_COVER_DOWNLOAD_BYTES: int = 15 * 1024 * 1024  # 15 MB
JOB_RETENTION_SECONDS: int = 60 * 60              # Jobs finalizados ficam 1h em memória

# Identificação de cliente para APIs externas (MusicBrainz / LRCLib)
USER_AGENT: str = (
    f"EchoOrganizeDAP/{APP_VERSION} (https://github.com/user/echo-organizer-dap)"
)
MUSICBRAINZ_RATE_LIMIT_SECONDS: float = 1.0
LRCLIB_RATE_LIMIT_SECONDS: float = 0.15
DURATION_MATCH_TOLERANCE_SECONDS: float = 3.0

# Nomes de arquivos de capa locais reconhecidos como fallback
LOCAL_COVER_FILENAMES: Tuple[str, ...] = (
    "cover.jpg", "cover.jpeg", "cover.png",
    "folder.jpg", "folder.jpeg", "folder.png",
    "album.jpg", "album.jpeg", "album.png",
    "front.jpg", "front.jpeg", "front.png",
)


def _parse_allowed_hosts(raw: str) -> FrozenSet[str]:
    """Converte a lista CSV de hosts permitidos em um conjunto normalizado."""
    return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())


# Hosts aceitos no cabeçalho `Host` (proteção contra DNS Rebinding).
# O servidor manipula o sistema de arquivos local: por padrão só responde a loopback.
ALLOWED_HOSTS: FrozenSet[str] = _parse_allowed_hosts(
    os.environ.get(
        "ECHO_ALLOWED_HOSTS",
        "127.0.0.1,localhost,::1,[::1],0.0.0.0,testserver",
    )
)


def get_logger(name: str) -> logging.Logger:
    """
    Configura e retorna um logger padronizado para os módulos do EchoOrganize.
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
            datefmt="%H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger
