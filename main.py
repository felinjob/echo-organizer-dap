import os
import re
import datetime
import platform
import subprocess
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Dict, Optional, Any

class LocateFolderRequest(BaseModel):
    folder_name: str

class BrowseRequest(BaseModel):
    title: Optional[str] = "Selecione a Pasta"

import tagger_service
import musicbrainz_service

app = FastAPI(title="Music Tagger & Organizer for DAPs")

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(CURRENT_DIR, "frontend")

os.makedirs(FRONTEND_DIR, exist_ok=True)

class ScanRequest(BaseModel):
    source_dir: str
    dest_dir: Optional[str] = None

class MusicBrainzQuery(BaseModel):
    artist: Optional[str] = ""
    title: Optional[str] = ""
    filename: Optional[str] = ""

class FileTagMapping(BaseModel):
    source: str
    tags: Dict[str, Any]

class ProcessRequest(BaseModel):
    dest_dir: str
    mappings: List[FileTagMapping]

class IndexRequest(BaseModel):
    dest_dir: str

def sanitize_path_component(name: Any) -> str:
    """
    Removes invalid characters for Windows file systems.
    """
    if name is None:
        return "Unknown"
    name_str = str(name).strip()
    if not name_str:
        return "Unknown"
    sanitized = re.sub(r'[\\/*?:"<>|]', "", name_str)
    sanitized = sanitized.strip().strip('.')
    return sanitized if sanitized else "Unknown"

@app.post("/api/browse-directory")
def browse_directory(request: BrowseRequest):
    """
    Opens a native folder dialog to select a directory on the user's OS.
    Supports Windows (PowerShell/Tkinter) and Linux (Zenity/Tkinter).
    """
    title = request.title or "Selecione a Pasta"
    system_os = platform.system()
    
    if system_os == "Windows":
        cmd = f'''
        $shell = New-Object -ComObject Shell.Application
        $folder = $shell.BrowseForFolder(0, "{title}", 0, 17)
        if ($folder) {{
            Write-Output $folder.Self.Path
        }}
        '''
        try:
            result = subprocess.run(["powershell", "-Command", cmd], capture_output=True, text=True, check=True)
            path = result.stdout.strip()
            if path and os.path.exists(path):
                return {"success": True, "path": path}
        except Exception as e:
            print(f"Error calling powershell browse: {e}")
            
    elif system_os == "Linux":
        try:
            result = subprocess.run(["zenity", "--file-selection", "--directory", f"--title={title}"], capture_output=True, text=True)
            path = result.stdout.strip()
            if path and os.path.exists(path):
                return {"success": True, "path": path}
        except Exception as e:
            print(f"Zenity not available or failed: {e}")
            
    # Fallback to Tkinter
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askdirectory(title=title)
        root.destroy()
        if path and os.path.exists(path):
            return {"success": True, "path": path}
    except Exception as tk_err:
        print(f"Tkinter fallback error: {tk_err}")
        
    return {"success": False, "message": "Nenhum diretório selecionado."}

