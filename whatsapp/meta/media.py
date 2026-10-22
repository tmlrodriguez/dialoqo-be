"""Meta media metadata requests and binary downloads."""

import hashlib

import requests
from django.conf import settings


class MetaMediaError(Exception):
    """
        DOCSTRING: Meta Media Error

        Description:
        - Represent a controlled failure while resolving, downloading, validating, or storing WhatsApp media.

        Notes:
        - User-facing API responses must not expose Meta access tokens or internal storage paths.
        - The exception message may be persisted as operational media-storage diagnostics.
    """

    pass


def get_meta_media_metadata(meta_media_id, access_token):
    """
        DOCSTRING: Get Meta Media Metadata

        Description:
        - Retrieve temporary media-download metadata from Meta using a WhatsApp media identifier.

        Notes:
        - The returned Meta URL is temporary and must never be persisted as a permanent media URL.
        - Authorization uses the global Meta access token configured by Dialoqo.
        - Non-successful Meta responses are converted into controlled MetaMediaError exceptions.
    """

    graph_api_version = getattr(settings, "DIALOQO_META_GRAPH_API_VERSION", "v24.0")
    request_url = f"https://graph.facebook.com/{graph_api_version}/{meta_media_id}"

    try:
        response = requests.get(request_url, headers={"Authorization": f"Bearer {access_token}"}, timeout=settings.DIALOQO_META_MEDIA_REQUEST_TIMEOUT)
    except requests.RequestException as error:
        raise MetaMediaError("Obtención de medio rechazada: no fue posible consultar los metadatos del archivo en Meta.") from error

    if response.status_code != 200:
        raise MetaMediaError("Obtención de medio rechazada: Meta no permitió obtener los metadatos del archivo.")

    try:
        payload = response.json()
    except ValueError as error:
        raise MetaMediaError("Obtención de medio rechazada: Meta devolvió metadatos inválidos.") from error

    media_url = payload.get("url")

    if not media_url:
        raise MetaMediaError("Obtención de medio rechazada: Meta no proporcionó una URL temporal para el archivo.")

    return {
        "url": media_url,
        "mime_type": payload.get("mime_type") or "",
        "sha256": payload.get("sha256") or "",
        "file_size": payload.get("file_size"),
        "id": payload.get("id") or meta_media_id,
    }


def download_meta_media(media_url, access_token, maximum_size):
    """
        DOCSTRING: Download Meta Media

        Description:
        - Download a WhatsApp media binary from the temporary Meta media URL.
        - Calculate the binary size and SHA-256 checksum while streaming the response.

        Notes:
        - The access token is supplied only through the Authorization header.
        - The download is streamed to avoid loading large media files entirely into memory.
        - Files exceeding the configured maximum size are rejected.
    """

    try:
        response = requests.get(media_url, headers={"Authorization": f"Bearer {access_token}"}, stream=True, timeout=settings.DIALOQO_META_MEDIA_REQUEST_TIMEOUT)
    except requests.RequestException as error:
        raise MetaMediaError("Descarga de medio rechazada: no fue posible descargar el archivo desde Meta.") from error

    if response.status_code != 200:
        response.close()
        raise MetaMediaError("Descarga de medio rechazada: Meta no permitió descargar el archivo.")

    temporary_file = settings.DIALOQO_META_MEDIA_TEMPORARY_FILE_CLASS(max_size=settings.DIALOQO_META_MEDIA_MEMORY_THRESHOLD, mode="w+b")
    checksum = hashlib.sha256()
    size = 0

    try:
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue

            size += len(chunk)

            if size > maximum_size:
                raise MetaMediaError("Descarga de medio rechazada: el archivo excede el tamaño máximo permitido.")

            checksum.update(chunk)
            temporary_file.write(chunk)

    except Exception:
        temporary_file.close()
        response.close()
        raise

    response.close()
    temporary_file.seek(0)

    return temporary_file, size, checksum.hexdigest()
