import json
import aiohttp
import random
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher

# =================================================================================
# SCRIPT: plex_smart_launch
# VERSION: v5.4
# CHANGES:
#   - FIX: Client scanning is now done in a loop.
#          The script waits up to 30 seconds for the player to appear, 
#          pressing Scan every 3 seconds.
# =================================================================================

# === 1. SETTINGS === Replace with your own values
PLEX_URL = "https://[plex_server_ip]:32400"        # Specify Plex Server IP and Port
PLEX_TOKEN = "XyZ987PvQgRtLmNoKjHs"                # Insert your Plex Token
PLEX_SCAN_BUTTON = "button.plex_190_scan_clients"  # Scan Clients Button (Find in HA Plex Integration)
VERIFY_SSL = False 
AI_ENTITY_ID = "ai_task.google_ai_task"            # Specify your Ai Task entity

# Cache (No editing/configuration needed)
PLEX_LIBS = {} 
PLEX_CACHE = {"movie": [], "show": [], "music": []}

# === 2. ZONES === Specify your entity IDs and Zone names (living_room, guest_room, bedroom)
ZONES = {
    "living_room": {
        "plex_client": "media_player.plex_plex_for_apple_tv_apple_tv", 
        "plex_device_id": "2d3845d406074601d143cf77ac3299ae", 
        "hardware_device_id": "84971b66f2f787bd9d999e9edd1f3d6a", 
        "hardware_entity": "media_player.apple_tv_4k", 
        "power_method": "apple_tv_device",   # Do not edit or replace in code below
        "boot_delay": 2, "app_load_delay": 6 # Time required to start TV and Plex App
    },
    "guest_room": {
        "plex_client": "media_player.plex_plex_for_lg_lg_oled42c2rlb",
        "hardware_device_id": "d025f3fd72c4d87b35bbda8128644844", 
        "hardware_entity": "media_player.lg_webos_tv_oled42c2rlb",
        "power_method": "lg_device",          # Do not edit or replace in code below
        "boot_delay": 6, "app_load_delay": 12      
    },
    "bedroom": {
        "plex_client": "media_player.plex_plex_for_samsung_tv_2019",
        "plex_device_id": "fc42e34c5ed19fff35f5e2ac4b4f55cc", 
        "remote_entity": "remote.samsung_the_frame_49_qe49ls03rauxru",
        "hardware_entity": "media_player.samsung_the_frame_49_qe49ls03rauxru", 
        "power_method": "samsung_remote",     # Do not edit or replace in code below
        "boot_delay": 6, "app_load_delay": 15
    }
}
# ======== End of Settings, Only the Prompt can be edited below ========


# === 3. AUTO-DISCOVERY OF LIBRARIES & CACHE ===
async def update_plex_cache():
    global PLEX_CACHE, PLEX_LIBS
    conn = aiohttp.TCPConnector(ssl=VERIFY_SSL)
    async with aiohttp.ClientSession(connector=conn) as session:
        try:
            url_libs = f"{PLEX_URL}/library/sections?X-Plex-Token={PLEX_TOKEN}"
            async with session.get(url_libs) as resp:
                if resp.status == 200:
                    root = ET.fromstring(await resp.text())
                    for directory in root.findall(".//Directory"):
                        l_type = directory.get("type")
                        if l_type == "artist": l_type = "music"
                        if l_type in ["movie", "show", "music"]:
                            PLEX_LIBS[l_type] = {"id": directory.get("key"), "title": directory.get("title")}
        except: return 

        for l_type, lib_info in PLEX_LIBS.items():
            try:
                url = f"{PLEX_URL}/library/sections/{lib_info['id']}/all?X-Plex-Token={PLEX_TOKEN}"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        xml_data = await resp.text()
                        root = ET.fromstring(xml_data)
                        items = []
                        if l_type == "movie":
                            for node in root.findall(".//Video"):
                                items.append({"title": node.get("title", "").lower(), "orig": node.get("originalTitle", "").lower(), "year": node.get("year"), "id": node.get("ratingKey")})
                        elif l_type in ["show", "music"]:
                            for node in root.findall(".//Directory"):
                                items.append({"title": node.get("title", "").lower(), "orig": node.get("originalTitle", "").lower(), "id": node.get("ratingKey")})
                        PLEX_CACHE[l_type] = items
            except: pass