@app.post("/api/locate-folder")
def locate_folder(request: LocateFolderRequest):
    """
    Searches for a directory named folder_name in common system folders
    to resolve drag-and-dropped folder paths.
    """
    folder_name = request.folder_name.strip()
    if not folder_name:
        return {"success": False, "paths": []}
        
    home = os.path.expanduser("~")
    common_roots = [
        os.path.join(home, "Music"),
        os.path.join(home, "Downloads"),
        os.path.join(home, "Desktop"),
        os.path.join(home, "Documents"),
        CURRENT_DIR
    ]
    common_roots = [r for r in common_roots if os.path.exists(r)]
    matches = []
    
    # 1. Search directly inside the common roots (case-insensitive)
    for root in common_roots:
        try:
            for entry in os.listdir(root):
                entry_path = os.path.join(root, entry)
                if os.path.isdir(entry_path) and entry.lower() == folder_name.lower():
                    if entry_path not in matches:
                        matches.append(entry_path)
        except Exception:
            continue
            
    # 2. If no direct match, search one level down
    if not matches:
        for root in common_roots:
            try:
                for entry in os.listdir(root):
                    entry_path = os.path.join(root, entry)
                    if os.path.isdir(entry_path):
                        for sub_entry in os.listdir(entry_path):
                            sub_path = os.path.join(entry_path, sub_entry)
                            if os.path.isdir(sub_path) and sub_entry.lower() == folder_name.lower():
                                if sub_path not in matches:
                                    matches.append(sub_path)
            except Exception:
                continue
                
    # 3. Check if the matches contain any audio files
    valid_matches = []
    supported_exts = (".mp3", ".flac", ".m4a", ".mp4")
    
    for path in matches:
        has_audio = False
        try:
            for r, dirs, files in os.walk(path):
                for f in files:
                    if f.lower().endswith(supported_exts):
                        has_audio = True
                        break
                if has_audio:
                    break
        except Exception:
            pass
        if has_audio:
            valid_matches.append(path)
            
    return {
        "success": len(valid_matches) > 0,
        "paths": valid_matches
    }

@app.post("/api/scan")
def scan_directory(request: ScanRequest):
    """
    Recursively scans the directory for MP3, FLAC, and M4A files and returns
    their original metadata and heuristic local tags.
    """
    source_dir = os.path.abspath(request.source_dir)
    if not os.path.exists(source_dir):
        raise HTTPException(status_code=400, detail="Diretório de origem não existe.")
        
    if not os.path.isdir(source_dir):
        raise HTTPException(status_code=400, detail="O caminho especificado não é um diretório.")
        
    audio_files = []
    supported_exts = (".mp3", ".flac", ".m4a", ".mp4")
    for root, dirs, files in os.walk(source_dir):
        for file in files:
            if file.lower().endswith(supported_exts):
                full_path = os.path.join(root, file)
                audio_files.append(full_path)
                
    results = []
    for path in audio_files:
        try:
            original = tagger_service.extract_metadata(path)
            heur_track, heur_artist, heur_title = musicbrainz_service.clean_filename(original["filename"])
            
            proposed = {
                "title": original["title"] or heur_title or "",
                "artist": original["artist"] or heur_artist or "Artista Desconhecido",
                "album_artist": original["album_artist"] or original["artist"] or heur_artist or "Artista Desconhecido",
                "album": original["album"] or "Álbum Desconhecido",
                "track": original["track"] or heur_track or "01",
                "disc": original["disc"] or "1",
                "year": original["year"] or "",
                "genre": original["genre"] or "",
                "has_cover": original["has_cover"],
                "lyrics": original.get("lyrics")
            }
            
            if proposed["track"]:
                match = re.match(r"^(\d+)", str(proposed["track"]))
                if match:
                    proposed["track"] = f"{int(match.group(1)):02d}"
                    
            if proposed["disc"]:
                match = re.match(r"^(\d+)", str(proposed["disc"]))
                if match:
                    proposed["disc"] = str(int(match.group(1)))
            
            # Check if already organized in target directory
            already_organized = False
            if request.dest_dir:
                album_artist = proposed.get("album_artist") or proposed.get("artist") or "Artista Desconhecido"
                if album_artist.lower() in ["various artists", "various", "varios artistas", "varios", "vários artistas"]:
                    album_artist = "Various Artists"
                    
                artist_folder = sanitize_path_component(album_artist)
                album_folder = sanitize_path_component(proposed.get("album", "Álbum Desconhecido"))
                
                track_num = proposed.get("track", "01")
                try:
                    track_num = f"{int(track_num):02d}"
                except ValueError:
                    track_num = sanitize_path_component(track_num)
                    
                disc_num = proposed.get("disc", "1")
                try:
                    disc_int = int(disc_num)
                    if disc_int > 1:
                        track_prefix = f"{disc_int}-{track_num}"
                    else:
                        track_prefix = track_num
                except ValueError:
                    track_prefix = track_num
                    
                title_clean = sanitize_path_component(proposed.get("title", "Faixa"))
                track_artist = proposed.get("artist", "")
                
                # Use source file extension
                ext = os.path.splitext(path)[1].lower()
                if ext not in supported_exts:
                    ext = ".mp3"
                    
                # Check if file has features / different artist
                is_diff_artist = False
                if track_artist and album_artist:
                    t_art = re.sub(r'\s+', ' ', track_artist).strip().lower()
                    a_art = re.sub(r'\s+', ' ', album_artist).strip().lower()
                    if t_art != a_art and a_art != "artista desconhecido":
                        is_diff_artist = True
                        
                if is_diff_artist:
                    file_name = f"{track_prefix} - {sanitize_path_component(track_artist)} - {title_clean}{ext}"
                else:
                    file_name = f"{track_prefix} - {title_clean}{ext}"
                    
                dest_path = os.path.join(request.dest_dir, artist_folder, album_folder, file_name)
                
                if os.path.exists(dest_path):
                    already_organized = True
            
            results.append({
                "source": path,
                "original": original,
                "proposed": proposed,
                "already_organized": already_organized
            })
        except Exception as e:
            print(f"Error scanning {path}: {e}")
            
    return {"files": results}

