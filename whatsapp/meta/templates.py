"""Meta message template catalog requests."""

from whatsapp.meta.client import MetaWhatsAppAPIError, meta_delete, meta_get, meta_post


def list_meta_whatsapp_message_templates(
    whatsapp_business_account,
):
    """
        DOCSTRING: List Meta WhatsApp Message Templates

        Description:
        - Retrieve all message templates belonging to a WABA.
        - Follow Meta pagination until all templates are retrieved.
        - Resolve authorization through the owning WABA.

        Notes:
        - Template normalization and persistence belong to
          the synchronization operation.
        - Pagination cursors are used instead of requesting
          arbitrary external URLs.
    """

    templates = []
    after = None

    while True:
        params = {
            "limit": 100,
        }

        if after:
            params["after"] = after

        response_data = meta_get(
            resource_path=(
                f"{whatsapp_business_account.meta_waba_id}"
                "/message_templates"
            ),
            params=params,
            whatsapp_business_account=whatsapp_business_account,
        )

        templates.extend(
            response_data.get("data") or []
        )

        paging = response_data.get("paging") or {}
        cursors = paging.get("cursors") or {}

        next_url = paging.get("next")
        after = cursors.get("after")

        if not next_url or not after:
            break

    return templates


def create_meta_whatsapp_message_template(
    whatsapp_business_account,
    name,
    language,
    category,
    parameter_format,
    components,
):
    """
        DOCSTRING: Create Meta WhatsApp Message Template

        Description:
        - Submit a new WhatsApp message template to Meta.
        - Resolve authorization through the owning WABA.

        Notes:
        - Meta performs final template validation and approval.
        - Newly created templates may remain pending.
    """

    payload = {
        "name": name,
        "language": language,
        "category": category,
        "components": components,
    }

    if parameter_format:
        payload["parameter_format"] = parameter_format

    return meta_post(
        resource_path=(
            f"{whatsapp_business_account.meta_waba_id}"
            "/message_templates"
        ),
        payload=payload,
        whatsapp_business_account=whatsapp_business_account,
    )


def update_meta_whatsapp_message_template(
    whatsapp_message_template,
    category=None,
    components=None,
):
    """
        DOCSTRING: Update Meta WhatsApp Message Template

        Description:
        - Submit supported message-template modifications to Meta.
        - Resolve authorization through the owning WABA.

        Notes:
        - Meta determines whether a template permits modification.
        - Local state must not assume success until Meta accepts it.
    """

    payload = {}

    if category is not None:
        payload["category"] = category

    if components is not None:
        payload["components"] = components

    if not payload:
        raise MetaWhatsAppAPIError(
            error_message=(
                "Actualización de plantilla rechazada: "
                "no se proporcionaron cambios."
            )
        )

    return meta_post(
        resource_path=(
            whatsapp_message_template.meta_template_id
        ),
        payload=payload,
        whatsapp_business_account=(
            whatsapp_message_template.whatsapp_business_account
        ),
    )


def delete_meta_whatsapp_message_template(
    whatsapp_message_template,
):
    """
        DOCSTRING: Delete Meta WhatsApp Message Template

        Description:
        - Request deletion of a WhatsApp message template from Meta.
        - Resolve authorization through the owning WABA.

        Notes:
        - Deletion is addressed through the owning WABA
          and template name.
        - Local historical records remain preserved.
    """

    whatsapp_business_account = (
        whatsapp_message_template.whatsapp_business_account
    )

    return meta_delete(
        resource_path=(
            f"{whatsapp_business_account.meta_waba_id}"
            "/message_templates"
        ),
        params={
            "name": whatsapp_message_template.name,
        },
        whatsapp_business_account=whatsapp_business_account,
    )
