from django.urls import path

from .views import AdministratorView, CurrentUserView, LoginView, LogoutView, MemberUserView, MonitorView

app_name = "access"

urlpatterns = [
    path("login/", LoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("me/", CurrentUserView.as_view(), name="me"),

    path("administrators/", AdministratorView.as_view(), name="administrator-list-create"),
    path("administrators/<int:user_id>/", AdministratorView.as_view(), name="administrator-detail"),

    path("monitors/", MonitorView.as_view(), name="monitor-list-create"),
    path("monitors/<int:user_id>/", MonitorView.as_view(), name="monitor-detail"),

    path("members/", MemberUserView.as_view(), name="member-user-list-create"),
    path("members/<int:user_id>/", MemberUserView.as_view(), name="member-user-detail"),
]
