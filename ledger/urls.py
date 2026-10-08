from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path

from . import views

urlpatterns = [
	path("login/", LoginView.as_view(template_name="registration/login.html"), name="login"),
	path("logout/", LogoutView.as_view(), name="logout"),
	path("", views.home, name="home"),
	path("hq/", views.hq_dashboard, name="hq-dashboard"),
	path("sites/<slug:slug>/", views.site_dashboard, name="site-dashboard"),
	path("sites/<slug:slug>/forms/<slug:form_key>/history/", views.site_form_history, name="site-form-history"),
	path("sites/<slug:slug>/cash-up-reports/", views.cash_up_reports, name="cash-up-reports"),
	path("sites/<slug:slug>/forms/fuel-readings/<slug:reading_type>/", views.fuel_reading_entry, name="fuel-reading-entry"),
	path("sites/<slug:slug>/forms/cash-up/<slug:section>/", views.cash_up_entry, name="cash-up-entry"),
	path("sites/<slug:slug>/forms/daily-fuel-readings/entry/", views.daily_fuel_reading_entry, name="daily-fuel-reading-entry"),
	path("sites/<slug:slug>/forms/<slug:form_key>/", views.blank_site_form, name="blank-site-form"),
	path("sites/<slug:slug>/users/", views.site_users, name="site-users"),
	path("sites/<slug:slug>/users/manage/", views.site_admin_users, name="site-admin-users"),
	path("sites/<slug:slug>/users/manage/<int:user_id>/", views.site_admin_users, name="site-admin-user-edit"),
	path("sites/<slug:slug>/users/manage/add/", views.site_admin_user_add, name="site-admin-user-add"),
	path("sites/<slug:slug>/users/new/", views.site_user_create, name="site-user-create"),
	path("sites/<slug:slug>/users/<int:user_id>/edit/", views.site_user_edit, name="site-user-edit"),
	path("sites/<slug:slug>/groups/", views.site_groups, name="site-groups"),
	path("sites/<slug:slug>/groups/manage/", views.site_admin_groups, name="site-admin-groups"),
	path("sites/<slug:slug>/groups/manage/<int:group_id>/", views.site_admin_groups, name="site-admin-group-edit"),
	path("sites/<slug:slug>/groups/<int:group_id>/edit/", views.site_group_edit, name="site-group-edit"),
	path("sites/<slug:slug>/history/", views.site_history, name="site-history"),
	path("sites/<slug:slug>/entries/", views.entry_list, name="entry-list"),
	path("sites/<slug:slug>/entries/<int:entry_id>/", views.entry_detail, name="entry-detail"),
]