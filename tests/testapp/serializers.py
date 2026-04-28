from __future__ import annotations

from rest_framework import serializers

from tests.testapp.models import Invoice


class InvoiceInputSerializer(serializers.Serializer):
    number = serializers.CharField(max_length=32)
    amount_cents = serializers.IntegerField(min_value=0)


class InvoiceOutputSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invoice
        fields = ["id", "number", "amount_cents", "sent"]
