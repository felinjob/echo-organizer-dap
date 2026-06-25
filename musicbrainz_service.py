import re
import urllib.parse
import time
import requests
import os

# Rate limiting: MusicBrainz allows 1 request per second.
LAST_REQUEST_TIME = 0.0
# Rate limiting: LRCLib (soft limit, usually ~1-2 requests per second is safe)
LAST_LRCLIB_REQUEST_TIME = 0.0

def rate_limited_get(url, headers=None):
    global LAST_REQUEST_TIME
    now = time.time()
    elapsed = now - LAST_REQUEST_TIME
    if elapsed < 1.0:
        time.sleep(1.0 - elapsed)
    
    if headers is None:
        headers = {}
    headers["User-Agent"] = "MusicTaggerDAP/1.0.0 (https://github.com/user/music-tagger-dap)"
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        LAST_REQUEST_TIME = time.time()
        if response.status_code == 200:
            try:
                return response.json()
            except Exception as json_err:
                print(f"MusicBrainz API JSON error: {json_err}")
                return None
        elif response.status_code == 403:
            print(f"MusicBrainz API HTTP 403 Forbidden. User-Agent might be blocked.")
        else:
            print(f"MusicBrainz API error: HTTP {response.status_code}")
    except Exception as e:
        print(f"Request exception: {e}")
        
    return None

def clean_filename(filename):
    """
    Cleans up common junk in music filenames to help regex parsing.
    Returns (track_num, artist, title) or (None, None, cleaned_title).
    """
    base = os.path.splitext(filename)[0] if '.' in filename else filename
    
    junk_patterns = [
        r"\b(official\s+video|official\s+audio|official\s+lyric\s+video|lyrics|lyric)\b",
        r"\b(hq|hd|4k|1080p|720p)\b",
        r"\b(remastered|remaster|live|acoustic|cover|mix|remix|edit)\b",
        r"\[.*?\]",
        r"\(.*?\)",
    ]
    
    cleaned = base
    for pattern in junk_patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
        
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"^[-_\s]+|[-_\s]+$", "", cleaned)
    
    match1 = re.match(r"^(\d+)\s*[-.]?\s*([^-]+)\s*-\s*(.+)$", cleaned)
    if match1:
        track = match1.group(1).strip()
        artist = match1.group(2).strip()
        title = match1.group(3).strip()
        return track, artist, title
        
    match2 = re.match(r"^([^-]+)\s*-\s*(.+)$", cleaned)
    if match2:
        artist = match2.group(1).strip()
        title = match2.group(2).strip()
        return None, artist, title
        
    match3 = re.match(r"^(\d+)\s*[-.]?\s*(.+)$", cleaned)
    if match3:
        track = match3.group(1).strip()
        title = match3.group(2).strip()
        return track, None, title
        
    return None, None, cleaned

def get_cover_art_url(release_id):
    """
    Fetches front cover art URL from Cover Art Archive for a release ID.
    """
    if not release_id:
        return None
    url = f"https://coverartarchive.org/release/{release_id}"
    data = rate_limited_get(url)
    if data and "images" in data:
        for img in data["images"]:
            if img.get("front") is True:
                thumbnails = img.get("thumbnails", {})
                if "500" in thumbnails:
                    return thumbnails["500"]
                elif "large" in thumbnails:
                    return thumbnails["large"]
                return img.get("image")
    return None

