import csv
import json
import os
import re
import sys
from pathlib import Path
import platform

# Dependency Checks

try:
    from mutagen.flac import FLAC
    from mutagen import MutagenError
except ImportError:
    sys.exit("[ERROR] 'mutagen' not installed.\nRun: pip install mutagen rapidfuzz colorama")

try:
    from rapidfuzz import fuzz, process as fuzz_process
except ImportError:
    sys.exit("[ERROR] 'rapidfuzz' not installed.\nRun: pip install mutagen rapidfuzz colorama")

try:
    import colorama
    colorama.init()
except ImportError:
    sys.stdout.write("[ERROR] 'colorama' not installed.\nRun pip install mutagen rapidfuzz colorama")
    pass   # colours still work on macOS/Linux without it

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

# Raw Keystroke Reader

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

# Arrow Key Menu

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
        print(_c(WHITE, "\n TIDAL CUE BUILDER - FILE NAVIGATOR", bold=True))
        print(_c(DIM,   " ------------------------------------"))
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
    """Navigate to a directory; press S to confirm."""
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
        print(_c(WHITE, f"\n TIDAL CUE BUILDER - {label}", bold=True))
        print(_c(DIM,    " ------------------------------------"))
        print(_c(CYAN,   f" Selected: {current}\n"))

        dirs = subdirs()
        for i, d in enumerate(dirs):
            pre = _c(CYAN, "> ", bold=True) if i == selected else "  "
            print(f"{pre}{_c(BLUE, f'[{d.name}]')}")

        print(_c(DIM,   "\n (↑↓ navigate · Enter open folder · Backspace go back)"))
        print(_c(GREEN,  " Press S to select the current folder"))
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

# Processing Logic

def _tag(audio, *keys) -> str:
    """Read the first matching tag from a mutagen audio object."""
    for k in keys:
        v = audio.get(k) or audio.get(k.upper()) or []
        if v: return v[0].strip()
    return ""

def sanitize_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", name).strip()

def cue_escape(s: str) -> str:
    return s.replace('"', '\\"')

def load_playlist(json_path: Path) -> tuple:
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("playlist", json_path.stem), data.get("tracks", [])

def scan_flac_library(library_root: Path) -> list:
    """
    Walk library_root recursively. For every .flac file found, read its
    TITLE, ARTIST, and ALBUM tags. Returns a list of track dicts, each with:
      path, title, artist, album, search_key
    search_key = "<title> <artist>" used for fuzzy matching.
    """
    tracks = []
    flac_count = 0

    for dirpath, _, filenames in os.walk(library_root):
        for fname in filenames:
            if not fname.lower().endswith(".flac"):
                continue
            flac_count += 1
            fpath = Path(dirpath) / fname

            title = artist = album = ""
            try:
                audio = FLAC(fpath)
                title  = _tag(audio, "title")
                artist = _tag(audio, "artist", "albumartist")
                album  = _tag(audio, "album")
            except MutagenError:
                pass

            if not title:
                title = fpath.stem  # fall back to filename if tag is missing

            tracks.append({
                "path":       fpath,
                "title":      title,
                "artist":     artist,
                "album":      album,
                "search_key": f"{title} {artist}".strip(),
            })

            sys.stdout.write(
                f"\r  {_c(DIM, '[*]')} Scanned {_c(WHITE, str(flac_count))} FLAC file(s),"
                f" {_c(CYAN, str(len(tracks)))} track(s) indexed...   "
            )
            sys.stdout.flush()

    print()
    return tracks

def match_track(pl_track: dict, library_tracks: list, search_keys: list, threshold: int):
    """
    Fuzzy-match a playlist track against the library by comparing
    "<title> <artist>" strings. Returns (lib_track, score) or None.
    """
    title   = (pl_track.get("title") or "").strip()
    artists = pl_track.get("artists", [])
    artist  = artists[0] if artists else ""
    query   = f"{title} {artist}".strip()

    result = fuzz_process.extractOne(
        query,
        search_keys,
        scorer=fuzz.token_sort_ratio,
        score_cutoff=threshold,
    )
    if result is None:
        return None
    _, score, idx = result
    return library_tracks[idx], score

def write_cue(playlist_name: str, matched_tracks: list, output_path: Path):
    """
    Write a multi-file CUE sheet. Each matched track gets its own FILE block
    with an absolute path, so the sheet works from any working directory.
    matched_tracks: list of (order, lib_track_dict, playlist_track_dict)
    """
    lines = []
    lines.append(f'TITLE "{cue_escape(playlist_name)}"')
    lines.append(f'PERFORMER ""')
    lines.append("")

    for order, lib_track, pl_track in matched_tracks:
        title  = pl_track.get("title") or lib_track["title"]
        artists = pl_track.get("artists", [])
        if artists:
            artist = ", ".join(artists)
        elif lib_track["artist"]:
            artist = lib_track["artist"]
        else:
            artist = ""

        file_path = str(lib_track["path"]).replace("\\", "/")

        lines.append(f'FILE "{cue_escape(file_path)}" WAVE')
        lines.append(f'  TRACK {order:02d} AUDIO')
        lines.append(f'    TITLE "{cue_escape(title)}"')
        lines.append(f'    PERFORMER "{cue_escape(artist)}"')
        lines.append(f'    INDEX 01 00:00:00')
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")

