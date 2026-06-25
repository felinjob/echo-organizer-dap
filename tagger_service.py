import os
import io
import shutil
import time
import re
import requests
from PIL import Image
from mutagen.id3 import ID3, TIT2, TPE1, TPE2, TALB, TRCK, TPOS, TYER, TCON, APIC, USLT, error as ID3Error
from mutagen.mp3 import MP3
from mutagen.flac import FLAC, Picture, error as FLACError
from mutagen.mp4 import MP4, MP4Cover, error as MP4Error

def strip_lrc_timestamps(lrc_text):
    """
    Strips LRC timestamps and metadata headers to create a clean, plain text lyrics transcription.
    """
    if not lrc_text:
        return ""
    lines = []
    for line in lrc_text.splitlines():
        # Remove metadata headers like [ar: ...], [ti: ...], [al: ...]
        if re.match(r"^\[[a-zA-Z]+:.*\]$", line.strip()):
            continue
        # Remove timestamps like [00:12.34], [00:12], [01:23.456]
        cleaned = re.sub(r"\[\d{2,}:\d{2}(?:\.\d{2,3})?\]", "", line).strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)

def extract_metadata(file_path):
    """
    Extracts essential metadata (including Album Artist, Disc Number, and Lyrics) from MP3, FLAC, and M4A files.
    """
    filename = os.path.basename(file_path)
    ext = os.path.splitext(filename)[1].lower()
    
    metadata = {
        "title": "",
        "artist": "",
        "album_artist": "",
        "album": "",
        "track": "",
        "disc": "1",
        "year": "",
        "genre": "",
        "has_cover": False,
        "filename": filename,
        "file_path": file_path,
        "extension": ext,
        "lyrics": None
    }
    
    embedded_plain = ""
    
    try:
        if ext == ".mp3":
            audio = ID3(file_path)
            if "TIT2" in audio:
                metadata["title"] = str(audio["TIT2"].text[0])
            if "TPE1" in audio:
                metadata["artist"] = str(audio["TPE1"].text[0])
            if "TPE2" in audio:
                metadata["album_artist"] = str(audio["TPE2"].text[0])
            if "TALB" in audio:
                metadata["album"] = str(audio["TALB"].text[0])
            if "TRCK" in audio:
                track_str = str(audio["TRCK"].text[0])
                if "/" in track_str:
                    track_str = track_str.split("/")[0]
                metadata["track"] = track_str
            if "TPOS" in audio:
                disc_str = str(audio["TPOS"].text[0])
                if "/" in disc_str:
                    disc_str = disc_str.split("/")[0]
                metadata["disc"] = disc_str
            if "TYER" in audio:
                metadata["year"] = str(audio["TYER"].text[0])
            elif "TDRC" in audio:
                metadata["year"] = str(audio["TDRC"].text[0])[:4]
            if "TCON" in audio:
                metadata["genre"] = str(audio["TCON"].text[0])
            
            for tag in audio.values():
                if isinstance(tag, APIC):
                    metadata["has_cover"] = True
                elif isinstance(tag, USLT):
                    embedded_plain = str(tag.text)
                    
        elif ext == ".flac":
            audio = FLAC(file_path)
            metadata["title"] = audio.get("title", [""])[0]
            metadata["artist"] = audio.get("artist", [""])[0]
            metadata["album_artist"] = audio.get("albumartist", audio.get("album_artist", [""]))[0]
            metadata["album"] = audio.get("album", [""])[0]
            track_list = audio.get("tracknumber", [""])
            if track_list:
                t = track_list[0]
                if "/" in t:
                    t = t.split("/")[0]
                metadata["track"] = t
            disc_list = audio.get("discnumber", [""])
            if disc_list:
                d = disc_list[0]
                if "/" in d:
                    d = d.split("/")[0]
                metadata["disc"] = d
            date_list = audio.get("date", [""])
            if date_list:
                metadata["year"] = date_list[0][:4]
            metadata["genre"] = audio.get("genre", [""])[0]
            metadata["has_cover"] = len(audio.pictures) > 0
            if "lyrics" in audio:
                embedded_plain = str(audio["lyrics"][0])
            
        elif ext in [".m4a", ".mp4"]:
            audio = MP4(file_path)
            if "\xa9nam" in audio:
                metadata["title"] = str(audio["\xa9nam"][0])
            if "\xa9ART" in audio:
                metadata["artist"] = str(audio["\xa9ART"][0])
            if "aART" in audio:
                metadata["album_artist"] = str(audio["aART"][0])
            if "\xa9alb" in audio:
                metadata["album"] = str(audio["\xa9alb"][0])
            if "trkn" in audio:
                metadata["track"] = str(audio["trkn"][0][0])
            if "disk" in audio:
                metadata["disc"] = str(audio["disk"][0][0])
            if "\xa9day" in audio:
                metadata["year"] = str(audio["\xa9day"][0])[:4]
            if "\xa9gen" in audio:
                metadata["genre"] = str(audio["\xa9gen"][0])
            metadata["has_cover"] = "covr" in audio
            if "\xa9lyr" in audio:
                embedded_plain = str(audio["\xa9lyr"][0])
                
    except Exception as e:
        print(f"Error reading tags from {file_path}: {e}")
        
    # Check sidecar .lrc file in the same directory as source
    external_content = ""
    lrc_path = os.path.splitext(file_path)[0] + ".lrc"
    if os.path.exists(lrc_path):
        try:
            with open(lrc_path, "r", encoding="utf-8", errors="ignore") as f:
                external_content = f.read().strip()
        except Exception as e:
            print(f"Error reading sidecar .lrc for {file_path}: {e}")
            
    # Parse and store lyrics
    lyrics_data = None
    
    def is_synced(content):
        return bool(re.search(r"\[\d{2,}:\d{2}", content))
        
    if external_content:
        lyrics_data = {"synced": None, "plain": None}
        if is_synced(external_content):
            lyrics_data["synced"] = external_content
        else:
            lyrics_data["plain"] = external_content
            
    if embedded_plain:
        if not lyrics_data:
            lyrics_data = {"synced": None, "plain": None}
        if is_synced(embedded_plain):
            if not lyrics_data["synced"]:
                lyrics_data["synced"] = embedded_plain
        else:
            if not lyrics_data["plain"]:
                lyrics_data["plain"] = embedded_plain
                
    metadata["lyrics"] = lyrics_data
    return metadata