@app.post("/api/musicbrainz")
def fetch_musicbrainz_tags(query: MusicBrainzQuery):
    """
    Fetches suggested tags and lyrics from MusicBrainz & LRCLib APIs for a single file.
    """
    search_artist = query.artist
    search_title = query.title
    
    if not search_artist or not search_title:
        if query.filename:
            _, heur_artist, heur_title = musicbrainz_service.clean_filename(query.filename)
            search_artist = search_artist or heur_artist
            search_title = search_title or heur_title
            
    if not search_title:
        raise HTTPException(status_code=400, detail="Título ou nome do arquivo é obrigatório.")
        
    mb_tags = musicbrainz_service.search_musicbrainz(search_artist, search_title)
    
    # Fetch lyrics from LRCLib
    lyrics = None
    if search_artist and search_title:
        album_name = mb_tags.get("album") if mb_tags else None
        lyrics = musicbrainz_service.fetch_lyrics_from_lrclib(search_artist, search_title, album_name)
    
    if mb_tags:
        if mb_tags.get("track"):
            match = re.match(r"^(\d+)", str(mb_tags["track"]))
            if match:
                mb_tags["track"] = f"{int(match.group(1)):02d}"
        if mb_tags.get("disc"):
            match = re.match(r"^(\d+)", str(mb_tags["disc"]))
            if match:
                mb_tags["disc"] = str(int(match.group(1)))
                
        response_data = {"success": True, "tags": mb_tags}
        if lyrics:
            response_data["lyrics"] = lyrics
        return response_data
        
    if lyrics:
        # Fallback to local heuristic tags if we only found lyrics
        heur_tags = {
            "title": search_title,
            "artist": search_artist,
            "source": "Local Heuristics"
        }
        return {"success": True, "tags": heur_tags, "lyrics": lyrics}
        
    return {"success": False, "message": "Nenhum resultado encontrado no MusicBrainz ou LRCLib."}

