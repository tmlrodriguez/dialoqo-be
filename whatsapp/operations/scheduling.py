from django.db import transaction

# Define your task scheduling operations here.

def schedule_media_attachment_storage(attachment):
    """
        DOCSTRING: Schedule Media Attachment Storage

        Description:
        - Queue asynchronous private storage of a WhatsApp media attachment after the current database transaction commits.

        Notes:
        - Celery must never receive attachment identifiers from transactions that later roll back.
        - The task import is intentionally deferred until after transaction commit to prevent circular imports between operations and Celery tasks.
        - The task itself remains idempotent if the attachment has already been stored.
    """

    attachment_id = attachment.id

    def enqueue():
        """
            DOCSTRING: Enqueue

            Description:
            - Import and enqueue the media-storage Celery task after the database transaction commits.

            Notes:
            - Deferred import prevents circular dependencies between workflows,
              scheduling helpers, and Celery task entry points.
        """
        from whatsapp.tasks import store_whatsapp_media_attachment_task

        store_whatsapp_media_attachment_task.delay(attachment_id)

    transaction.on_commit(enqueue)
