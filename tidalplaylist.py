import os
import re
import sys
import json
import time
import base64
import hashlib
import secrets
import threading
import webbrowser
import urllib.parse
import http.server
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = "http://localhost:8888/callback"
COUNTRY_CODE = "US"
BASE_DELAY = 0.5
TOKEN_CACHE_PATH = Path(".tidal_token_cache.json")
SCOPES = "playlists.read"


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def sleep(ms):
    time.sleep(ms / 1000)

def truncate(s, length=40):
    return s[:length] + "..." if len(s) > length else s

def extract_playlist_id(url):
    if not url:
        return None
    match = re.search(r'playlist/([0-9a-fA-F-]{36})', url)
    return match.group(1) if match else None

def sanitize_filename(name):
    return re.sub(r'[<>:"/\\|?*]+', '_', name).strip()

def normalize_tidal_url(link):
    if not link:
        return None
    if link.startswith("/"):
        link = "https://openapi.tidal.com" + link
    elif not link.startswith("http"):
        link = "https://openapi.tidal.com/" + link

    parsed = urllib.parse.urlparse(link)
    parsed = parsed._replace(scheme="https", netloc="openapi.tidal.com")

    path = parsed.path
    if not path.startswith("/v2/"):
        path = "/v2/" + path.lstrip("/")
        parsed = parsed._replace(path=path)

    params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    if "countryCode" not in params:
        params["countryCode"] = [COUNTRY_CODE]
    if "include" not in params:
        params["include"] = ["items"]

    query = urllib.parse.urlencode({k: v[0] for k, v in params.items()})
    parsed = parsed._replace(query=query)
    return urllib.parse.urlunparse(parsed)

def get_user_home():
    return str(Path.home())


# ─────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────

def _basic_auth_header():
    credentials = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    return {"Authorization": f"Basic {credentials}", "Content-Type": "application/x-www-form-urlencoded"}

def _save_token_cache(token_data):
    with open(TOKEN_CACHE_PATH, "w") as f:
        json.dump(token_data, f, indent=2)

def _pkce_pair():
    """Generate a PKCE code_verifier and S256 code_challenge."""
    verifier  = secrets.token_urlsafe(64)
    digest    = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge

def _try_refresh_token(refresh_token):
    """Attempt to get a new access token using the cached refresh token."""
    res = requests.post(
        "https://auth.tidal.com/v1/oauth2/token",
        headers=_basic_auth_header(),
        data=urllib.parse.urlencode({
            "grant_type":    "refresh_token",
            "refresh_token": refresh_token,
        }),
    )
    if res.ok:
        token_data = res.json()
        _save_token_cache(token_data)
        print(" [*] Token refreshed from cache.")
        return token_data["access_token"]
    return None

def _auth_code_flow():
    """Full browser-based OAuth2 authorization code flow with PKCE (S256)."""
    code_verifier, code_challenge = _pkce_pair()

    auth_params = {
        "response_type":         "code",
        "client_id":             CLIENT_ID,
        "redirect_uri":          REDIRECT_URI,
        "scope":                 SCOPES,
        "code_challenge":        code_challenge,
        "code_challenge_method": "S256",
    }
    auth_url = "https://login.tidal.com/authorize?" + urllib.parse.urlencode(auth_params)

    print(f"\n [*] Opening browser for TIDAL login...")
    print(f" [*] If the browser doesn't open, visit:\n     {auth_url}\n")

    auth_code  = [None]
    auth_error = [None]
    done       = threading.Event()

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            # Ignore favicon silently
            if self.path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return

            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)

            if "code" in params:
                auth_code[0] = params["code"][0]
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"<h2>Authentication successful! You can close this tab.</h2>")
                done.set()
            elif "error" in params:
                error = params["error"][0]
                desc  = params.get("error_description", ["No description"])[0]
                auth_error[0] = f"{error}: {desc}"
                self.send_response(200)
                self.end_headers()
                self.wfile.write(
                    f"<h2>Authentication failed</h2><p>{auth_error[0]}</p>".encode()
                )
                done.set()
            else:
                self.send_response(200)
                self.end_headers()

        def log_message(self, *args):
            pass  # Suppress server logs

    server = http.server.HTTPServer(("localhost", 8888), CallbackHandler)
    server.timeout = 1

    def _serve():
        while not done.is_set():
            server.handle_request()
        server.server_close()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()

    webbrowser.open(auth_url)
    completed = done.wait(timeout=120)

    if not completed:
        server.server_close()
        raise Exception("Authentication timed out (120s). Make sure you completed the login in the browser.")

    if auth_error[0]:
        raise Exception(f"TIDAL auth error — {auth_error[0]}")

    if not auth_code[0]:
        raise Exception("No auth code received. Check that the redirect URI is registered in the TIDAL Developer Portal.")

    # Exchange the auth code for tokens — must include code_verifier to match the challenge
    res = requests.post(
        "https://auth.tidal.com/v1/oauth2/token",
        headers=_basic_auth_header(),
        data=urllib.parse.urlencode({
            "grant_type":    "authorization_code",
            "code":          auth_code[0],
            "redirect_uri":  REDIRECT_URI,
            "code_verifier": code_verifier,
        }),
    )

    if not res.ok:
        raise Exception(f"Token exchange failed: {res.status_code} — {res.text}")

    token_data = res.json()
    _save_token_cache(token_data)
    print(" [+] Authentication successful. Token cached.")
    return token_data["access_token"]