@app.post("/api/process")
def process_files(request: ProcessRequest):
    """
    Cleans, tags, and organizes files to the destination directory.
    Uses sequential file creation order to preserve FAT32 sorting.
    """
    dest_dir = os.path.abspath(request.dest_dir)
    try:
        os.makedirs(dest_dir, exist_ok=True)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Não foi possível criar o diretório de destino: {e}")
        
    files_mapping = []
    supported_exts = (".mp3", ".flac", ".m4a", ".mp4")
    
    for item in request.mappings:
        src = item.source
        tags = item.tags
        
        # Album Artist main folder grouping
        album_artist = tags.get("album_artist") or tags.get("artist") or "Artista Desconhecido"
        if album_artist.lower() in ["various artists", "various", "varios artistas", "varios", "vários artistas"]:
            album_artist = "Various Artists"
            
        artist_folder = sanitize_path_component(album_artist)
        album_folder = sanitize_path_component(tags.get("album", "Álbum Desconhecido"))
        
        # Track Number
        track_num = tags.get("track", "01")
        try:
            track_num = f"{int(track_num):02d}"
        except ValueError:
            track_num = sanitize_path_component(track_num)
            
        # Disc Number track prefixing for sorting
        disc_num = tags.get("disc", "1")
        try:
            disc_int = int(disc_num)
            if disc_int > 1:
                track_prefix = f"{disc_int}-{track_num}"
            else:
                track_prefix = track_num
        except ValueError:
            track_prefix = track_num
            
        title_clean = sanitize_path_component(tags.get("title", "Faixa"))
        track_artist = tags.get("artist", "")
        
        # Determine file extension from source
        ext = os.path.splitext(src)[1].lower()
        if ext not in supported_exts:
            ext = ".mp3"
            
        # Naming format: include performer if it differs from album artist
        is_diff_artist = False
        if track_artist and album_artist:
            t_art = re.sub(r'\s+', ' ', track_artist).strip().lower()
            a_art = re.sub(r'\s+', ' ', album_artist).strip().lower()
            if t_art != a_art and a_art != "artista desconhecido":
                is_diff_artist = True
                
        if is_diff_artist:
            file_name = f"{track_prefix} - {sanitize_path_component(track_artist)} - {title_clean}{ext}"
        else:
            file_name = f"{track_prefix} - {title_clean}{ext}"
            
        dest_path = os.path.join(dest_dir, artist_folder, album_folder, file_name)
        
        files_mapping.append({
            "source": src,
            "dest": dest_path,
            "tags": tags
        })
        
    results = tagger_service.copy_sequentially(files_mapping)
    
    success_count = sum(1 for r in results if r["status"] == "success")
    error_count = len(results) - success_count
    
    return {
        "success": True,
        "results": results,
        "summary": {
            "total": len(results),
            "success": success_count,
            "error": error_count
        }
    }

@app.post("/api/generate-index")
def generate_library_index(request: IndexRequest):
    """
    Generates a library_index.txt file in the destination root folder,
    listing all organized artists, albums, and tracks in a nice tree format.
    """
    dest_dir = os.path.abspath(request.dest_dir)
    if not os.path.exists(dest_dir) or not os.path.isdir(dest_dir):
        raise HTTPException(status_code=400, detail="Diretório de destino inválido ou inexistente.")
        
    try:
        library = {}
        total_tracks = 0
        supported_exts = (".mp3", ".flac", ".m4a", ".mp4")
        
        for artist in sorted(os.listdir(dest_dir)):
            artist_path = os.path.join(dest_dir, artist)
            if not os.path.isdir(artist_path) or artist.startswith('.'):
                continue
                
            library[artist] = {}
            for album in sorted(os.listdir(artist_path)):
                album_path = os.path.join(artist_path, album)
                if not os.path.isdir(album_path) or album.startswith('.'):
                    continue
                    
                library[artist][album] = []
                for track_file in sorted(os.listdir(album_path)):
                    if track_file.lower().endswith(supported_exts):
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
            "------------------------"
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
                    is_last_track = (i == len(tracks) - 1)
                    branch = "      └── 🎵 " if is_last_track else "      ├── 🎵 "
                    lines.append(f"{branch}{track}")
            lines.append("")
            
        index_content = "\n".join(lines)
        index_file_path = os.path.join(dest_dir, "library_index.txt")
        
        with open(index_file_path, "w", encoding="utf-8") as f:
            f.write(index_content)
            
        return {
            "success": True, 
            "file_path": index_file_path,
            "summary": {
                "artists": total_artists,
                "albums": total_albums,
                "tracks": total_tracks
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao gerar índice: {e}")

@app.get("/")
def serve_index():
    index_path = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Backend rodando. Crie os arquivos HTML na pasta frontend."}

app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
