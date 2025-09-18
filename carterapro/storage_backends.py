# carterapro/storage_backends.py
from storages.backends.s3boto3 import S3Boto3Storage

class MediaStorage(S3Boto3Storage):
    """
    Almacena MEDIA en s3://<bucket>/media/ de forma privada.
    Django generará URLs firmadas temporalmente para acceder a los archivos.
    """
    default_acl = None
    location = "media"
    file_overwrite = False
