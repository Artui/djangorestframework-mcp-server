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


class SelectableInvoiceSerializer(InvoiceOutputSerializer):
    """``InvoiceOutputSerializer`` narrowed to the fields the caller asked for.

    Reads ``fields`` (``id,number``) off ``request.query_params``, which is
    where ``invoices.list``'s ``fields`` argument arrives: a ``QueryParam``
    routes a tool argument there, so a serializer written for HTTP's
    ``?fields=`` works unchanged. django-restql's ``?query=`` is read the same
    way; this is written by hand only because restql is not a dependency of the
    example.

    Two things are copied from restql's default, because they are what a model
    calling the tool depends on:

    - **It runs per row.** On a paged tool the selection names an *item*'s
      fields. ``fields=items`` selects the page envelope, the shape the tool's
      ``outputSchema`` shows, and no row has an ``items`` field.
    - **An unknown name is refused, not dropped.** Dropping it would hand back
      a page of empty rows, which reads as a real result. A refusal comes back
      as an ``isError`` ``validation_error`` naming the argument, and the model
      can correct itself from that.
    """

    def to_representation(self, instance: Invoice) -> dict[str, object]:
        data = super().to_representation(instance)
        raw = self.context["request"].query_params.get("fields")
        if not raw:
            return data
        wanted = [name.strip() for name in raw.split(",") if name.strip()]
        for name in wanted:
            if name not in data:
                # restql's own message and code, so a consumer switching to it
                # sees the same error.
                raise serializers.ValidationError(f"`{name}` field is not found", code="not_found")
        return {name: data[name] for name in wanted}
