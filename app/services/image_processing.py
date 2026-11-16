"""Validates an uploaded image and produces the stored derivatives.

Pure bytes-in / bytes-out, no storage and no database, so it is easy to test.

Rules:
- The bytes must decode as a real JPEG, PNG or WebP. The client's Content-Type and the
  filename are never consulted.
- Size and pixel-count limits are checked before the pixels are decoded (decompression bombs).
- Output is re-encoded from the decoded pixels, which drops EXIF/GPS and every other metadata
  block. Only the colour profile (ICC) is carried over so colours stay accurate.
- A full-size derivative (long edge <= 2400px) and a preview (long edge <= 800px) are produced,
  both as WebP. Images are never upscaled.
"""
import io
import warnings
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_PIXELS = 40_000_000
FULL_MAX_EDGE = 2400
PREVIEW_MAX_EDGE = 800
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
OUTPUT_CONTENT_TYPE = "image/webp"
FULL_QUALITY = 85
PREVIEW_QUALITY = 80

INVALID_IMAGE_MESSAGE = "That file is not a valid JPEG, PNG or WebP image."
TOO_LARGE_MESSAGE = "Image is too large. The maximum size is 15 MB."
TOO_MANY_PIXELS_MESSAGE = "Image dimensions are too large. The maximum is 40 megapixels."


class InvalidImageError(Exception):
    pass


class ImageTooLargeError(Exception):
    pass


@dataclass(frozen=True)
class ProcessedImage:
    full: bytes
    preview: bytes
    width: int
    height: int
    content_type: str = OUTPUT_CONTENT_TYPE


def _encode(img: Image.Image, max_edge: int, quality: int, icc_profile: bytes | None) -> tuple[bytes, tuple[int, int]]:
    work = img.copy()
    work.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)  # only ever shrinks
    out = io.BytesIO()
    kwargs = {"quality": quality, "method": 4}
    if icc_profile:
        kwargs["icc_profile"] = icc_profile
    work.save(out, format="WEBP", **kwargs)
    return out.getvalue(), work.size


def process_image(data: bytes) -> ProcessedImage:
    if len(data) > MAX_UPLOAD_BYTES:
        raise ImageTooLargeError(TOO_LARGE_MESSAGE)
    if not data:
        raise InvalidImageError(INVALID_IMAGE_MESSAGE)

    try:
        with warnings.catch_warnings():
            # Pillow warns (and raises at 2x) on very large images; we do our own explicit
            # check below and treat any bomb signal as a rejection.
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(data))
            if img.format not in ALLOWED_FORMATS:
                raise InvalidImageError(INVALID_IMAGE_MESSAGE)
            width, height = img.size
            if width < 1 or height < 1:
                raise InvalidImageError(INVALID_IMAGE_MESSAGE)
            if width * height > MAX_PIXELS:
                raise ImageTooLargeError(TOO_MANY_PIXELS_MESSAGE)
            img.load()  # full decode; raises on truncated/corrupt data
            img.seek(0)  # animated images: first frame only
            icc_profile = img.info.get("icc_profile")
            img = ImageOps.exif_transpose(img)  # bake in orientation before EXIF is dropped
            if img.mode == "P" or "transparency" in img.info:
                img = img.convert("RGBA")
            elif img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
    except (InvalidImageError, ImageTooLargeError):
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ImageTooLargeError(TOO_MANY_PIXELS_MESSAGE)
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, EOFError):
        raise InvalidImageError(INVALID_IMAGE_MESSAGE)

    full, size = _encode(img, FULL_MAX_EDGE, FULL_QUALITY, icc_profile)
    preview, _ = _encode(img, PREVIEW_MAX_EDGE, PREVIEW_QUALITY, icc_profile)
    return ProcessedImage(full=full, preview=preview, width=size[0], height=size[1])
