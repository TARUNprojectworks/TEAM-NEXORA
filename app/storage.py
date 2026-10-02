"""Check and save the files a student uploads to the AI Card Maker.

STORAGE_BACKEND=local saves under instance/uploads/ (on a laptop).
STORAGE_BACKEND=gcs saves to the private NOTES_BUCKET and returns a gs:// URI,
which Gemini reads directly. Objects are never made public. The bucket deletes
them after 7 days (lifecycle rule in deploy/setup_vm.sh); cards made from them stay.

Photos and PDFs go to Gemini as files. A .txt file is read here and added to
the notes text, so it is never stored.
"""

import os
import re
import uuid

from flask import current_app

PHOTO_LIMIT = 5
PHOTO_MAX_BYTES = 5 * 1024 * 1024
PDF_LIMIT = 1
PDF_MAX_BYTES = 10 * 1024 * 1024
PDF_MAX_PAGES = 10
TEXT_LIMIT = 1
TEXT_MAX_BYTES = 200 * 1024

EXTENSIONS = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
    "image/heic": ".heic", "image/heif": ".heif", "application/pdf": ".pdf",
}
HEIC_BRANDS = {b"heic", b"heix", b"hevc", b"hevx"}
HEIF_BRANDS = {b"mif1", b"msf1", b"heif"}


class UploadError(Exception):
    """A file we can't use. The message is shown to the student."""


def detect_type(filename, data):
    """The real file type from its first bytes (not the name, which anyone can change)."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp" and data[8:12] in HEIC_BRANDS:
        return "image/heic"
    if data[4:8] == b"ftyp" and data[8:12] in HEIF_BRANDS:
        return "image/heif"
    if data[:5] == b"%PDF-":
        return "application/pdf"
    if filename.lower().endswith(".txt"):
        return "text/plain"
    return None


def count_pdf_pages(data):
    """Best-effort page count without a PDF library.

    Most PDFs list each page as "/Type /Page". Some compress that list, so we
    also look at the page tree's "/Count". Returns 0 if we can't tell; Gemini
    still reads the file, we just can't enforce the 10-page limit for it.
    """
    pages = len(re.findall(rb"/Type\s*/Page(?![a-zA-Z])", data))
    if pages:
        return pages
    counts = [int(n) for n in re.findall(rb"/Type\s*/Pages\b[^>]*?/Count\s+(\d+)", data)]
    return max(counts, default=0)


def check_uploads(uploads):
    """Sort uploads into photos, PDFs and text, and check every limit.

    uploads: list of (filename, bytes). Returns a list of (mime_type, bytes).
    """
    checked = []
    for filename, data in uploads:
        if not data:
            continue
        mime_type = detect_type(filename, data)
        if mime_type is None:
            raise UploadError(f"{filename} isn't a photo (jpg, png, webp, heic), PDF or .txt file.")
        checked.append((filename, mime_type, data))

    photos = [c for c in checked if c[1].startswith("image/")]
    pdfs = [c for c in checked if c[1] == "application/pdf"]
    texts = [c for c in checked if c[1] == "text/plain"]
    if len(photos) > PHOTO_LIMIT:
        raise UploadError(f"Upload up to {PHOTO_LIMIT} photos at a time.")
    if len(pdfs) > PDF_LIMIT or len(texts) > TEXT_LIMIT:
        raise UploadError("Upload one PDF and one text file at a time.")
    for filename, _, data in photos:
        if len(data) > PHOTO_MAX_BYTES:
            raise UploadError(f"{filename} is bigger than 5 MB. Take a smaller photo or crop it.")
    for filename, _, data in pdfs:
        if len(data) > PDF_MAX_BYTES:
            raise UploadError(f"{filename} is bigger than 10 MB.")
        if count_pdf_pages(data) > PDF_MAX_PAGES:
            raise UploadError(f"{filename} has more than {PDF_MAX_PAGES} pages. Upload just the pages you need.")
    for filename, _, data in texts:
        if len(data) > TEXT_MAX_BYTES:
            raise UploadError(f"{filename} is too long. Paste a shorter piece of notes.")
    return [(mime_type, data) for _, mime_type, data in checked]


def read_text_file(data):
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise UploadError("That .txt file isn't plain text we can read. Save it as UTF-8 and try again.")


# ---------- Saving ----------

def save_note_file(data, mime_type):
    """Save a photo or PDF. Returns where it is: a local path or a gs:// URI."""
    name = f"{uuid.uuid4().hex}{EXTENSIONS[mime_type]}"
    if current_app.config["STORAGE_BACKEND"] == "gcs":
        return save_to_bucket(name, data, mime_type)
    folder = current_app.config["UPLOAD_FOLDER"]
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    with open(path, "wb") as file:
        file.write(data)
    return path


def save_to_bucket(name, data, mime_type):
    # Imported here so the app runs on a laptop without Google Cloud set up.
    from google.cloud import storage

    bucket_name = current_app.config["NOTES_BUCKET"]
    client = storage.Client(project=current_app.config["GCP_PROJECT"] or None)
    blob = client.bucket(bucket_name).blob(f"notes/{name}")
    blob.upload_from_string(data, content_type=mime_type)
    return f"gs://{bucket_name}/notes/{name}"


def file_for_gemini(reference, data, mime_type):
    """What make_cards() gets for one file: bytes on a laptop, a gs:// URI on GCP."""
    if reference.startswith("gs://"):
        return {"mime_type": mime_type, "uri": reference}
    return {"mime_type": mime_type, "data": data}
