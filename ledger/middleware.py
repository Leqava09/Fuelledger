import re

from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404


class SiteAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.current_site = None
        request.is_hq_context = False
        profile = self._profile(request.user) if request.user.is_authenticated else None
        request.is_hq_user = request.user.is_superuser or bool(profile and profile.role == "HQ")
        path = request.path_info

        if path.startswith("/admin/") and request.user.is_authenticated and not request.user.is_superuser:
            profile = self._profile(request.user)
            if not profile or profile.role != "HQ":
                return HttpResponseForbidden("Django administration is restricted to HQ users.")

        if path.startswith("/hq/") or path == "/hq":
            if not request.user.is_authenticated:
                return self.get_response(request)
            profile = self._profile(request.user)
            if not request.user.is_superuser and (not profile or profile.role != "HQ"):
                return HttpResponseForbidden("HQ access is restricted to HQ users.")
            request.is_hq_context = True

        if path.startswith("/site-admin/"):
            if not request.user.is_authenticated:
                return self.get_response(request)
            from .models import Site

            site_id = request.session.get("site_admin_site_id")
            site = Site.objects.filter(pk=site_id, is_active=True).first()
            profile = self._profile(request.user)
            if not site or (not request.user.is_superuser and not (
                profile and (
                    profile.role == "HQ"
                    or (profile.role == "SITE" and profile.site_id == site.id and profile.can_manage_users)
                )
            )):
                return HttpResponseForbidden("Select a site where you are allowed to manage users and groups.")
            request.current_site = site

        match = re.match(r"^/sites/([\w-]+)(?:/|$)", path)
        if match:
            if not request.user.is_authenticated:
                return self.get_response(request)
            from .models import Site

            site = get_object_or_404(Site, slug=match.group(1), is_active=True)
            profile = self._profile(request.user)
            if not request.user.is_superuser and (not profile or (profile.role == "SITE" and profile.site_id != site.id)):
                return HttpResponseForbidden("You do not have access to this site.")
            request.current_site = site

        return self.get_response(request)

    @staticmethod
    def _profile(user):
        try:
            return user.fuel_profile
        except Exception:
            return None