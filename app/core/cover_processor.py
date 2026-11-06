"""
Motor de inspeção binária de cabeçalhos JPEG e reencoding Hardware-Safe (Subsampling 4:2:0).

Projetado para decodificadores JPEG embarcados em SoCs de baixo consumo (Rockchip, Ingenic,
Actions), garantindo estritamente JPEG Baseline (SOF0 0xFFC0), ausência de SOF2 (0xFFC2),
subsampling 4:2:0 e remoção completa de metadados EXIF e perfis de cor ICC.
"""

import io
from typing import Optional, Tuple
from PIL import Image

import app.services.cache as cache_service
from app.config import (
    JPEG_QUALITY,
    MAX_BYPASS_COVER_BYTES,
    MAX_COVER_DIMENSIONS,
    get_logger,
)

logger = get_logger(__name__)

# Marcadores SOF não-baseline (SOF1 Extended, SOF2 Progressivo, SOF3 Lossless, etc.)
NON_BASELINE_SOF_MARKERS = frozenset(
    {0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)

# Marcadores standalone sem campo de comprimento de segmento
STANDALONE_JPEG_MARKERS = frozenset(
    {0x01, 0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8}
)


def is_hardware_safe_jpeg(
    image_bytes: bytes,
    max_size: Tuple[int, int] = MAX_COVER_DIMENSIONS,
    max_bytes: int = MAX_BYPASS_COVER_BYTES,
) -> bool:
    """
    Inspeciona os bytes do cabeçalho da imagem para decidir pelo Bypass Inteligente:
    Retorna True somente se a imagem:
    - For um binário JPEG válido com tamanho <= 150 KB
    - Possuir marcador Baseline DCT (SOF0 0xFFC0) e NÃO possuir marcador Progressivo (SOF2 0xFFC2)
    - Estiver em espaço YCbCr/RGB de 3 componentes com subsampling estrito 4:2:0 (Y=0x22, Cb=0x11, Cr=0x11)
    - Não contiver blocos EXIF/XMP (APP1 0xFFE1) nem perfis de cor ICC (APP2 0xFFE2)
    - Tiver dimensões físicas <= 500x500 pixels
    """
    if not image_bytes or not isinstance(image_bytes, (bytes, bytearray)):
        return False
    if len(image_bytes) < 4 or len(image_bytes) > max_bytes:
        return False
    if image_bytes[0:2] != b"\xff\xd8":
        return False

    idx = 2
    total_len = len(image_bytes)
    has_sof0 = False

    while idx < total_len:
        if image_bytes[idx] != 0xFF:
            return False
        while idx < total_len and image_bytes[idx] == 0xFF:
            idx += 1
        if idx >= total_len:
            break

        marker = image_bytes[idx]
        idx += 1

        # EOI (End of Image) ou SOS (Start of Scan - início dos dados de entropia)
        if marker in (0xD9, 0xDA):
            break

        if marker in STANDALONE_JPEG_MARKERS:
            continue

        if idx + 2 > total_len:
            return False

        seg_len = int.from_bytes(image_bytes[idx : idx + 2], "big")
        if seg_len < 2 or idx + seg_len > total_len:
            return False

        payload = image_bytes[idx + 2 : idx + seg_len]

        # Rejeita qualquer marcador SOF não-baseline (incluindo SOF2 0xFFC2 Progressivo)
        if marker in NON_BASELINE_SOF_MARKERS:
            return False

        # Rejeita blocos EXIF / XMP (APP1 0xFFE1)
        if marker == 0xE1:
            return False

        # Rejeita perfis de cor ICC (APP2 0xFFE2)
        if marker == 0xE2 and payload.startswith(b"ICC_PROFILE"):
            return False

        # Analisa o segmento Baseline DCT (SOF0 0xFFC0)
        if marker == 0xC0:
            if len(payload) < 15:
                return False
            precision = payload[0]
            height = int.from_bytes(payload[1:3], "big")
            width = int.from_bytes(payload[3:5], "big")
            num_components = payload[5]

            if precision != 8 or num_components != 3:
                return False
            if width <= 0 or height <= 0 or width > max_size[0] or height > max_size[1]:
                return False

            # Fatores de amostragem para 4:2:0 estrito: Y=0x22 (2x2), Cb=0x11 (1x1), Cr=0x11 (1x1)
            sampling_y = payload[7]
            sampling_cb = payload[10]
            sampling_cr = payload[13]
            if not (sampling_y == 0x22 and sampling_cb == 0x11 and sampling_cr == 0x11):
                return False

            has_sof0 = True

        idx += seg_len

    if not has_sof0:
        return False

    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            if img.format != "JPEG" or img.mode != "RGB":
                return False
            if img.width > max_size[0] or img.height > max_size[1]:
                return False
            if (
                img.info.get("progressive")
                or img.info.get("exif")
                or img.info.get("icc_profile")
            ):
                return False
    except Exception:
        return False

    return True


def resize_cover_image(
    image_bytes: bytes,
    max_size: Tuple[int, int] = MAX_COVER_DIMENSIONS,
) -> Optional[bytes]:
    """
    Processa a imagem de capa com compatibilidade estrita de hardware DAP:
    - Consulta o cache de binários por hash SHA-256 antes de reprocessar.
    - Bypass Inteligente: Se já for JPEG Baseline (SOF0, sem SOF2), subsampling 4:2:0,
      RGB/YCbCr, sem EXIF/ICC, <= 500x500 px e <= 150 KB, retorna os bytes originais intactos.
    - Reencoding Estrito: Caso contrário, converte para RGB, redimensiona via LANCZOS
      para até 500x500 px e salva como JPEG Baseline (`progressive=False`, `optimize=True`,
      `subsampling="4:2:0"`, `quality=85`) sem EXIF e sem perfil ICC.
    """
    if not image_bytes:
        return None

    raw_bytes = bytes(image_bytes)
    hash_key = f"sha256:{cache_service.compute_bytes_hash(raw_bytes)}"
    cached = cache_service.get_cached_cover_bytes(hash_key)
    if cached:
        return cached

    if is_hardware_safe_jpeg(raw_bytes, max_size=max_size, max_bytes=MAX_BYPASS_COVER_BYTES):
        cache_service.set_cached_cover_bytes(hash_key, raw_bytes)
        return raw_bytes

    try:
        cache_service.record_pillow_resize()
        with Image.open(io.BytesIO(raw_bytes)) as img:
            if img.mode in ("RGBA", "LA") or (
                img.mode == "P" and "transparency" in img.info
            ):
                rgba = img.convert("RGBA")
                background = Image.new("RGB", rgba.size, (0, 0, 0))
                background.paste(rgba, mask=rgba.split()[3])
                img = background
            elif img.mode != "RGB":
                img = img.convert("RGB")
            else:
                img = img.copy()

            img.info.pop("exif", None)
            img.info.pop("icc_profile", None)
            img.info.pop("progressive", None)

            if img.width > max_size[0] or img.height > max_size[1]:
                img.thumbnail(max_size, Image.Resampling.LANCZOS)

            out_buf = io.BytesIO()
            img.save(
                out_buf,
                format="JPEG",
                progressive=False,
                optimize=True,
                subsampling="4:2:0",
                quality=JPEG_QUALITY,
                exif=b"",
                icc_profile=None,
            )
            processed = out_buf.getvalue()
            cache_service.set_cached_cover_bytes(hash_key, processed)
            return processed
    except Exception as exc:
        logger.warning("Erro ao redimensionar imagem de capa: %s", exc)
        return None

