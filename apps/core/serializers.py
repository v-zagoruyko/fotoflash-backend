from rest_framework import serializers


class StudioSerializer(serializers.Serializer):
    address = serializers.CharField()
    phone = serializers.CharField(source="contact_phone")
    email = serializers.CharField(source="contact_email")
    map_url = serializers.CharField()
    booking_horizon_days = serializers.IntegerField()
    legal_name = serializers.CharField()
    inn = serializers.CharField()
    ogrnip = serializers.CharField()
    legal_address = serializers.CharField()