def process_playlist(json_path: Path, library_tracks: list, search_keys: list, threshold: int, output_dir: Path) -> tuple:
    playlist_name, tracks = load_playlist(json_path)
    valid_tracks = [t for t in tracks if t.get("title")]

    print(_c(GREEN, f"\n [+] Playlist: \"{playlist_name}\"  ({len(valid_tracks)} tracks)"))

    matched = []   # list of (order, lib_track, pl_track)
    skipped = []   # list of pl_track

    for i, pl_track in enumerate(valid_tracks, 1):
        label = _c(DIM, f"[{i}/{len(valid_tracks)}]")
        title = pl_track.get("title", "")

        result = match_track(pl_track, library_tracks, search_keys, threshold)

        if result is None:
            skipped.append(pl_track)
            print(f" {label} {_c(YELLOW, 'NOT FOUND')} \"{truncate(title, 35)}\"")
        else:
            lib_track, score = result
            matched.append((len(matched) + 1, lib_track, pl_track))
            lib_title = truncate(lib_track["title"], 25)
            print(
                f" {label} {_c(GREEN, 'MATCHED  ')} \"{truncate(title, 28)}\""
                f"  {_c(DIM, f'-> \"{lib_title}\" (score {score})')}"
            )

    if not matched:
        print(_c(YELLOW, f" [!] No tracks matched for \"{playlist_name}\" – skipping CUE."))
        return 0, len(valid_tracks)

    cue_path = output_dir / f"{sanitize_filename(playlist_name)}.cue"
    write_cue(playlist_name, matched, cue_path)

    print(
        _c(GREEN, "\n [SUCCESS]", bold=True) +
        f" {len(matched)}/{len(valid_tracks)} tracks → " +
        _c(CYAN, str(cue_path))
    )
    if skipped:
        print(_c(YELLOW, f" [!] {len(skipped)} track(s) not found in library (omitted from CUE):"))
        for t in skipped:
            artists = t.get("artists", [])
            artist_str = artists[0] if artists else "Unknown"
            print(_c(DIM, f"      - {t.get('title', '?')} — {artist_str}"))

    return len(matched), len(valid_tracks)

# MAIN APP

def start_app():
    clear_screen()
    print(_c(WHITE, "\n TIDAL CUE BUILDER", bold=True))
    print(_c(DIM,   " ------------------\n"))

    # 1 - Input Mode
    mode_idx = arrow_menu(
        "Choose playlist input mode:",
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

    # 2 - Scan FLAC Library
    library_root = folder_navigator(Path.home(), label="SELECT FLAC LIBRARY ROOT FOLDER")
    print(_c(BLUE, f"\n [*] Scanning FLAC library: {library_root}"))
    library_tracks = scan_flac_library(library_root)

    if not library_tracks:
        print(_c(RED, " [ERROR] No FLAC files found in the selected folder."))
        sys.exit(1)

    print(_c(GREEN, f" [+] Indexed {len(library_tracks)} FLAC track(s)."))

    search_keys = [t["search_key"] for t in library_tracks]

    # 3 - Fuzzy Search Threshold
    print()
    thresh_idx = arrow_menu(
        "Fuzzy match sensitivity (title + artist):",
        [
            "Strict  (90) – near-exact matches only",
            "Normal  (80) – handles punctuation & minor differences  [recommended]",
            "Lenient (65) – broader matching, catches more variants",
        ],
    )
    threshold = [90, 80, 65][thresh_idx]

    # 4 - Output Path
    default_out = str(Path.home() / "Music" / "Playlists")
    print(_c(YELLOW, "\n Enter output folder for .cue files (press Enter for default):"))
    raw = input(_c(DIM, f" [{default_out}] > ")).strip()
    output_dir = Path(raw) if raw else Path(default_out)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 5 - Process Each Playlist
    total_matched = total_tracks = 0
    for jp in json_paths:
        m, t = process_playlist(jp, library_tracks, search_keys, threshold, output_dir)
        total_matched += m
        total_tracks  += t

    # 6 - Summary
    print(_c(DIM,   "\n -----------------------------------------"))
    print(
        _c(WHITE, " DONE", bold=True) +
        f"  {total_matched}/{total_tracks} track(s) matched across {len(json_paths)} playlist(s)."
    )
    print(f" CUE files saved to: {_c(CYAN, str(output_dir))}\n")


if __name__ == "__main__":
    try:
        start_app()
    except KeyboardInterrupt:
        print(_c(DIM, "\n\n Cancelled.\n"))
        sys.exit(0)