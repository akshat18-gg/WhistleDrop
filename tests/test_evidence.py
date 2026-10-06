import io
import re
from pathlib import Path

import pytest
from PIL import ExifTags, Image
from PIL.PngImagePlugin import PngInfo
from sqlalchemy import inspect, select

from app import evidence
from app.config import get_settings
from app.models import EvidenceFile, Report, Status, utcnow

UPLOAD = "/api/reports/evidence"
RAW = {"Content-Type": "application/octet-stream"}
PDF = b"%PDF-1.4\n1 0 obj << /Author (Priya Sharma) /Producer (Word) >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"


def jpeg_with_metadata():
    exif = Image.Exif()
    exif[ExifTags.Base.Make] = "Canon"
    exif[ExifTags.Base.Model] = "EOS 5D Mark IV"
    exif[ExifTags.Base.Orientation] = 6  # "rotate 90 degrees to display"
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    gps[ExifTags.GPS.GPSLatitudeRef] = "N"
    gps[ExifTags.GPS.GPSLatitude] = (12.0, 49.0, 23.0)
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), "orange").save(buffer, "JPEG", exif=exif, comment=b"Priya's phone")
    return buffer.getvalue()


def png_with_metadata():
    info = PngInfo()
    info.add_text("Author", "Priya Sharma")
    info.add_text("Comment", "taken from my desk")
    buffer = io.BytesIO()
    Image.new("RGBA", (30, 20), (0, 128, 255, 200)).save(buffer, "PNG", pnginfo=info)
    return buffer.getvalue()


@pytest.fixture
def case(submit, session):
    """A submitted report: (case code headers, report id)."""
    code = submit().json()["case_code"]
    return {"X-Case-Code": code, **RAW}, session.scalar(select(Report.id))


def upload(client, headers, data):
    return client.post(UPLOAD, headers=headers, content=data)


def download_only_file(client, auth, report_id):
    detail = client.get(f"/api/moderator/reports/{report_id}", headers=auth).json()
    file_id = detail["evidence_files"][-1]["id"]
    return client.get(f"/api/moderator/reports/{report_id}/evidence/{file_id}", headers=auth)


def test_jpeg_upload_strips_exif_gps_and_comments(client, auth, case):
    headers, report_id = case
    response = upload(client, headers, jpeg_with_metadata())
    assert response.status_code == 201
    body = response.json()
    assert body["content_type"] == "image/jpeg"
    assert body["files_attached"] == 1
    assert "GPS location" in body["note"]

    file = download_only_file(client, auth, report_id)
    assert file.status_code == 200
    assert file.headers["content-type"] == "image/jpeg"
    for leak in [b"Exif", b"Canon", b"EOS 5D", b"Priya"]:
        assert leak not in file.content
    image = Image.open(io.BytesIO(file.content))
    assert len(image.getexif()) == 0
    assert "comment" not in image.info
    # the orientation tag was applied before it was dropped, so the photo still displays upright
    assert image.size == (48, 64)


def test_png_upload_strips_text_chunks(client, auth, case):
    headers, report_id = case
    assert upload(client, headers, png_with_metadata()).status_code == 201
    file = download_only_file(client, auth, report_id)
    assert b"Priya" not in file.content
    assert b"tEXt" not in file.content
    image = Image.open(io.BytesIO(file.content))
    assert image.format == "PNG"
    assert image.getpixel((0, 0)) == (0, 128, 255, 200)


def test_pdf_is_accepted_with_a_warning(client, auth, case):
    headers, report_id = case
    response = upload(client, headers, PDF)
    assert response.status_code == 201
    assert "not removed" in response.json()["note"]
    file = download_only_file(client, auth, report_id)
    assert file.content == PDF
    assert file.headers["content-type"] == "application/pdf"


@pytest.mark.parametrize(
    "data",
    [b"just some text", b"MZ\x90\x00 windows exe", b"<html><script>alert(1)</script>", b"GIF89a....", b"\x00" * 100],
    ids=["text", "exe", "html", "gif", "zeros"],
)
def test_type_comes_from_magic_bytes_not_name(client, case, data):
    headers, _ = case
    response = client.post(UPLOAD, headers={**headers, "Content-Type": "image/jpeg"}, content=data)
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"


@pytest.mark.parametrize("data", [b"\xff\xd8\xff" + b"not really a jpeg", b"\x89PNG\r\n\x1a\n" + b"broken"])
def test_damaged_images_are_rejected(client, case, data):
    headers, _ = case
    response = upload(client, headers, data)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UNREADABLE_IMAGE"


