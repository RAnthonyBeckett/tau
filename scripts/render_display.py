"""Render the TAU display headless in each state and save a labelled grid.

    python scripts/render_display.py --out results/tau_display_states.png --gif results/tau_display_speaking.gif

Uses Playwright's Chromium with software WebGL. Each state is the page's
``?demo=<state>`` mode, served from the real display server on loopback.
"""

from __future__ import annotations

import argparse
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

STATES = [("idle", "Idle: slow breathing, counter-rotating rings"),
          ("listening", "Listening: inner rings close like a shutter"),
          ("thinking", "Thinking: frame pulses, rings spin up"),
          ("speaking", "Speaking: the rings pulse with the voice"),
          ("error", "Error: frame flickers, rings dim"),
          ("burst", "Burst lane: cyan accent on the brain ring")]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--gif", type=Path)
    ap.add_argument("--size", type=int, default=560)
    a = ap.parse_args(argv)

    from PIL import Image, ImageDraw, ImageFont
    from playwright.sync_api import sync_playwright

    from tau.display.server import DisplayHub

    hub = DisplayHub({"tau": {"name": "TAU"}})
    port = hub.serve(0)
    url = f"http://127.0.0.1:{port}/"
    shots = {}
    frames = []
    with sync_playwright() as p:
        import os
        exe = os.environ.get("TAU_CHROMIUM")  # optional: a Chromium binary other than Playwright's own
        browser = p.chromium.launch(executable_path=exe if exe and os.path.isfile(exe) else None, args=["--use-gl=angle", "--use-angle=swiftshader",
                                          "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        page = browser.new_page(viewport={"width": a.size, "height": a.size}, device_scale_factor=1)
        for state, _ in STATES:
            page.goto(f"{url}?demo={state}")
            page.wait_for_function("window.__tau && window.__tau.ready", timeout=20000)
            page.mouse.move(a.size * 0.9, a.size * 0.12)   # a little parallax, away from the rings
            time.sleep(2.2 if state != "error" else 2.0)
            shots[state] = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
        if a.gif:
            page.goto(f"{url}?demo=speaking")
            page.wait_for_function("window.__tau && window.__tau.ready", timeout=20000)
            time.sleep(1.5)
            for _ in range(24):
                frames.append(Image.open(io.BytesIO(page.screenshot())).convert("RGB").resize((360, 360)))
                time.sleep(0.07)
        browser.close()
    hub.stop()

    cols, pad, label_h = 3, 14, 34
    w = h = a.size
    rows = (len(STATES) + cols - 1) // cols
    grid = Image.new("RGB", (cols * w + (cols + 1) * pad, rows * (h + label_h) + (rows + 1) * pad), (12, 8, 8))
    d = ImageDraw.Draw(grid)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 17)
    except OSError:
        font = ImageFont.load_default()
    for i, (state, label) in enumerate(STATES):
        x = pad + (i % cols) * (w + pad)
        y = pad + (i // cols) * (h + label_h + pad)
        grid.paste(shots[state], (x, y + label_h))
        d.text((x + 4, y + 8), label, fill=(243, 217, 164), font=font)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    grid.save(a.out, optimize=True)
    print(f"wrote {a.out}")
    if a.gif and frames:
        frames[0].save(a.gif, save_all=True, append_images=frames[1:], duration=80, loop=0, optimize=True)
        print(f"wrote {a.gif}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
