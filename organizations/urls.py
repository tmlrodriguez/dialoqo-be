from django.urls import path

from .views import BranchView, CompanyView, MyCompanyListView, UserCompanyAccessView

app_name = "organizations"

urlpatterns = [
    path("companies/", CompanyView.as_view(), name="company-list-create"),
    path("companies/<int:company_id>/", CompanyView.as_view(), name="company-detail"),

    path("companies/<int:company_id>/branches/", BranchView.as_view(), name="branch-list-create"),
    path("companies/<int:company_id>/branches/<int:branch_id>/", BranchView.as_view(), name="branch-detail"),

    path("company-access/", UserCompanyAccessView.as_view(), name="company-access-list-create"),
    path("company-access/<int:access_id>/", UserCompanyAccessView.as_view(), name="company-access-detail"),

    path("my-companies/", MyCompanyListView.as_view(), name="my-company-list"),
]
