"""
Image comparison helpers for Newport framebuffer tests.

Compares captured framebuffer images against saved references,
using per-pixel RGB distance with configurable thresholds.
"""

import os

REFERENCE_DIR = os.path.join(os.path.dirname(__file__), '..', 'reference_images')


def load_reference(name):
    """Load a reference image by name.

    Args:
        name: Reference image filename (e.g., 'prom_gradient.png')

    Returns:
        PIL.Image.Image or None if not found
    """
    from PIL import Image

    path = os.path.join(REFERENCE_DIR, name)
    if not os.path.exists(path):
        return None
    img = Image.open(path)
    img.load()
    return img


def save_reference(img, name):
    """Save an image as a reference.

    Args:
        img: PIL.Image.Image to save
        name: Reference image filename (e.g., 'prom_gradient.png')
    """
    os.makedirs(REFERENCE_DIR, exist_ok=True)
    path = os.path.join(REFERENCE_DIR, name)
    img.save(path)


def pixel_diff_percentage(img_a, img_b, threshold=10):
    """Compare two images pixel-by-pixel.

    Args:
        img_a: First PIL.Image.Image
        img_b: Second PIL.Image.Image
        threshold: Channel difference to count as "different" (0-255)

    Returns:
        (diff_fraction, max_channel_diff) where diff_fraction is 0.0-1.0
        representing the fraction of pixels differing by more than threshold,
        and max_channel_diff is the largest single-channel difference found.
    """
    if img_a.size != img_b.size:
        return (1.0, 255)

    a_rgb = img_a.convert('RGB')
    b_rgb = img_b.convert('RGB')
    w, h = a_rgb.size
    total = w * h

    try:
        import numpy as np
        a_arr = np.array(a_rgb, dtype=np.int16)
        b_arr = np.array(b_rgb, dtype=np.int16)
        diff = np.abs(a_arr - b_arr)
        max_diff = int(diff.max())
        pixels_over = int(np.any(diff > threshold, axis=2).sum())
        return (pixels_over / total, max_diff)
    except ImportError:
        pass

    # Fallback: per-pixel loop
    a_data = a_rgb.getdata()
    b_data = b_rgb.getdata()
    diff_count = 0
    max_diff = 0
    for (ar, ag, ab), (br, bg, bb) in zip(a_data, b_data):
        dr = abs(ar - br)
        dg = abs(ag - bg)
        db = abs(ab - bb)
        m = max(dr, dg, db)
        if m > max_diff:
            max_diff = m
        if m > threshold:
            diff_count += 1

    return (diff_count / total, max_diff)
