import re
from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.decorators import api_view, throttle_classes
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle

from .planner import NoRouteError, plan_trip
from .providers import ProviderError, get_providers

COORDS = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")
MAX_DEPARTURE_AHEAD = timedelta(days=7)  # forecast quality drops fast beyond a week


class PlaceField(serializers.Field):
    """A place as {label, lat, lon} (from autocomplete) or free text ("Dallas, TX" / "32.7,-96.8")."""

    def to_internal_value(self, data):
        if isinstance(data, dict):
            try:
                lat, lon = float(data["lat"]), float(data["lon"])
            except (KeyError, TypeError, ValueError):
                raise serializers.ValidationError("Expected lat and lon numbers.") from None
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise serializers.ValidationError("Coordinates out of range.")
            return {"label": str(data.get("label") or f"{lat:.4f}, {lon:.4f}")[:200], "lat": lat, "lon": lon}
        if not isinstance(data, str) or not data.strip():
            raise serializers.ValidationError("Enter a place.")
        if match := COORDS.match(data):
            return self.to_internal_value({"lat": match[1], "lon": match[2]})
        try:
            places = get_providers()[2].search(data.strip()[:200], limit=1)
        except ProviderError:
            raise serializers.ValidationError("Geocoding is unavailable right now.") from None
        if not places:
            raise serializers.ValidationError(f'Could not find "{data.strip()}".')
        p = places[0]
        return {"label": p.label, "lat": p.lat, "lon": p.lon}


class PlanSerializer(serializers.Serializer):
    origin = PlaceField()
    destination = PlaceField()
    departure = serializers.DateTimeField()
    load_lb = serializers.FloatField(min_value=0, max_value=80_000)
    interval_mi = serializers.ChoiceField(choices=[10, 25, 50], default=25)
    hos = serializers.BooleanField(default=True)  # plan FMCSA hours-of-service breaks into ETAs

    def validate_departure(self, value):
        now = timezone.now()
        if value < now - timedelta(hours=1):
            raise serializers.ValidationError("Departure can't be in the past.")
        if value > now + MAX_DEPARTURE_AHEAD:
            raise serializers.ValidationError("Departure must be within the next 7 days of forecast.")
        return value

    def validate(self, attrs):
        o, d = attrs["origin"], attrs["destination"]
        if abs(o["lat"] - d["lat"]) < 1e-3 and abs(o["lon"] - d["lon"]) < 1e-3:
            raise serializers.ValidationError({"destination": "Destination must differ from origin."})
        return attrs


class PlanThrottle(AnonRateThrottle):
    scope = "plan"


@api_view(["POST"])
@throttle_classes([PlanThrottle])
def plan(request):
    s = PlanSerializer(data=request.data)
    s.is_valid(raise_exception=True)
    v = s.validated_data
    try:
        result = plan_trip(v["origin"], v["destination"], v["departure"], v["load_lb"], int(v["interval_mi"]), v["hos"])
    except NoRouteError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
    except ProviderError as exc:
        return Response({"detail": f"Upstream data provider failed: {exc}"}, status=status.HTTP_502_BAD_GATEWAY)
    return Response(result)


@api_view(["GET"])
def geocode(request):
    q = request.query_params.get("q", "").strip()
    if len(q) < 2:
        return Response([])
    try:
        places = get_providers()[2].search(q[:200])
    except ProviderError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
    return Response([{"label": p.label, "lat": p.lat, "lon": p.lon} for p in places])


@api_view(["GET"])
def health(request):
    return Response({"status": "ok"})
