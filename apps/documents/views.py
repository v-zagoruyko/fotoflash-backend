from rest_framework import viewsets
from rest_framework.permissions import AllowAny

from .models import Document
from .serializers import DocumentSerializer


class DocumentViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [AllowAny]
    serializer_class = DocumentSerializer
    queryset = Document.objects.filter(is_published=True)
    lookup_field = "slug"
    pagination_class = None
