import csv
import json
import os
import sys
from pathlib import Path
import platform

try:
    from mutagen.flac import FLAC
    from mutagen import MutagenError
except ImportError:
    sys.exit("[ERROR] 'mutagen' not installed.\nRun: pip install mutagen rapidfuzz colorama")

try:
    from rapidfuzz import fuzz, process as fuzz_process
except ImportError:
    sys.exit("[ERROR] 'mutagen' not installed.\nRun: pip install mutagen rapidfuzz colorama")

try:
    import colorama
    colorama.init()
except ImportError:
    pass # Doesn't matter for macOS/Linux

RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
CYAN   = "\033[96m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
BLUE   = "\033[94m"
WHITE  = "\033[97m"

def _c(color: str, text: str, bold: bool = False) -> str:
    b = BOLD if bold else ""
    return f"{b}{color}{text}{RESET}"
 
def clear_screen():
    os.system("cls" if platform.system() == "Windows" else "clear")
 
def erase_lines(n: int):
    """Move cursor up n lines and erase to end of screen."""
    sys.stdout.write(f"\033[{n}A\033[0J")
    sys.stdout.flush()
 
def truncate(s: str, length: int = 40) -> str:
    return s[:length] + "..." if len(s) > length else s

# Key Stroke Reader

IS_WINDOWS = platform.system() == "Windows"
 
if IS_WINDOWS:
    import msvcrt
else:
    import tty
    import termios
 
 
def read_key() -> str:
    """Return a semantic key: 'up' | 'down' | 'enter' | 'backspace' | 'char:<c>'"""
    if IS_WINDOWS:
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            ch2 = msvcrt.getwch()
            return {"H": "up", "P": "down"}.get(ch2, "unknown")
        if ch == "\r":    return "enter"
        if ch == "\x08":  return "backspace"
        if ch == "\x03":  raise KeyboardInterrupt
        return f"char:{ch}"
    else:
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
            if ch == "\x03": raise KeyboardInterrupt
            if ch == "\r":   return "enter"
            if ch in ("\x7f", "\x08"): return "backspace"
            if ch == "\x1b":
                ch2 = sys.stdin.read(1)
                if ch2 == "[":
                    ch3 = sys.stdin.read(1)
                    return {"A": "up", "B": "down"}.get(ch3, "unknown")
            return f"char:{ch}"
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

# Arrow Key menu

def arrow_menu(prompt: str, options: list) -> int:
    """Render an arrow-key menu inline; return chosen index."""
    selected = 0
    total_lines = len(options) + 1  # prompt line + option lines
 
    def render(first: bool = False):
        if not first:
            erase_lines(total_lines)
        print(prompt)
        for i, opt in enumerate(options):
            if i == selected:
                print(_c(CYAN, f"> {opt}", bold=True))
            else:
                print(f"  {opt}")
 
    render(first=True)
    while True:
        key = read_key()
        if key == "up":
            selected = max(0, selected - 1)
        elif key == "down":
            selected = min(len(options) - 1, selected + 1)
        elif key == "enter":
            print()
            return selected
        render()

# File Navigator

def _dir_items(current: Path, extensions: tuple) -> list:
    """Return [parent_sentinel, ...dirs_and_matching_files] for the navigator."""
    try:
        entries = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        entries = []
    visible = [p for p in entries if p.is_dir() or p.suffix.lower() in extensions]
    return [current.parent] + visible   # index 0 is always ".."
 
 
def file_navigator(start_dir: Path, extensions: tuple = (".json",)) -> Path:
    """Navigate the filesystem and select a file. Returns chosen Path."""
    current = start_dir.resolve()
    selected = 0
 
    def render():
        clear_screen()
        print(_c(WHITE, "\n TIDAL MISSING ALBUMS - FILE NAVIGATOR", bold=True))
        print(_c(DIM,   " ----------------------------------------"))
        print(_c(CYAN,  f" Directory: {current}\n"))
 
        items = _dir_items(current, extensions)
        idx = max(0, min(selected, len(items) - 1))
 
        for i, item in enumerate(items):
            pre = _c(CYAN, "> ", bold=True) if i == idx else "  "
            if i == 0:
                name = _c(BLUE, "[..]")
            elif item.is_dir():
                name = _c(BLUE, f"[{item.name}]")
            else:
                name = _c(WHITE, item.name)
            print(f"{pre}{name}")
 
        print(_c(DIM, "\n (↑↓ navigate · Enter select/open · Backspace go back · Ctrl+C quit)"))
        return items, idx
 
    items, idx = render()
    while True:
        key = read_key()
        items = _dir_items(current, extensions)
 
        if key == "up":
            selected = max(0, selected - 1)
        elif key == "down":
            selected = min(len(items) - 1, selected + 1)
        elif key == "backspace":
            current = current.parent.resolve()
            selected = 0
        elif key == "enter":
            chosen = items[max(0, min(selected, len(items) - 1))]
            if chosen == current.parent or chosen.is_dir():
                current = chosen.resolve()
                selected = 0
            else:
                clear_screen()
                return chosen
        render()

