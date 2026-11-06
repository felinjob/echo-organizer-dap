"""
Serviço de geração do relatório estrutural da biblioteca (library_index.txt).
"""

import datetime
import os
from typing import Any, Dict, List

from app.config import SUPPORTED_AUDIO_EXTENSIONS


def build_library_index(dest_dir: str) -> Dict[str, Any]:
    """
    Percorre o diretório de destino organizado e gera um arquivo `library_index.txt`
    formatado como árvore visual com estatísticas de artistas, álbuns e faixas.
    """
    library: Dict[str, Dict[str, List[str]]] = {}
    total_tracks = 0

    for artist in sorted(os.listdir(dest_dir)):
        artist_path = os.path.join(dest_dir, artist)
        if not os.path.isdir(artist_path) or artist.startswith("."):
            continue

        library[artist] = {}
        for album in sorted(os.listdir(artist_path)):
            album_path = os.path.join(artist_path, album)
            if not os.path.isdir(album_path) or album.startswith("."):
                continue

            library[artist][album] = []
            for track_file in sorted(os.listdir(album_path)):
                if track_file.lower().endswith(SUPPORTED_AUDIO_EXTENSIONS):
                    library[artist][album].append(track_file)
                    total_tracks += 1

    now = datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    total_artists = len(library)
    total_albums = sum(len(library[art]) for art in library)

    lines = [
        "==================================================",
        "        ECHOORGANIZE - BIBLIOTECA ORGANIZADA      ",
        "==================================================",
        f"Relatório gerado em: {now}",
        f"Total de Artistas:   {total_artists}",
        f"Total de Álbuns:     {total_albums}",
        f"Total de Faixas:     {total_tracks}",
        "==================================================",
        "",
        "Estrutura da Biblioteca:",
        "------------------------",
    ]

    for artist, albums in library.items():
        if not albums or all(len(tracks) == 0 for tracks in albums.values()):
            continue
        lines.append(f"📁 {artist}")
        for album, tracks in albums.items():
            if not tracks:
                continue
            lines.append(f"  └── 💿 {album}")
            for i, track in enumerate(tracks):
                is_last_track = i == len(tracks) - 1
                branch = "      └── 🎵 " if is_last_track else "      ├── 🎵 "
                lines.append(f"{branch}{track}")
        lines.append("")

    index_content = "\n".join(lines)
    index_file_path = os.path.join(dest_dir, "library_index.txt")

    with open(index_file_path, "w", encoding="utf-8") as index_file:
        index_file.write(index_content)

    return {
        "file_path": index_file_path,
        "summary": {
            "artists": total_artists,
            "albums": total_albums,
            "tracks": total_tracks,
        },
    }

