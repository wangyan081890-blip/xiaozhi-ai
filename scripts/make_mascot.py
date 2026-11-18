"""Generate a transparent PNG mascot from the supplied white-background image."""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "static" / "assets" / "mascot-src.jpg"
DST = ROOT / "static" / "assets" / "mascot.png"


def main() -> None:
    im = Image.open(SRC).convert("RGB")
    a = np.asarray(im).astype(np.float32)
    h, w = a.shape[:2]

    # Whiteness metric: 0 for pure white, grows for coloured pixels.
    dist = np.sqrt(((255.0 - a) ** 2).sum(axis=2))

    # Flood fill the outer background only, so the white belly stays opaque.
    seeds = Image.fromarray(np.where(dist < 20.0, 255, 0).astype(np.uint8), "L").convert("RGB")
    ImageDraw.floodfill(seeds, (0, 0), (255, 0, 0), thresh=10)
    arr = np.asarray(seeds)
    bg = (arr[:, :, 0] > 200) & (arr[:, :, 1] < 80) & (arr[:, :, 2] < 80)

    # One-pixel rim around the background keeps edges smooth without a white halo.
    band = np.zeros_like(bg)
    for dy in (-2, -1, 1, 2):
        band |= np.roll(bg, dy, axis=0)
    for dx in (-2, -1, 1, 2):
        band |= np.roll(bg, dx, axis=1)
    band &= ~bg

    alpha = np.ones((h, w), dtype=np.float32)
    alpha[bg] = 0.0
    edge = np.clip((dist[band] - 4.0) / 28.0, 0.0, 1.0)
    alpha[band] = edge

    rgb = a.copy()
    # Un-blend rim pixels from the white backdrop so they don't glow light.
    ea = np.maximum(alpha[band][:, None], 1e-3)
    rgb[band] = np.clip((a[band] - (1.0 - ea) * 255.0) / ea, 0.0, 255.0)

    rgba = np.dstack([rgb, alpha * 255.0]).astype(np.uint8)
    img = Image.fromarray(rgba, "RGBA")

    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)

    pad = 8
    canvas = Image.new("RGBA", (img.width + pad * 2, img.height + pad * 2), (0, 0, 0, 0))
    canvas.paste(img, (pad, pad))

    # Display height ~110 CSS px; keep 4x for crisp scaling.
    target_h = 480
    if canvas.height > target_h:
        w2 = round(canvas.width * target_h / canvas.height)
        canvas = canvas.resize((w2, target_h), Image.LANCZOS)

    canvas.save(DST, optimize=True)
    print("saved", DST, canvas.size, DST.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