def find_in_cache(target_type, query_string):
    lib = PLEX_CACHE.get(target_type, [])
    if not lib or not query_string: return None
    q = query_string.lower().strip()
    best, highest = None, 0.0
    for item in lib:
        r = max(SequenceMatcher(None, q, item["title"]).ratio(), SequenceMatcher(None, q, item.get("orig", "")).ratio())
        if r > 0.95: return item
        if r > 0.6 and r > highest: highest = r; best = item
    return best

# === 4. HARDWARE (HABR STYLE SCAN) ===
async def boot_hardware_process(zone_config):
    hw_entity = zone_config.get("hardware_entity")
    plex_client = zone_config["plex_client"]
    power = zone_config.get("power_method")
    
    # 1. Turn on TV/Set-top box
    if power == "apple_tv_device":
        await service.call("media_player", "turn_on", device_id=zone_config["hardware_device_id"])
    elif power == "lg_device":
        if state.get(hw_entity) in ["off", "unavailable"]:
             await service.call("media_player", "turn_on", device_id=zone_config["hardware_device_id"])
    elif power == "samsung_remote":
        await service.call("remote", "turn_on", entity_id=zone_config["remote_entity"])
    else:
        await service.call("media_player", "turn_on", entity_id=hw_entity)

    await task.sleep(zone_config["boot_delay"])

    # 2. Launch Plex (if needed)
    try:
        if power == "apple_tv_device" or state.getattr(hw_entity).get("source") != "Plex":
             await service.call("media_player", "select_source", entity_id=hw_entity, source="Plex")
             await task.sleep(zone_config["app_load_delay"])
    except: pass

    # 3. SCANNING LOOP (Habr Style)
    # Try 10 times with a 3-second pause (30 sec total) until the client becomes available
    for i in range(10):
        # If client appears (not unavailable/unknown/off) — exit loop
        current_state = state.get(plex_client)
        if current_state not in ["unavailable", "unknown", "off"]:
            log.debug(f"Plex Client found: {plex_client} (State: {current_state})")
            break
        
        # If not found — press Scan
        log.debug(f"Plex Client not found. Scanning... (Attempt {i+1}/10)")
        try: await service.call("button", "press", entity_id=PLEX_SCAN_BUTTON)
        except: pass
        
        await task.sleep(3)

# === 5. LOGIC ===
@service
def plex_smart_launch(command_text=None):
    if not command_text: return
    task.create(smartplex_execution, cmd=command_text)

