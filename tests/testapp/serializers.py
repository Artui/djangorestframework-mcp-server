from __future__ import annotations

from rest_framework import serializers
from rest_framework_services import MARKING, FieldMarking

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
            "id": {"style": {MARKING: FieldMarking.handle("Invoice handle.")}},
            "number": {"style": {MARKING: FieldMarking.label()}},
            "sent": {"style": {MARKING: FieldMarking.hidden()}},
        }


class LedgerSerializer(serializers.ModelSerializer):
    """An output serializer that formats one value for display on the way out.

    A stand-in for a real consumer defect rather than a contrivance: someone
    reaches for ``to_representation`` to render an amount the way a person
    reads it, and the advertised schema, derived from the *declared* field,
    goes on saying ``integer``. No key changes name and none disappears, so
    every key-set assertion in a suite stays green over it.
    """

    class Meta:
        model = Invoice
        fields = ["id", "number", "amount_cents"]

    def to_representation(self, instance: Invoice) -> dict[str, object]:
        row = super().to_representation(instance)
        row["amount_cents"] = f"{row['amount_cents'] / 100:.2f}"
        return row
