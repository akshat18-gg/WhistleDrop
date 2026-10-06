import io
import os
import uuid
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import get_settings
from app.errors import ApiError
from app.models import decrypt, encrypt

MAX_BYTES = 5 * 1024 * 1024
MAX_FILES_PER_REPORT = 5
# A small PNG of one flat colour can unpack into billions of pixels. Anything
# bigger than a large phone photo is refused before Pillow decodes it.
MAX_PIXELS = 50_000_000

# The first bytes of the file decide what it is. The name and the Content-Type
# header are whatever the client says, so they aren't trusted.
SIGNATURES = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"%PDF-": "application/pdf",
}
EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "application/pdf": "pdf"}
PILLOW_FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG"}


def detect_type(data: bytes) -> str:
    for signature, content_type in SIGNATURES.items():
        if data.startswith(signature):
            return content_type
    raise ApiError(415, "UNSUPPORTED_FILE_TYPE", "Only JPEG and PNG images and PDF files can be uploaded.")


def without_metadata(data: bytes, content_type: str) -> bytes:
    """Rebuilds an image from its pixels alone. EXIF (GPS location, phone model,
    time taken), comments and PNG text chunks don't survive, because they're
    never copied across in the first place."""
    unreadable = ApiError(422, "UNREADABLE_IMAGE", "That file looks like an image but it couldn't be read.")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != PILLOW_FORMATS[content_type]:
                raise unreadable
            if image.width * image.height > MAX_PIXELS:
                raise ApiError(422, "IMAGE_TOO_LARGE", "That image has too many pixels. Try a smaller copy.")
            # Turn the photo the right way up now, since the orientation tag is about to go.
            upright = ImageOps.exif_transpose(image)
            mode = "RGBA" if content_type == "image/png" else "RGB"
            pixels = upright.convert(mode)
            clean = Image.frombytes(mode, pixels.size, pixels.tobytes())
    except Image.DecompressionBombError:
        raise ApiError(422, "IMAGE_TOO_LARGE", "That image has too many pixels. Try a smaller copy.") from None
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise unreadable from None

    output = io.BytesIO()
    clean.save(output, format=PILLOW_FORMATS[content_type], quality=90)
    return output.getvalue()


def clean_upload(data: bytes) -> tuple[str, bytes]:
    if not data:
        raise ApiError(400, "MISSING_FILE", "Send the file itself as the request body.")
    content_type = detect_type(data)
    if content_type in PILLOW_FORMATS:
        data = without_metadata(data, content_type)
    return content_type, data


def _path(evidence_id: uuid.UUID) -> Path:
    return Path(get_settings().evidence_dir) / evidence_id.hex


def save(evidence_id: uuid.UUID, data: bytes) -> None:
    path = _path(evidence_id)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(encrypt(data))
    path.chmod(0o600)
    # The file's modified time would be the exact upload time, so set it to 1970.
    os.utime(path, (0, 0))


def load(evidence_id: uuid.UUID) -> bytes | None:
    path = _path(evidence_id)
    if not path.exists():
        return None
    return decrypt(path.read_bytes())


def delete(evidence_id: uuid.UUID) -> None:
    _path(evidence_id).unlink(missing_ok=True)