# Folder Navigator

def folder_navigator(start_dir: Path, label: str = "SELECT FOLDER") -> Path:
    """Navigate to a directory; press S (or Enter on an empty list) to confirm."""
    current = start_dir.resolve()
    selected = 0
 
    def subdirs():
        try:
            return sorted(
                [p for p in current.iterdir() if p.is_dir()],
                key=lambda p: p.name.lower()
            )
        except PermissionError:
            return []
 
    def render():
        clear_screen()
        print(_c(WHITE, f"\n TIDAL MISSING ALBUMS - {label}", bold=True))
        print(_c(DIM,    " ----------------------------------------"))
        print(_c(CYAN,   f" Selected: {current}\n"))
 
        dirs = subdirs()
        for i, d in enumerate(dirs):
            pre = _c(CYAN, "> ", bold=True) if i == selected else "  "
            print(f"{pre}{_c(BLUE, f'[{d.name}]')}")
 
        print(_c(DIM,  "\n (↑↓ navigate · Enter open folder · Backspace go back)"))
        print(_c(GREEN, " Press S to select the current folder"))
        return dirs
 
    subdirs_list = render()
    while True:
        key = read_key()
        subdirs_list = subdirs()
 
        if key == "up":
            selected = max(0, selected - 1)
        elif key == "down":
            selected = min(len(subdirs_list) - 1, selected + 1) if subdirs_list else 0
        elif key == "backspace":
            current = current.parent.resolve()
            selected = 0
        elif key == "enter" and subdirs_list:
            current = subdirs_list[max(0, min(selected, len(subdirs_list) - 1))].resolve()
            selected = 0
        elif key in ("char:s", "char:S"):
            clear_screen()
            return current
        render()

# Core Logic

def load_playlist(json_path: Path) -> list:
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("tracks", [])
 
 
def build_album_map(tracks: list) -> dict:
    album_map: dict = {}
    for t in tracks:
        album = (t.get("album") or "").strip()
        if not album or album == "Unknown Album":
            continue
        artists = t.get("artists", [])
        if isinstance(artists, list):
            artist_str = ", ".join(a for a in artists if a and a != "Unknown")
        else:
            artist_str = str(artists).strip()
        album_map.setdefault(album, set())
        if artist_str:
            album_map[album].add(artist_str)
    return album_map
 
 
def merge_album_maps(*maps) -> dict:
    merged: dict = {}
    for m in maps:
        for album, artists in m.items():
            merged.setdefault(album, set()).update(artists)
    return merged
 
 
def scan_flac_library(library_root: Path) -> list:
    found: set = set()
    flac_count = 0
 
    for dirpath, _, filenames in os.walk(library_root):
        for fname in filenames:
            if not fname.lower().endswith(".flac"):
                continue
            flac_count += 1
            fpath = Path(dirpath) / fname
            album_tag = None
            try:
                audio = FLAC(fpath)
                tags = audio.get("album") or audio.get("ALBUM") or []
                if tags:
                    album_tag = tags[0].strip()
            except MutagenError:
                pass
 
            if not album_tag:
                album_tag = Path(dirpath).name.strip()
            if album_tag:
                found.add(album_tag)
 
            sys.stdout.write(
                f"\r  {_c(DIM, '[*]')} Scanned {_c(WHITE, str(flac_count))} FLAC file(s),"
                f" {_c(CYAN, str(len(found)))} unique album(s)...   "
            )
            sys.stdout.flush()
 
    print()
    return list(found)
 
 
def find_missing(playlist_albums: dict, library_albums: list, threshold: int) -> list:
    missing = []
    total = len(playlist_albums)
 
    for i, (album, artists) in enumerate(sorted(playlist_albums.items()), 1):
        label = _c(DIM, f"[{i}/{total}]")
 
        if not library_albums:
            missing.append({"Album": album, "Artist(s)": " / ".join(sorted(artists))})
            print(f" {label} {_c(YELLOW, 'MISSING')} \"{truncate(album, 35)}\"")
            continue
 
        match = fuzz_process.extractOne(
            album,
            library_albums,
            scorer=fuzz.token_sort_ratio,
            score_cutoff=threshold,
        )
 
        if match is None:
            missing.append({"Album": album, "Artist(s)": " / ".join(sorted(artists))})
            print(f" {label} {_c(YELLOW, 'MISSING')} \"{truncate(album, 35)}\"")
        else:
            print(
                f" {label} {_c(GREEN, 'FOUND  ')} \"{truncate(album, 28)}\""
                f"  {_c(DIM, f'≈ \"{truncate(match[0], 25)}\" (score {match[1]})')}"
            )
 
    return missing
 
 
