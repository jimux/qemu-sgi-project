#!/usr/bin/env python3
"""Generate a 75x75 SGI .rgb silhouette icon to serve as /usr/local/lib/faces/userIcon
when the install doesn't ship its canonical face icons. Mimics the reference SGI icon
(a darker silhouette of a person on the desktop scheme's gray background)."""
from PIL import Image, ImageDraw

W = H = 75
im = Image.new('RGB', (W, H), (192, 192, 192))   # light gray IMD background
d = ImageDraw.Draw(im)

# Person silhouette (head + shoulders), shadow underneath.
# Head: filled dark gray circle
head_cx, head_cy, head_r = W // 2, H // 2 - 18, 12
d.ellipse((head_cx - head_r, head_cy - head_r, head_cx + head_r, head_cy + head_r),
          fill=(72, 72, 72))
# Body / shoulders: rounded trapezoid
sx, sy = W // 2, H // 2 + 6
shoulder_pts = [
    (sx - 24, sy + 4),
    (sx - 16, sy - 8),
    (sx + 16, sy - 8),
    (sx + 24, sy + 4),
    (sx + 22, sy + 14),
    (sx - 22, sy + 14),
]
d.polygon(shoulder_pts, fill=(72, 72, 72))
# Shadow (light) -- thin rectangle below
d.rectangle((W // 2 - 28, H - 12, W // 2 + 28, H - 8), fill=(140, 140, 140))

im.save('/home/jimmy/qemu-sgi/ip54_tftp_staging/userIcon', 'SGI')
print('wrote SGI .rgb file', W, 'x', H)
import os
print('size:', os.path.getsize('/home/jimmy/qemu-sgi/ip54_tftp_staging/userIcon'), 'bytes')