def resize_cover_image(image_bytes, max_size=(500, 500)):
    """
    Resizes cover art to max 500x500 pixels and returns JPEG bytes.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
        if img.mode != 'RGB':
            img = img.convert('RGB')
        img.thumbnail(max_size, Image.Resampling.LANCZOS)
        out_buf = io.BytesIO()
        img.save(out_buf, format='JPEG', quality=85)
        return out_buf.getvalue()
    except Exception as e:
        print(f"Error resizing cover image: {e}")
        return None

def clean_and_tag_file(source_path, dest_path, clean_tags, resize_cover=True):
    """
    Reads source file, writes clean tags (version 2.3 for MP3), processes cover art,
    embeds lyrics, creates sidecar .lrc files, and saves to dest_path.
    """
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    shutil.copy2(source_path, dest_path)
    
    ext = os.path.splitext(dest_path)[1].lower()
    
    try:
        cover_data = None
        cover_mime = None
        
        # 1. Read existing cover first
        if ext == ".mp3":
            try:
                audio = ID3(dest_path)
                for tag in audio.values():
                    if isinstance(tag, APIC):
                        cover_data = tag.data
                        cover_mime = tag.mime
                        break
            except ID3Error:
                audio = ID3()
                audio.save(dest_path)
        elif ext == ".flac":
            audio = FLAC(dest_path)
            if audio.pictures:
                cover_data = audio.pictures[0].data
                cover_mime = audio.pictures[0].mime
        elif ext in [".m4a", ".mp4"]:
            audio = MP4(dest_path)
            if "covr" in audio:
                cover_data = audio["covr"][0]
                cover_mime = "image/jpeg"
                
        # 2. Try downloading online cover or local fallback if missing
        if not cover_data:
            if clean_tags.get("cover_url"):
                try:
                    print(f"Downloading cover art from {clean_tags['cover_url']}...")
                    img_resp = requests.get(clean_tags["cover_url"], timeout=10)
                    if img_resp.status_code == 200:
                        cover_data = img_resp.content
                        cover_mime = "image/jpeg"
                except Exception as img_err:
                    print(f"Failed to download online cover art: {img_err}")
            
            if not cover_data:
                try:
                    source_dir = os.path.dirname(source_path)
                    possible_names = [
                        "cover.jpg", "cover.jpeg", "cover.png",
                        "folder.jpg", "folder.jpeg", "folder.png",
                        "album.jpg", "album.jpeg", "album.png",
                        "front.jpg", "front.jpeg", "front.png"
                    ]
                    for file_in_dir in os.listdir(source_dir):
                        if file_in_dir.lower() in possible_names:
                            local_cover_path = os.path.join(source_dir, file_in_dir)
                            if os.path.isfile(local_cover_path):
                                print(f"Found local cover art fallback: {file_in_dir}")
                                with open(local_cover_path, "rb") as f:
                                    cover_data = f.read()
                                    if file_in_dir.lower().endswith(".png"):
                                        cover_mime = "image/png"
                                    else:
                                        cover_mime = "image/jpeg"
                                break
                except Exception as local_err:
                    print(f"Failed to find local cover art: {local_err}")
                    
        # Extract or generate plain lyrics for embedding
        plain_lyrics = ""
        if clean_tags.get("lyrics"):
            plain_lyrics = clean_tags["lyrics"].get("plain") or ""
            if not plain_lyrics and clean_tags["lyrics"].get("synced"):
                plain_lyrics = strip_lrc_timestamps(clean_tags["lyrics"]["synced"])

        # 3. Write metadata based on format
        if ext == ".mp3":
            audio = ID3(dest_path)
            audio.clear()
            if clean_tags.get("title"):
                audio.add(TIT2(encoding=1, text=[str(clean_tags["title"])]))
            if clean_tags.get("artist"):
                audio.add(TPE1(encoding=1, text=[str(clean_tags["artist"])]))
            if clean_tags.get("album_artist"):
                audio.add(TPE2(encoding=1, text=[str(clean_tags["album_artist"])]))
            if clean_tags.get("album"):
                audio.add(TALB(encoding=1, text=[str(clean_tags["album"])]))
            if clean_tags.get("track"):
                try:
                    track_val = int(clean_tags["track"])
                    audio.add(TRCK(encoding=1, text=[f"{track_val:02d}"]))
                except (ValueError, TypeError):
                    audio.add(TRCK(encoding=1, text=[str(clean_tags["track"])]))
            if clean_tags.get("disc"):
                try:
                    disc_val = int(clean_tags["disc"])
                    audio.add(TPOS(encoding=1, text=[str(disc_val)]))
                except (ValueError, TypeError):
                    audio.add(TPOS(encoding=1, text=[str(clean_tags["disc"])]))
            if clean_tags.get("year"):
                audio.add(TYER(encoding=1, text=[str(clean_tags["year"])]))
            if clean_tags.get("genre"):
                audio.add(TCON(encoding=1, text=[str(clean_tags["genre"])]))
            
            # Embed Unsynced Lyrics
            if plain_lyrics:
                audio.add(USLT(encoding=1, lang="eng", desc="Lyrics", text=str(plain_lyrics)))
                
            if cover_data:
                if resize_cover:
                    resized_data = resize_cover_image(cover_data)
                    if resized_data:
                        cover_data = resized_data
                        cover_mime = "image/jpeg"
                audio.add(APIC(
                    encoding=3,
                    mime=cover_mime or 'image/jpeg',
                    type=3,
                    desc='Cover',
                    data=cover_data
                ))
            audio.save(dest_path, v2_version=3)
            
        elif ext == ".flac":
            audio = FLAC(dest_path)
            audio.clear()
            if clean_tags.get("title"):
                audio["title"] = [str(clean_tags["title"])]
            if clean_tags.get("artist"):
                audio["artist"] = [str(clean_tags["artist"])]
            if clean_tags.get("album_artist"):
                audio["albumartist"] = [str(clean_tags["album_artist"])]
            if clean_tags.get("album"):
                audio["album"] = [str(clean_tags["album"])]
            if clean_tags.get("track"):
                try:
                    audio["tracknumber"] = [f"{int(clean_tags['track']):02d}"]
                except (ValueError, TypeError):
                    audio["tracknumber"] = [str(clean_tags["track"])]
            if clean_tags.get("disc"):
                try:
                    audio["discnumber"] = [str(int(clean_tags["disc"]))]
                except (ValueError, TypeError):
                    audio["discnumber"] = [str(clean_tags["disc"])]
            if clean_tags.get("year"):
                audio["date"] = [str(clean_tags["year"])]
            if clean_tags.get("genre"):
                audio["genre"] = [str(clean_tags["genre"])]
                
            # Embed Unsynced Lyrics
            if plain_lyrics:
                audio["lyrics"] = [str(plain_lyrics)]
                
            if cover_data:
                if resize_cover:
                    resized_data = resize_cover_image(cover_data)
                    if resized_data:
                        cover_data = resized_data
                        cover_mime = "image/jpeg"
                pic = Picture()
                pic.data = cover_data
                pic.mime = cover_mime
                pic.type = 3
                pic.desc = "Cover"
                audio.add_picture(pic)
            audio.save()
            
        elif ext in [".m4a", ".mp4"]:
            audio = MP4(dest_path)
            audio.clear()
            if clean_tags.get("title"):
                audio["\xa9nam"] = [str(clean_tags["title"])]
            if clean_tags.get("artist"):
                audio["\xa9ART"] = [str(clean_tags["artist"])]
            if clean_tags.get("album_artist"):
                audio["aART"] = [str(clean_tags["album_artist"])]
            if clean_tags.get("album"):
                audio["\xa9alb"] = [str(clean_tags["album"])]
            if clean_tags.get("track"):
                try:
                    audio["trkn"] = [(int(clean_tags["track"]), 0)]
                except (ValueError, TypeError):
                    pass
            if clean_tags.get("disc"):
                try:
                    audio["disk"] = [(int(clean_tags["disc"]), 0)]
                except (ValueError, TypeError):
                    pass
            if clean_tags.get("year"):
                audio["\xa9day"] = [str(clean_tags["year"])]
            if clean_tags.get("genre"):
                audio["\xa9gen"] = [str(clean_tags["genre"])]
                
            # Embed Unsynced Lyrics
            if plain_lyrics:
                audio["\xa9lyr"] = [str(plain_lyrics)]
                
            if cover_data:
                if resize_cover:
                    resized_data = resize_cover_image(cover_data)
                    if resized_data:
                        cover_data = resized_data
                        cover_mime = "image/jpeg"
                fmt = MP4Cover.FORMAT_JPEG
                if cover_mime == "image/png":
                    fmt = MP4Cover.FORMAT_PNG
                audio["covr"] = [MP4Cover(cover_data, imageformat=fmt)]
            audio.save()
            
        # 4. Save External Lyrics File if available
        if clean_tags.get("lyrics"):
            lyrics_to_save = clean_tags["lyrics"].get("synced") or clean_tags["lyrics"].get("plain")
            if lyrics_to_save:
                lrc_path = os.path.splitext(dest_path)[0] + ".lrc"
                try:
                    with open(lrc_path, "w", encoding="utf-8") as lrc_file:
                        lrc_file.write(str(lyrics_to_save))
                    print(f"Saved external lyrics file: {lrc_path}")
                except Exception as lrc_err:
                    print(f"Failed to save lyrics file: {lrc_err}")
            
        return True
    except Exception as e:
        print(f"Error tagging file {dest_path}: {e}")
        if os.path.exists(dest_path):
            try:
                os.remove(dest_path)
            except:
                pass
        raise e

def clean_and_tag_mp3(source_path, dest_path, clean_tags, resize_cover=True):
    return clean_and_tag_file(source_path, dest_path, clean_tags, resize_cover)

def copy_sequentially(files_mapping):
    """
    Copies files sequentially to preserve physical order.
    files_mapping is a list of dicts: [{"source": str, "dest": str, "tags": dict}]
    Returns list of results.
    """
    results = []
    
    def get_sort_key(item):
        dest = item["dest"]
        try:
            track = int(item["tags"].get("track", 0))
        except ValueError:
            track = 999
        try:
            disc = int(item["tags"].get("disc", 1))
        except ValueError:
            disc = 1
        return (os.path.dirname(dest), disc, track, dest)
        
    sorted_mapping = sorted(files_mapping, key=get_sort_key)
    
    for item in sorted_mapping:
        src = item["source"]
        dst = item["dest"]
        tags = item["tags"]
        
        try:
            success = clean_and_tag_file(src, dst, tags, resize_cover=True)
            results.append({"source": src, "dest": dst, "status": "success"})
            time.sleep(0.05) 
        except Exception as e:
            results.append({"source": src, "dest": dst, "status": "error", "message": str(e)})
            
    return results
