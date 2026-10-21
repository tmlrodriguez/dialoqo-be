from rest_framework import serializers


# Define your Embedded Signup serializers here.


class WhatsAppEmbeddedSignupRequestSerializer(serializers.Serializer):
    """
        DOCSTRING: WhatsApp Embedded Signup Request Serializer

        Description:
        - Validate the authorization information received
          from Meta WhatsApp Embedded Signup.
        - Normalize the identifiers required by Django
          to complete the onboarding process.

        Notes:
        - Company ownership is determined by the API URL.
        - Company authorization is enforced by the API view.
        - The authorization code is exchanged exclusively
          by the Django backend.
        - Business Portfolio ID is optional because Django
          resolves and verifies ownership through Meta.
        - Access tokens and App Secrets are never accepted
          from the frontend.
    """

    code = serializers.CharField(
        required=True,
        allow_blank=False,
        trim_whitespace=True,
        write_only=True,
        max_length=4096,
    )

    waba_id = serializers.CharField(
        required=True,
        allow_blank=False,
        trim_whitespace=True,
        max_length=100,
    )

    phone_number_id = serializers.CharField(
        required=True,
        allow_blank=False,
        trim_whitespace=True,
        max_length=100,
    )

    business_id = serializers.CharField(
        required=False,
        allow_blank=False,
        allow_null=True,
        trim_whitespace=True,
        max_length=100,
    )

    def validate(self, attrs):
        """
            DOCSTRING: Validate Embedded Signup Request

            Description:
            - Normalize optional onboarding identifiers.

            Notes:
            - Meta resource ownership is verified by the
              backend operation, not by this serializer.
        """

        if attrs.get("business_id") is None:
            attrs.pop("business_id", None)

        return attrs


class WhatsAppEmbeddedSignupRegisteredNumberSerializer(
    serializers.Serializer
):
    """
        DOCSTRING: Embedded Signup Registered Number Serializer

        Description:
        - Represent a WhatsApp number registered in Dialoqo
          during Embedded Signup.

        Notes:
        - The number belongs to the authorized company.
        - Newly created numbers belong to the Principal branch.
        - Existing branch assignments remain unchanged.
    """

    id = serializers.IntegerField(
        read_only=True,
    )

    meta_phone_number_id = serializers.CharField(
        read_only=True,
    )

    branch_id = serializers.IntegerField(
        read_only=True,
    )

    created = serializers.BooleanField(
        read_only=True,
    )


class WhatsAppEmbeddedSignupPhoneSerializer(
    serializers.Serializer
):
    """
        DOCSTRING: Embedded Signup Phone Serializer

        Description:
        - Represent phone-number information discovered
          from Meta during onboarding.
    """

    meta_phone_number_id = serializers.CharField(
        read_only=True,
    )

    display_phone_number = serializers.CharField(
        read_only=True,
    )

    verified_name = serializers.CharField(
        read_only=True,
        allow_blank=True,
    )

    code_verification_status = serializers.CharField(
        read_only=True,
        allow_blank=True,
    )

    is_selected = serializers.BooleanField(
        read_only=True,
        required=False,
    )


class WhatsAppEmbeddedSignupResultSerializer(
    serializers.Serializer
):
    """
        DOCSTRING: WhatsApp Embedded Signup Result Serializer

        Description:
        - Represent the result of a completed Meta
          WhatsApp Embedded Signup operation.
        - Expose WABA connection information.
        - Expose registered WhatsApp numbers.
        - Expose credential validation metadata.

        Notes:
        - Access tokens are never serialized.
        - Encrypted credentials are never serialized.
        - The Meta App Secret is never serialized.
        - Credential expiration is informational.
        - Monitoring is activated independently.
        - Company authorization remains backend-authoritative.
    """

    account_id = serializers.IntegerField(
        read_only=True,
    )

    meta_waba_id = serializers.CharField(
        read_only=True,
    )

    meta_business_id = serializers.CharField(
        read_only=True,
    )

    display_name = serializers.CharField(
        read_only=True,
    )

    created = serializers.BooleanField(
        read_only=True,
    )

    is_connected = serializers.BooleanField(
        read_only=True,
    )

    is_webhook_configured = serializers.BooleanField(
        read_only=True,
    )

    credential_validated = serializers.BooleanField(
        read_only=True,
    )

    credential_expires_at = serializers.DateTimeField(
        read_only=True,
        allow_null=True,
        required=False,
    )

    selected_phone = WhatsAppEmbeddedSignupPhoneSerializer(
        read_only=True,
    )

    phone_numbers = WhatsAppEmbeddedSignupPhoneSerializer(
        many=True,
        read_only=True,
    )

    registered_numbers = (
        WhatsAppEmbeddedSignupRegisteredNumberSerializer(
            many=True,
            read_only=True,
        )
    )

    created_numbers = serializers.IntegerField(
        read_only=True,
    )

    reused_numbers = serializers.IntegerField(
        read_only=True,
    )