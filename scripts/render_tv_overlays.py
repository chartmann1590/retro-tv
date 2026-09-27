"""Render native On-TV VOD and Sports overlays as 1280x720 HDMI TV screenshots."""
import os
import sys
sys.path.insert(0, os.path.abspath("."))
import subprocess
from PIL import Image

import tvvod
import tvsports

SCREENSHOTS_DIR = os.path.abspath("screenshots")
DOCS_SCREENSHOTS_DIR = os.path.abspath("docs/screenshots")
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
os.makedirs(DOCS_SCREENSHOTS_DIR, exist_ok=True)

def generate_ass_file(ass_lines, out_path):
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,24,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    dialogues = []
    for line in ass_lines:
        line = line.strip()
        if not line:
            continue
        dialogues.append(f"Dialogue: 0,0:00:00.00,0:01:00.00,Default,,0,0,0,,{line}\n")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(header + "".join(dialogues))


def render_overlay(name, ass_lines, bitmaps, out_png):
    ass_path = f"/tmp/{name}.ass"
    base_png = f"/tmp/{name}_base.png"
    generate_ass_file(ass_lines, ass_path)

    # Use ffmpeg with libass to render the ASS events over 1280x720 canvas
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=0x08121e:s=1280x720:d=1",
        "-vf", f"ass={ass_path}", "-vframes", "1", base_png
    ]
    subprocess.run(cmd, check=True, capture_output=True)

    # Composite bitmap overlays (posters/logos) if any
    img = Image.open(base_png).convert("RGBA")
    for b in bitmaps:
        # b: {"x": x, "y": y, "w": w, "h": h, "path": path, "fmt": fmt}
        raw_path = b["path"]
        w, h = b["w"], b["h"]
        if os.path.exists(raw_path):
            with open(raw_path, "rb") as rf:
                data = rf.read()
            if len(data) >= w * h * 4:
                # Format is BGRA
                b_img = Image.frombytes("RGBA", (w, h), data[:w * h * 4])
                # Convert BGRA to RGBA
                r, g, bl, a = b_img.split()
                rgba = Image.merge("RGBA", (bl, g, r, a))
                img.paste(rgba, (b["x"], b["y"]), rgba)

    img.save(out_png)
    print(f"Rendered {out_png} ({img.size[0]}x{img.size[1]})")


def capture_tv_vod():
    ass_lines = []
    bitmaps = []

    def mock_send(cmd):
        if not cmd:
            return {"error": "success"}
        if cmd[0] == "osd-overlay" and len(cmd) > 3:
            ass_lines.extend(cmd[3].splitlines())
        elif cmd[0] == "overlay-add" and len(cmd) >= 9:
            # ["overlay-add", id, x, y, path, offset, fmt, w, h, stride]
            bitmaps.append({
                "x": cmd[2],
                "y": cmd[3],
                "path": cmd[4],
                "fmt": cmd[6],
                "w": cmd[7],
                "h": cmd[8]
            })
        return {"error": "success"}

    tvvod._send = mock_send
    tvvod.open_vod()

    out_file = os.path.join(SCREENSHOTS_DIR, "tv-vod.png")
    render_overlay("tv_vod", ass_lines, bitmaps, out_file)
    # Also save to docs
    import shutil
    shutil.copyfile(out_file, os.path.join(DOCS_SCREENSHOTS_DIR, "tv-vod.png"))


def capture_tv_sports():
    ass_lines = []
    bitmaps = []

    def mock_send(cmd):
        if not cmd:
            return {"error": "success"}
        if cmd[0] == "osd-overlay" and len(cmd) > 3:
            ass_lines.extend(cmd[3].splitlines())
        elif cmd[0] == "overlay-add" and len(cmd) >= 9:
            bitmaps.append({
                "x": cmd[2],
                "y": cmd[3],
                "path": cmd[4],
                "fmt": cmd[6],
                "w": cmd[7],
                "h": cmd[8]
            })
        return {"error": "success"}

    import playback
    playback.mpv_alive = lambda: True

    tvsports._send = mock_send
    tvsports.open_sports()

    out_file = os.path.join(SCREENSHOTS_DIR, "tv-sports.png")
    render_overlay("tv_sports", ass_lines, bitmaps, out_file)
    import shutil
    shutil.copyfile(out_file, os.path.join(DOCS_SCREENSHOTS_DIR, "tv-sports.png"))


def capture_tv_sports_game():
    ass_lines = []
    bitmaps = []

    def mock_send(cmd):
        if not cmd:
            return {"error": "success"}
        if cmd[0] == "osd-overlay" and len(cmd) > 3:
            ass_lines.extend(cmd[3].splitlines())
        elif cmd[0] == "overlay-add" and len(cmd) >= 9:
            bitmaps.append({
                "x": cmd[2],
                "y": cmd[3],
                "path": cmd[4],
                "fmt": cmd[6],
                "w": cmd[7],
                "h": cmd[8]
            })
        return {"error": "success"}

    import playback
    playback.mpv_alive = lambda: True

    tvsports._send = mock_send
    import sports
    scores_resp = sports.get_all_scores()
    games = scores_resp.get("games", [])
    first_game = games[0] if games else None
    game_id = first_game.get("id") if first_game else "1"
    league = first_game.get("league", "nfl") if first_game else "nfl"
    sport = first_game.get("sport", "football") if first_game else "football"
    tvsports.open_sports(game_id=game_id, league=league, sport=sport)

    out_file = os.path.join(SCREENSHOTS_DIR, "tv-sports-game.png")
    render_overlay("tv_sports_game", ass_lines, bitmaps, out_file)
    import shutil
    shutil.copyfile(out_file, os.path.join(DOCS_SCREENSHOTS_DIR, "tv-sports-game.png"))


def main():
    import playback
    playback.mpv_alive = lambda: True

    print("Capturing On-TV VOD overlay...")
    capture_tv_vod()

    print("Capturing On-TV Sports overlay...")
    capture_tv_sports()

    print("Capturing On-TV Sports Game Center overlay...")
    capture_tv_sports_game()

    print("All TV view screenshots rendered successfully!")

if __name__ == "__main__":
    main()
