"""On-screen titles: drawn to a transparent PNG with Pillow, then overlaid by the timeline export.

Pillow (not ffmpeg's drawtext) keeps the result independent of the machine's fonts and ffmpeg build.
The editor's preview (studio.css `.ttl`) uses the same bundled font and the same maths, all relative to
the frame: x/y are the text block's centre as a fraction of width/height, size is a fraction of height.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT = Path(__file__).parent / 'static' / 'fonts' / 'DejaVuSans-Bold.ttf'
LINE_HEIGHT = 1.25   # em; the preview's CSS uses the same value
PADDING = 0.4        # em around the text when the box is on
MAX_WIDTH = 0.9      # of the frame width, before wrapping


def hex_rgb(color):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def wrap(text, font, limit):
    lines = []
    for paragraph in text.split('\n'):
        words, line = paragraph.split(' '), ''
        for word in words:
            attempt = f'{line} {word}'.strip() if line else word
            if line and font.getlength(attempt) > limit:
                lines.append(line); line = word
            else:
                line = attempt
        lines.append(line)
    return lines


def render_png(title, width, height, path):
    """Write the title as a width x height RGBA picture (transparent except for the text and its box)."""
    size = max(8, round(title['size'] * height))
    font = ImageFont.truetype(str(FONT), size)
    lines = wrap(title['text'], font, MAX_WIDTH * width)
    step = size * LINE_HEIGHT
    block_w = max(font.getlength(line) for line in lines)
    block_h = step * len(lines)
    pad = PADDING * size if title.get('box') else 0
    cx = min(max(title['x'] * width, block_w / 2 + pad), width - block_w / 2 - pad)
    cy = min(max(title['y'] * height, block_h / 2 + pad), height - block_h / 2 - pad)
    image = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if title.get('box'):
        draw.rounded_rectangle((cx - block_w / 2 - pad, cy - block_h / 2 - pad, cx + block_w / 2 + pad, cy + block_h / 2 + pad),
                               radius=pad * .6, fill=(0, 0, 0, 160))
    color = hex_rgb(title['color'])
    outline = max(1, size // 16) if not title.get('box') else 0
    for index, line in enumerate(lines):
        draw.text((cx, cy - block_h / 2 + step * (index + .5)), line, font=font, fill=color + (255,), anchor='mm',
                  stroke_width=outline, stroke_fill=(0, 0, 0, 255))
    image.save(path)
    return path
