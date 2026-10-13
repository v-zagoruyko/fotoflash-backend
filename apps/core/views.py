from django.db import connection
from django.http import JsonResponse
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import StudioSettings
from .serializers import StudioSerializer


def healthz(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return JsonResponse({"status": "ok"})


class StudioView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(responses=StudioSerializer, description="Публичные контакты, реквизиты и горизонт бронирования студии для сайта.")
    def get(self, request):
        return Response(StudioSerializer(StudioSettings.load()).data)
