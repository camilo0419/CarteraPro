import os

from django.conf import settings
from django.core.exceptions import ValidationError


SAFE_COMPROBANTE_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
SAFE_COMPROBANTE_CONTENT_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
}
DEFAULT_MAX_COMPROBANTE_SIZE = 10 * 1024 * 1024


def max_comprobante_size():
    return int(getattr(settings, "COMPROBANTE_MAX_UPLOAD_SIZE", DEFAULT_MAX_COMPROBANTE_SIZE))


def validate_comprobante_file(uploaded_file):
    if not uploaded_file:
        return

    filename = os.path.basename(getattr(uploaded_file, "name", "") or "")
    extension = os.path.splitext(filename)[1].lower()
    if extension not in SAFE_COMPROBANTE_EXTENSIONS:
        allowed = ", ".join(sorted(ext.lstrip(".").upper() for ext in SAFE_COMPROBANTE_EXTENSIONS))
        raise ValidationError(f"Formato de comprobante no permitido. Usa uno de estos formatos: {allowed}.")

    if "\x00" in filename or len(filename) > 180:
        raise ValidationError("El nombre del comprobante no es válido o es demasiado largo.")

    try:
        size = getattr(uploaded_file, "size", None)
    except Exception:
        size = None
    max_size = max_comprobante_size()
    if size and size > max_size:
        mb = max_size // (1024 * 1024)
        raise ValidationError(f"El comprobante no puede superar {mb} MB.")

    content_type = (getattr(uploaded_file, "content_type", "") or "").split(";", 1)[0].strip().lower()
    if content_type and content_type not in SAFE_COMPROBANTE_CONTENT_TYPES:
        raise ValidationError("El tipo de archivo del comprobante no coincide con un PDF o imagen segura.")
