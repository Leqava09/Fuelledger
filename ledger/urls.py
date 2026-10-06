from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path

from . import views

urlpatterns = [
	path("login/", LoginView.as_view(template_name="registration/login.html"), name="login"),
	path("logout/", LogoutView.as_view(), name="logout"),
	path("", views.home, name="home"),
	path("hq/", views.hq_dashboard, name="hq-dashboard"),
	path("sites/<slug:slug>/", views.site_dashboard, name="site-dashboard"),
	path("sites/<slug:slug>/forms/daily-fuel-readings/entry/", views.daily_fuel_reading_entry, name="daily-fuel-reading-entry"),
	path("sites/<slug:slug>/forms/<slug:form_key>/", views.blank_site_form, name="blank-site-form"),
	path("sites/<slug:slug>/users/", views.site_users, name="site-users"),
	path("sites/<slug:slug>/users/new/", views.site_user_create, name="site-user-create"),
	path("sites/<slug:slug>/groups/", views.site_groups, name="site-groups"),
	path("sites/<slug:slug>/history/", views.site_history, name="site-history"),
	path("sites/<slug:slug>/entries/", views.entry_list, name="entry-list"),
	path("sites/<slug:slug>/entries/<int:entry_id>/", views.entry_detail, name="entry-detail"),
]