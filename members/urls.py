from django.urls import path

from .views import BranchMemberView, MemberView, PositionView

app_name = "members"

urlpatterns = [
    path("companies/<int:company_id>/positions/", PositionView.as_view(), name="position-list-create"),
    path("companies/<int:company_id>/positions/<int:position_id>/", PositionView.as_view(), name="position-detail"),

    path("companies/<int:company_id>/members/", MemberView.as_view(), name="member-list-create"),
    path("companies/<int:company_id>/members/<int:member_id>/", MemberView.as_view(), name="member-detail"),

    path("companies/<int:company_id>/branches/<int:branch_id>/members/", BranchMemberView.as_view(), name="branch-member-list"),
]
