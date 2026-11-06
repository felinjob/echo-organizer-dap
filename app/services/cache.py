"""
Camada de persistência e cache local SQLite (WAL) e RAM do EchoOrganize.

Armazena resultados de consultas de Releases e Recordings do MusicBrainz,
URLs do Cover Art Archive, binários JPEG Baseline 4:2:0 já redimensionados
e letras sincronizadas do LRCLib, evitando chamadas HTTP e reamostragens
matriciais redundantes. Inclui auto-recuperação (self-healing) automática de
schema caso o arquivo `.db` seja removido ou recriado em tempo de execução.
"""

import hashlib
import json
import os
import sqlite3
import threading
import time
from typing import Any, Dict, Optional, Tuple

from app.config import DEFAULT_DB_PATH, get_logger

__all__ = [
    "DEFAULT_DB_PATH",
    "init_db",
    "make_key",
    "compute_bytes_hash",
    "get_cached_mb_release",
    "set_cached_mb_release",
    "get_cached_mb_recording",
    "set_cached_mb_recording",
    "get_cached_cover_url",
    "set_cached_cover_url",
    "get_cached_lyrics",
    "set_cached_lyrics",
    "get_cached_cover_bytes",
    "set_cached_cover_bytes",
    "clear_memory_cover_cache",
    "clear_all_caches",
    "record_http_download",
    "record_pillow_resize",
    "get_cover_metrics",
]

logger = get_logger(__name__)

_db_lock = threading.RLock()
_memory_cover_cache: Dict[str, bytes] = {}
_http_download_count: int = 0
_pillow_resize_count: int = 0

_SCHEMA_SQL: str = """
CREATE TABLE IF NOT EXISTS mb_release_cache (
    cache_key TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS mb_recording_cache (
    cache_key TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS cover_art_url_cache (
    release_id TEXT PRIMARY KEY,
    cover_url TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS cover_binary_cache (
    cache_key TEXT PRIMARY KEY,
    jpeg_bytes BLOB NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS lrclib_cache (
    cache_key TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Garante que todas as tabelas de cache existam na conexão SQLite ativa."""
    conn.executescript(_SCHEMA_SQL)
    conn.commit()