async def smartplex_execution(cmd):
    if not PLEX_LIBS: await update_plex_cache()

    # === PROMPT ===
    prompt = (
        "You are SmartPlex, a Plex API driver. Output JSON only.\n"
        "CLEANUP: Remove '4k', 'uhd', 'imax', 'hdr' from title.\n\n"
        "1. HARD SCENARIOS (PRIORITY):\n"
        "- 'fresh', 'new', 'latest' -> sort_order='newest', shuffle=false, year=2024 (do not leave year empty).\n"
        "- 'old', 'classic', 'early' -> sort_order='oldest', year=2000 (approximate limit).\n"
        "- 'best', 'top', 'popular' -> sort_order='top_rated'.\n"
        "- 'Any', 'Random', 'Something' -> sort_order='random', shuffle=true.\n"
        "- 'Linkin Park 2023' (Music + Year) -> {artist: 'Linkin Park', year: 2023}.\n\n"

        "2. LANGUAGE RULES:\n"
        "- Genres MUST be in English: 'Comedy', 'Action', 'Drama', 'Sci-Fi'.\n"
        "- Use standard Plex genre names.\n\n"

        "3. ZONES (room):\n"
        "- 'living room', 'hall', 'main room' -> 'living_room'\n"
        "- 'guest room', 'kids room', 'small room' -> 'guest_room'\n"
        "- 'bedroom', 'bed' -> 'bedroom'\n\n"
        
        "4. TYPES (type):\n"
        "- 'movie': Movies.\n"
        "- 'show': TV Shows / Series.\n"
        "- 'music': Music.\n"
        "- 'music_video': Music Videos / Clips.\n"
        "- 'playlist': Playlists.\n\n"

        "5. QUERY FILLING RULES:\n"
        "--- MOVIE ---\n"
        "   * ALLOWED: title, year, genre, actor, director, studio, collection, country, decade, contentRating.\n"
        "--- SHOW ---\n"
        "   * ALLOWED: show_name, season, episode, genre, year, studio.\n"
        "--- MUSIC ---\n"
        "   * ALLOWED: artist, album, title, year, genre, mood.\n"
        "--- MUSIC_VIDEO ---\n"
        "   * ALLOWED: artist.\n\n"

        "6. CONTROLS (control):\n"
        "- resume_mode: 'resume' (continue, finish, resume), 'start' (play, watch, start). DEFAULT: 'start'.\n"
        "- sort_order: 'newest', 'oldest', 'top_rated', 'random', 'default'.\n"
        "- shuffle: true (if 'shuffle', 'mix' or request is generic).\n\n"
        
        "JSON OUTPUT:\n"
        "{\n"
        "  \"control\": { \"room\": \"...\", \"type\": \"...\", \"resume_mode\": \"start/resume\", \"sort_order\": \"...\", \"shuffle\": false },\n"
        "  \"query\": { \"title\": \"...\", \"show_name\": \"...\", \"artist\": \"...\", \"album\": \"...\", \"season\": null, \"episode\": null, \"actor\": \"...\", \"genre\": \"...\", \"year\": null, \"studio\": \"...\", \"collection\": \"...\", \"decade\": null, \"contentRating\": \"...\", \"mood\": \"...\" }\n"
        "}\n"
        f"USER COMMAND: {cmd}"
    )

    try:
        response = await service.call("ai_task", "generate_data", 
                                      entity_id=AI_ENTITY_ID, 
                                      task_name="SmartPlex", 
                                      instructions=prompt, return_response=True)
        data = json.loads(response.get('data', '').replace('```json', '').replace('```', '').strip())
        
        control = data.get("control", {})
        query = data.get("query", {})
        
        room_key = control.get("room", "living_room")
        if room_key not in ZONES: room_key = "living_room"
        zone = ZONES[room_key]
        
        hw_task = task.create(boot_hardware_process(zone))
        
        payload = {"allow_multiple": 1}
        m_type = control.get("type", "movie")
        
        if control.get("resume_mode") == "resume": payload["resume"] = 1
        else: payload["resume"] = 0; payload["offset"] = 0

        sort_mode = control.get("sort_order", "default")
        
        if sort_mode == "newest": 
            payload["sort"] = "originallyAvailableAt:desc"
            payload["shuffle"] = 0 
        elif sort_mode == "oldest": 
            payload["sort"] = "originallyAvailableAt:asc"
            payload["shuffle"] = 0
        elif sort_mode == "top_rated": 
            payload["sort"] = "audienceRating:desc"
            payload["shuffle"] = 0
        elif control.get("shuffle") or sort_mode == "random": 
            payload["shuffle"] = 1

        media_type = "MOVIE"

        # >>> SHOWS
        if m_type == "show":
            media_type = "EPISODE"
            lib_data = PLEX_LIBS.get("show")
            if lib_data: payload["library_name"] = lib_data["title"]
            
            s_name = query.get("show_name") or query.get("title")
            cached = find_in_cache("show", s_name)
            exact_episode = query.get("episode")
            
            if cached:
                if exact_episode: payload["show.id"] = cached["id"]
                else: payload["show.title"] = cached["title"] 
            else:
                if s_name: payload["show.title"] = s_name

            if query.get("season"): payload["season.index"] = int(query["season"])
            if query.get("episode"): payload["episode.index"] = int(query["episode"])
            
            if not exact_episode and control.get("resume_mode") == "resume":
                payload["episode.unwatched"] = 1 
                payload["resume"] = 1

        # >>> MUSIC
        elif m_type == "music":
            media_type = "MUSIC"
            lib_data = PLEX_LIBS.get("music")
            if lib_data: payload["library_name"] = lib_data["title"]
            
            artist = query.get("artist")
            cached = find_in_cache("music", artist)
            
            filter_active = query.get("year") or query.get("album") or query.get("title") or query.get("genre") or query.get("mood")
            
            if cached and not filter_active:
                payload["id"] = cached["id"]
                payload["shuffle"] = 1
            else:
                if artist: payload["artist.title"] = artist
                if query.get("album"): payload["album.title"] = query["album"]
                if query.get("title"): payload["track.title"] = query["title"]
                if query.get("genre"): payload["genre"] = query["genre"]
                if query.get("mood"): payload["mood"] = query["mood"]
                
            if query.get("year"): payload["year"] = int(query["year"])

        # >>> MUSIC VIDEOS
        elif m_type == "music_video":
            media_type = "MUSIC" 
            lib_data = PLEX_LIBS.get("music")
            if lib_data: payload["library_name"] = lib_data["title"]
            
            artist = query.get("artist")
            cached = find_in_cache("music", artist)
            
            if cached:
                payload["id"] = cached["id"]
                payload["shuffle"] = 1
            elif artist:
                payload["artist.title"] = artist
                payload["shuffle"] = 1

        # >>> MOVIES
        elif m_type == "movie":
            media_type = "MOVIE"
            lib_data = PLEX_LIBS.get("movie")
            if lib_data: payload["library_name"] = lib_data["title"]
            
            f_title = query.get("title")
            has_filters = query.get("actor") or query.get("genre") or query.get("studio") or query.get("collection") or query.get("decade")
            
            if f_title and not has_filters:
                cached = find_in_cache("movie", f_title)
                if cached: payload["id"] = cached["id"]
                else: payload["title"] = f_title
            else:
                if f_title: payload["title"] = f_title
                if query.get("actor"): payload["actor"] = query["actor"]
                if query.get("director"): payload["director"] = query["director"]
                if query.get("genre"): payload["genre"] = query["genre"]
                if query.get("year"): payload["year"] = int(query["year"])
                if query.get("unwatched"): payload["unwatched"] = 1
                if query.get("studio"): payload["studio"] = query["studio"]
                if query.get("collection"): payload["collection"] = query["collection"]
                if query.get("country"): payload["country"] = query["country"]
                if query.get("contentRating"): payload["contentRating"] = query["contentRating"]
                if query.get("decade"): payload["decade"] = int(query["decade"])

        # >>> PLAYLISTS
        elif m_type == "playlist":
            media_type = "PLAYLIST"
            p_title = query.get("title")
            await hw_task
            target_dev = zone.get("plex_device_id")
            target_ent = zone.get("plex_client")
            await service.call("media_player", "play_media", 
                               device_id=target_dev, entity_id=target_ent,
                               media_content_id=json.dumps({"playlist_name": p_title, "shuffle": 1}), 
                               media_content_type="PLAYLIST")
            return

        # 3. FINAL EXECUTION
        await hw_task 
        log.debug(f"SmartPlex Payload: {payload}")
        target_dev = zone.get("plex_device_id")
        target_ent = zone.get("plex_client") if not target_dev else None
        
        await service.call("media_player", "play_media", 
                           device_id=target_dev, entity_id=target_ent,
                           media_content_id=json.dumps(payload), 
                           media_content_type=media_type)

    except Exception as e:
        log.error(f"SmartPlex Error: {e}")

@time_trigger('startup')
@time_trigger('cron(0 * * * *)') 
async def cron_cache():
    await update_plex_cache()