def write_csv(rows: list, output_path: Path):
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["Album", "Artist(s)"])
        writer.writeheader()
        writer.writerows(rows)

# MAIN APP
 
def start_app():
    clear_screen()
    print(_c(WHITE, "\n TIDAL MISSING ALBUMS", bold=True))
    print(_c(DIM,   " ---------------------\n"))
  
    # Input Mode
    mode_idx = arrow_menu(
        "Choose input mode:",
        ["Single JSON file", "Multiple JSON files (select a folder)"],
    )
 
    json_paths: list = []
 
    if mode_idx == 0:
        chosen = file_navigator(Path.cwd(), extensions=(".json",))
        json_paths = [chosen]
    else:
        chosen_dir = folder_navigator(Path.cwd(), label="SELECT FOLDER CONTAINING JSON FILES")
        json_paths = sorted(chosen_dir.glob("*.json"))
        if not json_paths:
            print(_c(RED, f"\n [ERROR] No .json files found in {chosen_dir}"))
            sys.exit(1)
        print(_c(BLUE, f" [*] Found {len(json_paths)} JSON file(s)."))

    # 2 - Load Playlist
    print(_c(BLUE, "\n [*] Loading playlist data..."))
    all_maps = []
    for jp in json_paths:
        try:
            tracks = load_playlist(jp)
            all_maps.append(build_album_map(tracks))
            print(_c(GREEN, f" [+] {jp.name}  ({len(tracks)} tracks)"))
        except (json.JSONDecodeError, OSError) as e:
            print(_c(RED, f" [!] Could not read {jp.name}: {e}"))
 
    if not all_maps:
        print(_c(RED, " [ERROR] No valid playlist data found."))
        sys.exit(1)
 
    playlist_albums = merge_album_maps(*all_maps)
    print(_c(BLUE, f"\n [*] {len(playlist_albums)} unique album(s) across all playlist(s)."))
 
    # 3 - Library Scan
    print()
    scan_idx = arrow_menu(
        "Scan a local FLAC library to filter out albums you already own?",
        ["Yes – navigate to my library folder", "No  – export all albums as missing"],
    )
    skip_scan = scan_idx == 1
 
    library_albums: list = []
    if not skip_scan:
        library_root = folder_navigator(Path.home(), label="SELECT FLAC LIBRARY ROOT FOLDER")
        print(_c(BLUE, f"\n [*] Scanning FLAC library: {library_root}"))
        library_albums = scan_flac_library(library_root)
        print(_c(GREEN, f" [+] Scan complete. {len(library_albums)} unique album(s) in library."))
 
    # 4 - Fuzzy Threshold
    threshold = 85
    if not skip_scan and library_albums:
        print()
        thresh_idx = arrow_menu(
            "Fuzzy match sensitivity:",
            [
                "Strict  (95) – near-exact matches only",
                "Normal  (85) – handles punctuation & minor differences  [recommended]",
                "Lenient (70) – broader matching, may catch more variants",
            ],
        )
        threshold = [95, 85, 70][thresh_idx]
 
    # 5 - Output Path
    default_out = str(Path.home() / "Music" / "missing_albums.csv")
    print(_c(YELLOW, "\n Enter output CSV path (press Enter for default):"))
    raw = input(_c(DIM, f" [{default_out}] > ")).strip()
    output_path = Path(raw) if raw else Path(default_out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
 
    # 6 - Compare
    print(_c(BLUE, f"\n [*] Comparing {len(playlist_albums)} album(s)"
                   + (f" with threshold={threshold}..." if not skip_scan else "...") + "\n"))
    missing = find_missing(playlist_albums, library_albums, threshold)

    # 7 - Write CSV 
    if not missing:
        print(_c(GREEN, "\n [✓] Your library covers every album in the playlist(s). Nothing to buy!\n"))
    else:
        write_csv(missing, output_path)
        print(
            "\n" +
            _c(GREEN, " [SUCCESS]", bold=True) + " " +
            _c(WHITE, f"{len(missing)} missing album(s) saved to:") +
            f"\n          {_c(CYAN, str(output_path))}\n"
        )
 
 
if __name__ == "__main__":
    try:
        start_app()
    except KeyboardInterrupt:
        print(_c(DIM, "\n\n Cancelled.\n"))
        sys.exit(0)
 