def fetch_lyrics_from_lrclib(artist, title, album=None):
    """
    Queries LRCLib API for synced or plain lyrics.
    Returns dict: {"synced": str, "plain": str} or None.
    """
    url = "https://lrclib.net/api/get"
    params = {
        "artist_name": artist,
        "track_name": title
    }
    if album:
        params["album_name"] = album
        
    headers = {
        "User-Agent": "MusicTaggerDAP/1.0.0 (https://github.com/user/music-tagger-dap)"
    }
    
    global LAST_LRCLIB_REQUEST_TIME
    
    try:
        now = time.time()
        elapsed = now - LAST_LRCLIB_REQUEST_TIME
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)
            
        response = requests.get(url, params=params, headers=headers, timeout=8)
        LAST_LRCLIB_REQUEST_TIME = time.time()
        
        if response.status_code == 200:
            try:
                data = response.json()
            except Exception as json_err:
                print(f"LRCLib JSON error: {json_err}")
                return None
                
            return {
                "synced": data.get("syncedLyrics"),
                "plain": data.get("plainLyrics")
            }
        elif response.status_code == 404:
            # Try search endpoint as fallback
            search_url = "https://lrclib.net/api/search"
            search_params = {"q": f"{artist} {title}"}
            now2 = time.time()
            elapsed2 = now2 - LAST_LRCLIB_REQUEST_TIME
            if elapsed2 < 1.0:
                time.sleep(1.0 - elapsed2)
                
            search_resp = requests.get(search_url, params=search_params, headers=headers, timeout=8)
            LAST_LRCLIB_REQUEST_TIME = time.time()
            
            if search_resp.status_code == 200:
                try:
                    results = search_resp.json()
                except Exception as json_err:
                    print(f"LRCLib Search JSON error: {json_err}")
                    return None
                    
                if results and isinstance(results, list) and len(results) > 0:
                    best_match = results[0]
                    return {
                        "synced": best_match.get("syncedLyrics"),
                        "plain": best_match.get("plainLyrics")
                    }
    except Exception as e:
        print(f"Error fetching lyrics from LRCLib for {artist} - {title}: {e}")
        
    return None

def normalize_genre(genre_raw):
    """
    Normalizes complex genres from MusicBrainz into standard broad DAP genres.
    """
    if not genre_raw:
        return ""
    genre_lower = str(genre_raw).lower().strip()
    if not genre_lower:
        return ""
    
    if "metal" in genre_lower:
        return "Metal"
    if "punk" in genre_lower:
        return "Punk"
    if "rock" in genre_lower:
        return "Rock"
    if "jazz" in genre_lower:
        return "Jazz"
    if "blues" in genre_lower:
        return "Blues"
    if "pop" in genre_lower:
        return "Pop"
    if "rap" in genre_lower or "hip" in genre_lower:
        return "Hip-Hop"
    if any(k in genre_lower for k in ["house", "techno", "electronic", "synth", "electro", "dance", "edm"]):
        return "Eletrônica"
    if any(k in genre_lower for k in ["classical", "clássica", "symphony", "baroque", "opera"]):
        return "Clássica"
    if "folk" in genre_lower:
        return "Folk"
    if "reggae" in genre_lower:
        return "Reggae"
    if "samba" in genre_lower or "pagode" in genre_lower:
        return "Samba"
    if "bossa" in genre_lower:
        return "Bossa Nova"
    if any(k in genre_lower for k in ["soundtrack", "ost", "trilha"]):
        return "Trilha Sonora"
        
    return genre_raw.title()

