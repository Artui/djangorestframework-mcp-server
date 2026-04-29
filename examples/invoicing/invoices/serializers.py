from __future__ import annotations

from rest_framework import serializers

from invoices.models import Invoice


class InvoiceInputSerializer(serializers.Serializer):
    """Input shape for ``invoices.create``."""

    number = serializers.CharField(max_length=32)
    amount_cents = serializers.IntegerField(min_value=0)


class MarkSentInputSerializer(serializers.Serializer):
    """Input shape for ``invoices.mark_sent``."""

    pk = serializers.IntegerField(min_value=1)


class InvoiceOutputSerializer(serializers.ModelSerializer):
    """Output shape for every read surface in this example."""

    class Meta:
        model = Invoice
        fields = ["id", "number", "amount_cents", "sent", "created_at"]
