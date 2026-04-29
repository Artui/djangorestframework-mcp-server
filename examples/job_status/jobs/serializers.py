from __future__ import annotations

from rest_framework import serializers

from jobs.models import Job


class StartJobInputSerializer(serializers.Serializer):
    duration_seconds = serializers.IntegerField(min_value=1, max_value=60)


class JobOutputSerializer(serializers.ModelSerializer):
    class Meta:
        model = Job
        fields = ["id", "status", "duration_seconds", "error", "created_at", "finished_at"]