def get_access_token():
    """
    Returns a valid access token.
    Priority:
      1. Refresh cached token if available
      2. Full browser OAuth2 + PKCE flow
    """
    if TOKEN_CACHE_PATH.exists():
        try:
            with open(TOKEN_CACHE_PATH) as f:
                cached = json.load(f)
            if "refresh_token" in cached:
                token = _try_refresh_token(cached["refresh_token"])
                if token:
                    return token
        except (json.JSONDecodeError, KeyError):
            pass  # Cache corrupted — fall through to fresh login

    return _auth_code_flow()


# ─────────────────────────────────────────────
# API
# ─────────────────────────────────────────────

def fetch_with_retry(url, access_token, retries=5):
    for _ in range(retries):
        res = requests.get(url, headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.api+json",
        })
        if res.status_code == 429:
            retry_after = res.headers.get("retry-after")
            server_wait = int(retry_after) * 1000 if retry_after else 5000
            cut_wait = (server_wait // 3) + (1 if server_wait % 3 else 0)
            sys.stdout.write(f"\r\n [!] Rate Limit: Waiting {cut_wait}ms...\r\n")
            sys.stdout.flush()
            time.sleep(cut_wait / 1000)
            continue
        return res
    raise Exception("Max retries exceeded.")


# ─────────────────────────────────────────────
# Input Helpers
# ─────────────────────────────────────────────

def ask_question(query):
    return input(query)

def prompt_400_error(ref_id):
    import tty
    import termios
    sys.stdout.write(f"\r\n [!] HTTP 400 Error for Item ID: {ref_id}\r\n")
    sys.stdout.flush()
    options = ["Retry", "Skip", "Input New URL"]
    selected = [0]

    def render():
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.write("Choose action:\r\n")
        for i, opt in enumerate(options):
            if i == selected[0]:
                sys.stdout.write(f"\033[96;1m> {opt}\033[0m\r\n")
            else:
                sys.stdout.write(f"  {opt}\r\n")
        sys.stdout.flush()

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        render()
        while True:
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                ch2 = sys.stdin.read(2)
                if ch2 == "[A":
                    selected[0] = max(0, selected[0] - 1)
                elif ch2 == "[B":
                    selected[0] = min(len(options) - 1, selected[0] + 1)
                render()
            elif ch == "\r":
                break
            elif ch == "\x03":
                sys.exit()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    choice = options[selected[0]]
    if choice == "Input New URL":
        new_url = input("\r\nEnter new TIDAL URL: ")
        return {"action": "url", "url": new_url}
    return {"action": choice.lower()}


# ─────────────────────────────────────────────
# File Navigator
# ─────────────────────────────────────────────

def file_navigator(start_dir):
    import tty
    import termios

    current_dir = Path(start_dir).resolve()
    selected = [0]

    def get_items():
        try:
            entries = sorted(current_dir.iterdir(), key=lambda x: (not x.is_dir(), x.name))
            filtered = [e for e in entries if not e.name.startswith(".") and (e.is_dir() or e.suffix == ".txt")]
        except PermissionError:
            filtered = []
        return [{"name": "..", "is_dir": True}] + [
            {"name": e.name, "is_dir": e.is_dir()} for e in filtered
        ]

    def render():
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.write("\r\n TIDAL PLAYLIST EXPORTER - FILE NAVIGATOR\r\n")
        sys.stdout.write(" -----------------------------------------\r\n")
        sys.stdout.write(f" Directory: {current_dir}\r\n\r\n")
        items = get_items()
        if selected[0] >= len(items):
            selected[0] = len(items) - 1
        for i, item in enumerate(items):
            prefix = "\033[96;1m> \033[0m" if i == selected[0] else "  "
            name = f"\033[34m[{item['name']}]\033[0m" if item["is_dir"] else item["name"]
            sys.stdout.write(f"{prefix}{name}\r\n")
        sys.stdout.write("\r\n (Use Arrows to navigate, Enter to select/open, Backspace to go back)\r\n")
        sys.stdout.flush()

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    result = [None]

    try:
        tty.setraw(fd)
        render()
        while result[0] is None:
            ch = sys.stdin.read(1)
            items = get_items()
            if ch == "\x1b":
                ch2 = sys.stdin.read(2)
                if ch2 == "[A":
                    selected[0] = max(0, selected[0] - 1)
                elif ch2 == "[B":
                    selected[0] = min(len(items) - 1, selected[0] + 1)
            elif ch == "\r":
                item = items[selected[0]]
                new_path = (current_dir / item["name"]).resolve()
                if item["is_dir"]:
                    current_dir = new_path
                    selected[0] = 0
                else:
                    result[0] = str(new_path)
            elif ch == "\x7f":
                current_dir = current_dir.parent
                selected[0] = 0
            elif ch == "\x03":
                sys.exit()
            render()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    return result[0]


# ─────────────────────────────────────────────
# Playlist Processing
# ─────────────────────────────────────────────

def process_playlist(playlist_id, token):
    playlist_meta_url = normalize_tidal_url(f"/playlists/{playlist_id}")
    res = fetch_with_retry(playlist_meta_url, token)

    if not res or not res.ok:
        print(f" [!] Playlist ID {playlist_id} not found or inaccessible (HTTP {res.status_code if res else 'N/A'}).")
        return

    playlist_data = res.json()
    playlist_name = playlist_data["data"]["attributes"]["name"]
    safe_name = sanitize_filename(playlist_name)
    output_dir = Path(get_user_home()) / "Music/Playlist"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{safe_name}.json"

    print(f"\n [+] Found: \"{playlist_name}\"")

    all_track_refs = []
    next_url = normalize_tidal_url(f"/playlists/{playlist_id}/relationships/items")
    print(" [*] Mapping playlist structure...")

    while next_url:
        sleep(BASE_DELAY * 1000)
        res = fetch_with_retry(next_url, token)
        if not res or not res.ok:
            break
        data = res.json()
        if data.get("data"):
            all_track_refs.extend(data["data"])
        next_url = normalize_tidal_url(data["links"]["next"]) if data.get("links", {}).get("next") else None

    total_tracks = len(all_track_refs)
    print(f" [*] Processing {total_tracks} items...\n")

    final_tracks = []
    for i, ref in enumerate(all_track_refs):
        ref = dict(ref)
        track_order = i + 1
        progress_label = f"[{track_order}/{len(all_track_refs)}]"
        sleep(BASE_DELAY * 1000)

        success = False
        while not success:
            try:
                track_url = normalize_tidal_url(f"/tracks/{ref['id']}?include=artists,albums")
                res = fetch_with_retry(track_url, token)

                if res.status_code == 400:
                    decision = prompt_400_error(ref["id"])
                    if decision["action"] == "skip":
                        print(f" {progress_label} [SKIPPED] ID: {ref['id']}")
                        final_tracks.append({"order": track_order, "id": ref["id"], "status": "skipped"})
                        success = True
                    elif decision["action"] == "retry":
                        continue
                    elif decision["action"] == "url":
                        new_id = extract_playlist_id(decision["url"])
                        if new_id:
                            ref["id"] = new_id
                            continue
                        else:
                            print(" Invalid URL provided.")
                            continue
                elif res.ok:
                    data = res.json()
                    if data and data.get("data"):
                        track = data["data"]
                        included = data.get("included", [])
                        raw_artists = [
                            next((x["attributes"]["name"] for x in included if x["type"] == "artists" and x["id"] == r["id"]), "Unknown")
                            for r in track["relationships"]["artists"]["data"]
                        ]
                        album = next((x["attributes"]["title"] for x in included if x["type"] == "albums"), "Unknown Album")

                        final_tracks.append({
                            "order":   track_order,
                            "title":   track["attributes"]["title"],
                            "artists": raw_artists,
                            "album":   album,
                            "id":      ref["id"],
                            "isrc":    track["attributes"].get("isrc"),
                        })
                        print(f" {progress_label} Processing \"{truncate(track['attributes']['title'], 30)}\"")
                        success = True
                else:
                    raise Exception(f"HTTP {res.status_code}")
            except Exception as e:
                print(f" {progress_label} [ERROR] ID {ref['id']}: {e}")
                final_tracks.append({"order": track_order, "id": ref["id"], "status": "error"})
                success = True

        if (i + 1) % 5 == 0 or i == len(all_track_refs) - 1:
            with open(output_path, "w") as f:
                json.dump({"playlist": playlist_name, "tracks": final_tracks}, f, indent=2)

    print(f"\n [SUCCESS] JSON Saved to {output_path}\n")


# ─────────────────────────────────────────────
# Menu UI
# ─────────────────────────────────────────────

def start_app():
    import tty
    import termios

    sys.stdout.write("\033[2J\033[H")
    sys.stdout.write("\r\n TIDAL PLAYLIST EXPORTER\r\n")
    sys.stdout.write(" -----------------------\r\n\r\n")
    sys.stdout.flush()

    if not CLIENT_ID or not CLIENT_SECRET:
        sys.stdout.write(" [ERROR] Missing CLIENT_ID or CLIENT_SECRET in .env\r\n")
        sys.stdout.flush()
        sys.exit(1)

    modes = ["Single URL", "Text File (Multiple URLs)"]
    selected = [0]

    def get_menu():
        lines = []
        for i, mode in enumerate(modes):
            if i == selected[0]:
                lines.append(f"\033[96;1m> {mode}\033[0m")
            else:
                lines.append(f"  {mode}")
        return "\r\n".join(lines)

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    mode_choice = [None]

    try:
        tty.setraw(fd)
        sys.stdout.write("Choose export mode:\r\n")
        sys.stdout.write(get_menu() + "\r\n")
        sys.stdout.flush()

        while mode_choice[0] is None:
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                ch2 = sys.stdin.read(2)
                if ch2 == "[A":
                    selected[0] = 0
                elif ch2 == "[B":
                    selected[0] = 1
            elif ch == "\r":
                mode_choice[0] = modes[selected[0]]
                break
            elif ch == "\x03":
                sys.exit()

            sys.stdout.write("\033[3A\033[0J")
            sys.stdout.write("Choose export mode:\r\n")
            sys.stdout.write(get_menu() + "\r\n")
            sys.stdout.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    playlist_ids = []

    if mode_choice[0] == "Single URL":
        url = ask_question("\033[33mEnter TIDAL Playlist URL: \033[0m")
        pid = extract_playlist_id(url)
        if pid:
            playlist_ids.append(pid)
    else:
        file_path = file_navigator(os.getcwd())
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
        playlist_ids = [pid for line in lines if (pid := extract_playlist_id(line.strip()))]
        print(f" [*] Found {len(playlist_ids)} valid URLs in file.")

    if not playlist_ids:
        print(" [ERROR] No valid playlists found.")
        return

    try:
        print("\n [*] Authenticating...")
        token = get_access_token()
        for playlist_id in playlist_ids:
            process_playlist(playlist_id, token)
    except Exception as err:
        print(f"\n [CRITICAL] {err}")


start_app()
