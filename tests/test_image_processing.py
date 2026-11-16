import io

import pytest
from PIL import Image

from app.services import image_processing as ip
from tests.conftest import make_image_bytes


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_accepts_real_formats_and_outputs_webp(fmt):
    out = ip.process_image(make_image_bytes(fmt))
    assert Image.open(io.BytesIO(out.full)).format == "WEBP"
    assert Image.open(io.BytesIO(out.preview)).format == "WEBP"
    assert (out.width, out.height) == (300, 200)


def test_rejects_non_image_bytes_regardless_of_name():
    with pytest.raises(ip.InvalidImageError):
        ip.process_image(b"<html>not an image</html>")


def test_rejects_empty():
    with pytest.raises(ip.InvalidImageError):
        ip.process_image(b"")


def test_rejects_other_image_formats():
    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, format="GIF")
    with pytest.raises(ip.InvalidImageError):
        ip.process_image(buf.getvalue())


def test_rejects_truncated_image():
    data = make_image_bytes("PNG", size=(400, 400))
    with pytest.raises(ip.InvalidImageError):
        ip.process_image(data[: len(data) // 2])


def test_rejects_oversized_upload():
    with pytest.raises(ip.ImageTooLargeError):
        ip.process_image(b"0" * (ip.MAX_UPLOAD_BYTES + 1))


def test_rejects_too_many_pixels_before_decoding(monkeypatch):
    monkeypatch.setattr(ip, "MAX_PIXELS", 1000)
    with pytest.raises(ip.ImageTooLargeError):
        ip.process_image(make_image_bytes("PNG", size=(100, 100)))


def test_downsizes_long_edges_and_never_upscales():
    big = ip.process_image(make_image_bytes("JPEG", size=(3000, 1500)))
    assert (big.width, big.height) == (2400, 1200)
    assert max(Image.open(io.BytesIO(big.preview)).size) == 800
    small = ip.process_image(make_image_bytes("JPEG", size=(300, 200)))
    assert Image.open(io.BytesIO(small.preview)).size == (300, 200)


def test_strips_exif_and_gps():
    data = make_image_bytes("JPEG", exif=True)
    assert Image.open(io.BytesIO(data)).getexif()  # the input really has EXIF
    out = ip.process_image(data)
    for blob in (out.full, out.preview):
        img = Image.open(io.BytesIO(blob))
        assert not img.getexif()
        assert b"SecretCameraMaker" not in blob


def test_keeps_alpha():
    out = ip.process_image(make_image_bytes("PNG", mode="RGBA"))
    assert Image.open(io.BytesIO(out.full)).mode == "RGBA"