def search_musicbrainz(artist, title, query_str=None):
    """
    Queries MusicBrainz API for track info.
    Returns suggested tags dict: {title, artist, album_artist, album, track, disc, year, genre, cover_url} or None.
    """
    if artist and title:
        query = f'artist:"{artist}" AND recording:"{title}"'
    elif title:
        query = f'recording:"{title}"'
    elif query_str:
        query = query_str
    else:
        return None
        
    encoded_query = urllib.parse.quote(query)
    url = f"https://musicbrainz.org/ws/2/recording/?query={encoded_query}&fmt=json"
    
    data = rate_limited_get(url)
    if not data or "recordings" not in data or not data["recordings"]:
        return None
        
    recording = data["recordings"][0]
    
    suggested = {
        "title": recording.get("title", title),
        "artist": "",
        "album_artist": "",
        "album": "",
        "track": "",
        "disc": "1",
        "year": "",
        "genre": "",
        "cover_url": ""
    }
    
    artist_credits = recording.get("artist-credit", [])
    if artist_credits:
        suggested["artist"] = ", ".join([ac.get("name", "") for ac in artist_credits if "name" in ac])
    else:
        suggested["artist"] = artist or ""
        
    releases = recording.get("releases", [])
    if releases:
        def release_sort_key(r):
            score = 0
            if r.get("status") == "Official":
                score += 10
            if r.get("date"):
                score += 5
            return -score
            
        sorted_releases = sorted(releases, key=release_sort_key)
        best_release = sorted_releases[0]
        
        suggested["album"] = best_release.get("title", "")
        
        # Get Album Artist (Release-level Artist Credit)
        rel_artists = best_release.get("artist-credit", [])
        if rel_artists:
            suggested["album_artist"] = ", ".join([ac.get("name", "") for ac in rel_artists if "name" in ac])
        else:
            suggested["album_artist"] = suggested["artist"]
            
        # Get Year
        date = best_release.get("date", "")
        if date:
            suggested["year"] = date[:4]
            
        # Get Track Position and Disc Number
        media = best_release.get("media", [])
        if media:
            found_track = False
            for m_idx, m in enumerate(media):
                tracks = m.get("tracks", [])
                for t in tracks:
                    if t.get("recording", {}).get("id") == recording.get("id"):
                        suggested["track"] = t.get("number", "")
                        suggested["disc"] = str(m.get("position", m_idx + 1))
                        found_track = True
                        break
                if found_track:
                    break
            
            # Fallback: if not found, use first media track position
            if not suggested["track"] and tracks:
                suggested["track"] = tracks[0].get("number", "")
                suggested["disc"] = "1"
                
        # Get Cover Art URL
        release_id = best_release.get("id")
        if release_id:
            try:
                suggested["cover_url"] = get_cover_art_url(release_id) or ""
            except Exception as e:
                print(f"Error fetching cover art: {e}")
                
    tags = recording.get("tags", [])
    if not tags and "artist-credit" in recording and recording["artist-credit"]:
        artist_id = recording["artist-credit"][0].get("artist", {}).get("id")
        if artist_id:
            artist_url = f"https://musicbrainz.org/ws/2/artist/{artist_id}?fmt=json"
            artist_data = rate_limited_get(artist_url)
            if artist_data:
                tags = artist_data.get("tags", [])
                
    if tags:
        sorted_tags = sorted(tags, key=lambda x: x.get("count", 0), reverse=True)
        if sorted_tags:
            suggested["genre"] = normalize_genre(sorted_tags[0].get("name", ""))
            
    return suggested

def get_proposed_metadata(file_path):
    """
    Extracts tags, cleans filename, searches MusicBrainz,
    and returns combined original and proposed metadata.
    """
    from tagger_service import extract_metadata
    
    original = extract_metadata(file_path)
    filename = os.path.basename(file_path)
    
    heur_track, heur_artist, heur_title = clean_filename(filename)
    
    search_artist = original["artist"] or heur_artist
    search_title = original["title"] or heur_title
    
    proposed = {
        "title": original["title"] or heur_title or "",
        "artist": original["artist"] or heur_artist or "",
        "album_artist": original["album_artist"] or original["artist"] or heur_artist or "",
        "album": original["album"] or "",
        "track": original["track"] or heur_track or "",
        "disc": original["disc"] or "1",
        "year": original["year"] or "",
        "genre": normalize_genre(original["genre"]) or "",
        "has_cover": original["has_cover"],
        "cover_url": ""
    }
    
    mb_tags = search_musicbrainz(search_artist, search_title)
    if mb_tags:
        proposed["title"] = mb_tags.get("title") or proposed["title"]
        proposed["artist"] = mb_tags.get("artist") or proposed["artist"]
        proposed["album_artist"] = mb_tags.get("album_artist") or proposed["album_artist"]
        proposed["album"] = mb_tags.get("album") or proposed["album"]
        proposed["track"] = mb_tags.get("track") or proposed["track"]
        proposed["disc"] = mb_tags.get("disc") or proposed["disc"]
        proposed["year"] = mb_tags.get("year") or proposed["year"]
        proposed["genre"] = normalize_genre(mb_tags.get("genre")) or proposed["genre"]
        proposed["cover_url"] = mb_tags.get("cover_url") or ""
        if proposed["cover_url"]:
            proposed["has_cover"] = True
        proposed["source"] = "MusicBrainz"
    else:
        proposed["source"] = "Local Heuristics"
        
    if proposed["track"]:
        match = re.match(r"^(\d+)", str(proposed["track"]))
        if match:
            proposed["track"] = f"{int(match.group(1)):02d}"
            
    if proposed["disc"]:
        match = re.match(r"^(\d+)", str(proposed["disc"]))
        if match:
            proposed["disc"] = str(int(match.group(1)))
            
    return {
        "original": original,
        "proposed": proposed
    }
