"""Save and read note photos: a local folder or a private Cloud Storage bucket.

Owner: AI. Built in phase 5. STORAGE_BACKEND=local saves under
instance/uploads/; STORAGE_BACKEND=gcs saves to NOTES_BUCKET and returns a
gs:// URI. Objects are never public. Photos are deleted after 7 days
(bucket lifecycle rule); cards made from them stay.

Planned public functions:
    save_note_photo(file_bytes, content_type) -> reference (file path or gs:// URI)
    read_note_photo(reference)               -> bytes

Accepted: jpg or png, max 5 MB.
"""

ALLOWED_CONTENT_TYPES = ("image/jpeg", "image/png")
MAX_PHOTO_BYTES = 5 * 1024 * 1024