def test_jpeg_disguised_as_png_is_rejected(client, case):
    headers, _ = case
    disguised = b"\x89PNG\r\n\x1a\n" + jpeg_with_metadata()
    assert upload(client, headers, disguised).status_code == 422


def test_pixel_bomb_is_rejected_before_decoding(client, case):
    headers, _ = case
    buffer = io.BytesIO()
    Image.new("1", (8000, 8000)).save(buffer, "PNG")  # tiny file, 64 million pixels
    assert len(buffer.getvalue()) < 100_000
    response = upload(client, headers, buffer.getvalue())
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "IMAGE_TOO_LARGE"


def test_empty_upload_is_400(client, case):
    headers, _ = case
    assert upload(client, headers, b"").json()["error"]["code"] == "MISSING_FILE"


def test_upload_limit_is_5_mb_not_32_kb(client, case):
    headers, _ = case
    assert upload(client, headers, PDF + b"%" * 200_000).status_code == 201
    response = upload(client, headers, PDF + b"%" * evidence.MAX_BYTES)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "BODY_TOO_LARGE"


def test_upload_needs_a_valid_case_code(client, submit):
    submit()
    assert upload(client, RAW, PDF).status_code == 400
    assert upload(client, {"X-Case-Code": "nope", **RAW}, PDF).status_code == 400
    assert upload(client, {"X-Case-Code": "WD-0000-0000-0000-0000", **RAW}, PDF).status_code == 404


def test_no_uploads_on_a_closed_case(client, case, session):
    headers, report_id = case
    report = session.get(Report, report_id)
    report.status = Status.RESOLVED
    report.closed_at = utcnow()
    session.commit()
    response = upload(client, headers, PDF)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CASE_CLOSED"


def test_at_most_five_files_per_report(client, case):
    headers, _ = case
    for count in range(1, 6):
        assert upload(client, headers, PDF).json()["files_attached"] == count
    response = upload(client, headers, PDF)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "TOO_MANY_FILES"


def test_files_on_disk_are_encrypted_with_random_names_and_no_time(client, case):
    headers, _ = case
    upload(client, headers, jpeg_with_metadata())
    [path] = list(Path(get_settings().evidence_dir).iterdir())
    assert re.fullmatch(r"[0-9a-f]{32}", path.name)
    stored = path.read_bytes()
    assert stored.startswith(b"gAAAAAAAAAAA")  # Fernet token with a zero timestamp
    assert b"\xff\xd8\xff" not in stored and b"JFIF" not in stored
    assert path.stat().st_mtime == 0
    assert path.stat().st_mode & 0o777 == 0o600


def test_evidence_table_has_dates_not_times_and_no_identity(engine):
    columns = {c["name"]: str(c["type"]) for c in inspect(engine).get_columns("evidence_files")}
    assert columns == {
        "id": "CHAR(32)",
        "report_id": "CHAR(32)",
        "content_type": "VARCHAR(32)",
        "size_bytes": "INTEGER",
        "uploaded_on": "DATE",
    }


def test_moderator_sees_files_in_report_detail(client, auth, case):
    headers, report_id = case
    upload(client, headers, PDF)
    files = client.get(f"/api/moderator/reports/{report_id}", headers=auth).json()["evidence_files"]
    assert len(files) == 1
    assert files[0]["content_type"] == "application/pdf"
    assert files[0]["size_bytes"] == len(PDF)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", files[0]["uploaded_on"])


def test_download_is_an_attachment(client, auth, case):
    headers, report_id = case
    upload(client, headers, PDF)
    file = download_only_file(client, auth, report_id)
    assert file.headers["content-disposition"].startswith('attachment; filename="evidence-')
    assert file.headers["x-content-type-options"] == "nosniff"
    assert file.headers["cache-control"] == "no-store"


def test_download_checks_the_file_belongs_to_the_report(client, auth, case, make_report):
    headers, report_id = case
    upload(client, headers, PDF)
    file_id = client.get(f"/api/moderator/reports/{report_id}", headers=auth).json()["evidence_files"][0]["id"]
    other = make_report()
    response = client.get(f"/api/moderator/reports/{other.id}/evidence/{file_id}", headers=auth)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "EVIDENCE_NOT_FOUND"


def test_download_of_a_file_missing_from_disk_is_404(client, auth, case, session):
    headers, report_id = case
    upload(client, headers, PDF)
    item = session.scalar(select(EvidenceFile))
    evidence.delete(item.id)
    response = client.get(f"/api/moderator/reports/{report_id}/evidence/{item.id}", headers=auth)
    assert response.status_code == 404
