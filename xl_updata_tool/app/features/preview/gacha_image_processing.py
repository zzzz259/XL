"""Image geometry shared by the Android bundle and the visual asset updater."""

from PIL import Image

PORTRAIT_SIZE = 256
PORTRAIT_CROP = (38, 0, 180, 180)
# CommonResource.HeadBg: 320x140 PicLoader at (-91, -23), with an 88x88 ellipse.
ACCOUNT_HEAD_CROP = (91, 23, 88, 88)
ACCOUNT_HEAD_SOURCE_SIZE = (320, 140)
SQUARE_SOURCE_SIZE = (140, 270)


def alpha_bbox(image):
    rgba = image if image.mode == "RGBA" else image.convert("RGBA")
    return rgba.getchannel("A").getbbox()


def save_lossless_webp(image, path, duration=None, loop=None, append_images=None):
    options = {"lossless": True, "quality": 100, "method": 4, "exact": True}
    if duration is not None:
        options["duration"] = duration
    if loop is not None:
        options["loop"] = loop
    if append_images is not None:
        options["save_all"] = True
        options["append_images"] = append_images
    image.save(path, "WEBP", **options)


def normalize_portrait(image, square_size):
    """Center the visible pixels horizontally on a square canvas."""
    canvas = Image.new("RGBA", (square_size, square_size))
    bbox = alpha_bbox(image)
    if bbox is not None:
        x0, _, x1, _ = bbox
        dx = round((square_size - (x1 - x0)) / 2) - x0
        canvas.paste(image.convert("RGBA"), (dx, 0))
    return canvas


def trim_portrait(image, margin=4):
    """Keep a margin around the visible portrait and fit it to the display canvas."""
    canvas = Image.new("RGBA", (PORTRAIT_SIZE, PORTRAIT_SIZE))
    bbox = alpha_bbox(image)
    if bbox is None:
        return canvas
    box = (
        max(0, bbox[0] - margin), max(0, bbox[1] - margin),
        min(image.width, bbox[2] + margin), min(image.height, bbox[3] + margin),
    )
    cropped = image.convert("RGBA").crop(box)
    scale = min(PORTRAIT_SIZE / cropped.width, PORTRAIT_SIZE / cropped.height)
    size = (max(1, round(cropped.width * scale)), max(1, round(cropped.height * scale)))
    resized = cropped.resize(size, Image.Resampling.LANCZOS)
    canvas.paste(resized, ((PORTRAIT_SIZE - size[0]) // 2, (PORTRAIT_SIZE - size[1]) // 2))
    return canvas


def prepare_portrait(image, *, square_fallback=False):
    if square_fallback:
        if image.size != SQUARE_SOURCE_SIZE:
            raise ValueError(f"unexpected CardHeadSquare source size: {image.size}")
        image = normalize_portrait(image, SQUARE_SOURCE_SIZE[1])
    return trim_portrait(image)
