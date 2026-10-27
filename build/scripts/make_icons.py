"""Generate EVA's Windows app icon and tray icons from the 1024 px logo artwork.

Usage (needs Pillow; uv can supply it without installing anything):
    uv run --with pillow python build/scripts/make_icons.py [<logo folder> [<output folder>]]

<logo folder> defaults to branding/icon (the icon artwork kept in the repo) and holds:
    color/eva-icon-color-on-black.png
    black-and-white/eva-icon-bw-white-transparent.png
    black-and-white/eva-icon-bw-black-transparent.png
<output folder> defaults to build/assets. Writes:
    icon.ico           app / installer / uninstaller icon, 16-256 px (from color-on-black)
    icon.png           512 px copy of the same artwork
    tray-white*.png    tray icon for DARK taskbars  (white line-art)
    tray-black*.png    tray icon for LIGHT taskbars (black line-art)
                       each at 16 px plus @1.25x / @1.5x / @2x display-scaling sizes (20 / 24 / 32 px)

The tray line-art is only ~1 % of the canvas wide, so at 16-32 px it would fade to grey. Each tray size is
therefore built from an 8x oversampled copy whose strokes are thickened by THICKEN_PX target pixels first.
"""
import sys
from pathlib import Path

from PIL import Image, ImageFilter

THICKEN_PX = 1.0          # extra stroke width, in pixels of the final tray icon
OVERSAMPLE = 8
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]
TRAY_SIZES = {"": 16, "@1.25x": 20, "@1.5x": 24, "@2x": 32}


def thicken(src, size, color):
    """Return a size x size RGBA image of `src`'s alpha shape, thickened and filled with `color`."""
    big = size * OVERSAMPLE
    alpha = src.getchannel("A").resize((big, big), Image.LANCZOS)
    k = int(round(THICKEN_PX * OVERSAMPLE))
    if k % 2 == 0:
        k += 1
    alpha = alpha.filter(ImageFilter.MaxFilter(k)).resize((size, size), Image.LANCZOS)
    out = Image.new("RGBA", (size, size), color + (0,))
    out.putalpha(alpha)
    return out


def main(logos, out_dir):
    color = Image.open(logos / "color" / "eva-icon-color-on-black.png").convert("RGBA")
    white = Image.open(logos / "black-and-white" / "eva-icon-bw-white-transparent.png").convert("RGBA")
    black = Image.open(logos / "black-and-white" / "eva-icon-bw-black-transparent.png").convert("RGBA")
    out_dir.mkdir(parents=True, exist_ok=True)

    color.resize((512, 512), Image.LANCZOS).save(out_dir / "icon.png", optimize=True)
    color.resize((256, 256), Image.LANCZOS).save(
        out_dir / "icon.ico", format="ICO", sizes=[(s, s) for s in ICO_SIZES])

    for name, art, rgb in (("white", white, (255, 255, 255)), ("black", black, (0, 0, 0))):
        for suffix, size in TRAY_SIZES.items():
            thicken(art, size, rgb).save(out_dir / f"tray-{name}{suffix}.png", optimize=True)

    for p in sorted(out_dir.glob("icon.*")) + sorted(out_dir.glob("tray-*.png")):
        print(f"{p.name:22} {p.stat().st_size:7} bytes")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        sys.exit(__doc__)
    repo = Path(__file__).resolve().parents[2]
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else repo / "branding" / "icon",
         Path(sys.argv[2]) if len(sys.argv) > 2 else repo / "build" / "assets")
