from __future__ import annotations

from rest_framework import serializers
from rest_framework_services import AGENT, AgentField

from tests.testapp.models import Invoice


class InvoiceInputSerializer(serializers.Serializer):
    number = serializers.CharField(max_length=32)
    amount_cents = serializers.IntegerField(min_value=0)


class InvoiceOutputSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invoice
        fields = ["id", "number", "amount_cents", "sent"]


class AgentInvoiceSerializer(serializers.ModelSerializer):
    """The output serializer with agent markings, for audience-projection tests."""

    status = serializers.ChoiceField(
        choices=[("PENDING_REVIEW", "Awaiting review"), ("PAID", "Paid")],
        default="PENDING_REVIEW",
    )

    class Meta:
        model = Invoice
        fields = ["id", "number", "amount_cents", "sent", "status"]
        extra_kwargs = {
            "id": {"style": {AGENT: AgentField.handle("Invoice handle.")}},
            "number": {"style": {AGENT: AgentField.label()}},
            "sent": {"style": {AGENT: AgentField.hidden()}},
        }
