from rest_framework import serializers

from .models import Document


class DocumentSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model = Document
        fields = ("slug", "title", "url", "updated_at")

    def get_url(self, obj) -> str:
        return self.context["request"].build_absolute_uri(obj.file.url)