def _get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """
    Cria uma conexão SQLite configurada com journal_mode=WAL e synchronous=NORMAL.
    Se o arquivo `.db` não existir ou tiver acabado de ser recriado vazio, inicializa
    o schema automaticamente (self-healing).
    """
    target_path = db_path or DEFAULT_DB_PATH
    needs_init = not os.path.exists(target_path) or os.path.getsize(target_path) < 4096
    conn = sqlite3.connect(target_path, timeout=15.0, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    if needs_init:
        _ensure_schema(conn)
    return conn


def init_db(db_path: Optional[str] = None) -> None:
    """
    Inicializa as tabelas de cache no banco SQLite caso ainda não existam.
    """
    with _db_lock:
        conn = _get_connection(db_path)
        try:
            _ensure_schema(conn)
        finally:
            conn.close()


init_db()


def _execute_query(
    db_path: Optional[str],
    sql: str,
    params: Tuple[Any, ...],
    *,
    fetch_one: bool = False,
    commit: bool = False,
    caller_name: str = "sqlite_op",
) -> Optional[Tuple[Any, ...]]:
    """
    Executa uma operação SQLite protegida por lock com auto-recuperação transparente
    caso alguma tabela tenha sido apagada ou o arquivo `.db` tenha sido recriado.
    """
    with _db_lock:
        conn = _get_connection(db_path)
        try:
            for attempt in range(2):
                try:
                    cur = conn.execute(sql, params)
                    row = cur.fetchone() if fetch_one else None
                    if commit:
                        conn.commit()
                    return row
                except sqlite3.OperationalError as op_err:
                    if attempt == 0 and "no such table" in str(op_err).lower():
                        _ensure_schema(conn)
                        continue
                    raise
        except Exception as exc:
            logger.warning("Erro SQLite em %s: %s", caller_name, exc)
        finally:
            conn.close()
    return None


def make_key(*parts: Any) -> str:
    """
    Gera uma chave determinística SHA-256 normalizando e concatenando as partes informadas.
    """
    norm_parts = [str(part or "").strip().lower() for part in parts]
    raw = "|".join(norm_parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_bytes_hash(data: bytes) -> str:
    """Calcula o digest SHA-256 hexadecimal de um payload binário."""
    return hashlib.sha256(data).hexdigest()


def get_cached_mb_release(
    cache_key: str, db_path: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Recupera um Release completo do MusicBrainz a partir do cache SQLite."""
    row = _execute_query(
        db_path,
        "SELECT payload_json FROM mb_release_cache WHERE cache_key = ?",
        (cache_key,),
        fetch_one=True,
        caller_name="get_cached_mb_release",
    )
    if row and row[0]:
        try:
            return json.loads(row[0])
        except Exception:
            return None
    return None


def set_cached_mb_release(
    cache_key: str, payload: Dict[str, Any], db_path: Optional[str] = None
) -> None:
    """Salva um Release completo do MusicBrainz no cache SQLite."""
    _execute_query(
        db_path,
        """
        INSERT OR REPLACE INTO mb_release_cache (cache_key, payload_json, updated_at)
        VALUES (?, ?, ?)
        """,
        (cache_key, json.dumps(payload, ensure_ascii=False), time.time()),
        commit=True,
        caller_name="set_cached_mb_release",
    )


def get_cached_mb_recording(
    cache_key: str, db_path: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Recupera o metadado de uma gravação individual do MusicBrainz no SQLite."""
    row = _execute_query(
        db_path,
        "SELECT payload_json FROM mb_recording_cache WHERE cache_key = ?",
        (cache_key,),
        fetch_one=True,
        caller_name="get_cached_mb_recording",
    )
    if row and row[0]:
        try:
            return json.loads(row[0])
        except Exception:
            return None
    return None


def set_cached_mb_recording(
    cache_key: str, payload: Dict[str, Any], db_path: Optional[str] = None
) -> None:
    """Persiste o metadado de uma gravação individual do MusicBrainz no SQLite."""
    _execute_query(
        db_path,
        """
        INSERT OR REPLACE INTO mb_recording_cache (cache_key, payload_json, updated_at)
        VALUES (?, ?, ?)
        """,
        (cache_key, json.dumps(payload, ensure_ascii=False), time.time()),
        commit=True,
        caller_name="set_cached_mb_recording",
    )


def get_cached_cover_url(
    release_id: str, db_path: Optional[str] = None
) -> Optional[str]:
    """Recupera a URL da capa associada a um Release MBID no SQLite."""
    if not release_id:
        return None
    row = _execute_query(
        db_path,
        "SELECT cover_url FROM cover_art_url_cache WHERE release_id = ?",
        (release_id.strip().lower(),),
        fetch_one=True,
        caller_name="get_cached_cover_url",
    )
    if row is not None:
        return row[0]
    return None


def set_cached_cover_url(
    release_id: str, cover_url: str, db_path: Optional[str] = None
) -> None:
    """Salva a URL da capa associada a um Release MBID no SQLite."""
    if not release_id:
        return
    _execute_query(
        db_path,
        """
        INSERT OR REPLACE INTO cover_art_url_cache (release_id, cover_url, updated_at)
        VALUES (?, ?, ?)
        """,
        (release_id.strip().lower(), cover_url or "", time.time()),
        commit=True,
        caller_name="set_cached_cover_url",
    )


def get_cached_lyrics(
    cache_key: str, db_path: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Recupera letras sincronizadas ou planas do cache SQLite."""
    row = _execute_query(
        db_path,
        "SELECT payload_json FROM lrclib_cache WHERE cache_key = ?",
        (cache_key,),
        fetch_one=True,
        caller_name="get_cached_lyrics",
    )
    if row and row[0]:
        try:
            return json.loads(row[0])
        except Exception:
            return None
    return None


def set_cached_lyrics(
    cache_key: str, payload: Dict[str, Any], db_path: Optional[str] = None
) -> None:
    """Armazena o resultado de busca de letras do LRCLib no SQLite."""
    _execute_query(
        db_path,
        """
        INSERT OR REPLACE INTO lrclib_cache (cache_key, payload_json, updated_at)
        VALUES (?, ?, ?)
        """,
        (cache_key, json.dumps(payload, ensure_ascii=False), time.time()),
        commit=True,
        caller_name="set_cached_lyrics",
    )


def get_cached_cover_bytes(
    cache_key: str, db_path: Optional[str] = None
) -> Optional[bytes]:
    """
    Recupera os bytes da capa JPEG Baseline 4:2:0 já processada a partir da
    memória RAM (nível L1) ou do banco SQLite (nível L2).
    """
    if not cache_key:
        return None
    with _db_lock:
        if cache_key in _memory_cover_cache:
            return _memory_cover_cache[cache_key]
        row = _execute_query(
            db_path,
            "SELECT jpeg_bytes FROM cover_binary_cache WHERE cache_key = ?",
            (cache_key,),
            fetch_one=True,
            caller_name="get_cached_cover_bytes",
        )
        if row and row[0]:
            jpeg_bytes = bytes(row[0])
            _memory_cover_cache[cache_key] = jpeg_bytes
            return jpeg_bytes
    return None


def set_cached_cover_bytes(
    cache_key: str, jpeg_bytes: bytes, db_path: Optional[str] = None
) -> None:
    """
    Armazena os bytes da capa JPEG Baseline 4:2:0 simultaneamente no cache
    em memória RAM (L1) e na tabela BLOB do SQLite (L2).
    """
    if not cache_key or not jpeg_bytes:
        return
    with _db_lock:
        _memory_cover_cache[cache_key] = bytes(jpeg_bytes)
        _execute_query(
            db_path,
            """
            INSERT OR REPLACE INTO cover_binary_cache (cache_key, jpeg_bytes, updated_at)
            VALUES (?, ?, ?)
            """,
            (cache_key, sqlite3.Binary(jpeg_bytes), time.time()),
            commit=True,
            caller_name="set_cached_cover_bytes",
        )


def clear_memory_cover_cache() -> None:
    """Limpa o cache em memória de capas e reinicia os contadores de telemetria."""
    global _http_download_count, _pillow_resize_count
    with _db_lock:
        _memory_cover_cache.clear()
        _http_download_count = 0
        _pillow_resize_count = 0


def clear_all_caches(db_path: Optional[str] = None) -> None:
    """Remove todos os registros de cache em memória RAM e nas tabelas SQLite."""
    clear_memory_cover_cache()
    with _db_lock:
        conn = _get_connection(db_path)
        try:
            _ensure_schema(conn)
            conn.executescript(
                """
                DELETE FROM mb_release_cache;
                DELETE FROM mb_recording_cache;
                DELETE FROM cover_art_url_cache;
                DELETE FROM cover_binary_cache;
                DELETE FROM lrclib_cache;
                """
            )
            conn.commit()
        finally:
            conn.close()


def record_http_download() -> None:
    """Incrementa o contador de downloads HTTP de capas realizados."""
    global _http_download_count
    with _db_lock:
        _http_download_count += 1


def record_pillow_resize() -> None:
    """Incrementa o contador de redimensionamentos matriciais via Pillow."""
    global _pillow_resize_count
    with _db_lock:
        _pillow_resize_count += 1


def get_cover_metrics() -> Dict[str, int]:
    """Retorna as métricas de deduplicação de capas (downloads HTTP, resizes e entradas RAM)."""
    with _db_lock:
        return {
            "http_downloads": _http_download_count,
            "pillow_resizes": _pillow_resize_count,
            "memory_entries": len(_memory_cover_cache),
        }
