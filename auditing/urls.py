from django.urls import path

from .views import AuditEventView

app_name = "auditing"

urlpatterns = [
    path("companies/<int:company_id>/events/", AuditEventView.as_view(), name="event-list"),
    path("companies/<int:company_id>/events/<int:event_id>/", AuditEventView.as_view(), name="event-detail"),
]
