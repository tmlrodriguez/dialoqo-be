from django.conf import settings
from django.core.files.storage import FileSystemStorage

# Define your storage providers here.

class PrivateWhatsAppMediaStorage(FileSystemStorage):
    """
        DOCSTRING: Private WhatsApp Media Storage

        Description:
        - Provide private filesystem storage for WhatsApp media binaries.
        - Prevent Django from generating public media URLs for stored WhatsApp content.

        Notes:
        - Files are served only through authenticated Dialoqo views.
        - The storage location must not be mapped directly through the web server.
        - Production may replace this implementation with private S3-compatible object storage without changing the media-domain operations.
    """
    def __init__(self):
        super().__init__(location=settings.DIALOQO_PRIVATE_MEDIA_ROOT, base_url=None)


private_whatsapp_media_storage = PrivateWhatsAppMediaStorage()
