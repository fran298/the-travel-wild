from django.utils.deprecation import MiddlewareMixin
from django.conf import settings
import re

class ConditionalCsrfMiddleware(MiddlewareMixin):
    def process_view(self, request, callback, callback_args, callback_kwargs):
        exempt_urls = getattr(settings, "CSRF_EXEMPT_URLS", [])
        path = request.path_info.lstrip("/")
        for pattern in exempt_urls:
            if re.match(pattern, path):
                # Skip CSRF for matching URLs
                return None
        return None