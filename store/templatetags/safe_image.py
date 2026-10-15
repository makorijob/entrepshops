"""Safe image URL filter that never crashes the page."""

from django import template

register = template.Library()


@register.filter
def safe_url(file_field):
    if not file_field:
        return ''
    try:
        return file_field.url
    except RecursionError:
        return ''
    except (ValueError, OSError):
        return ''
    except Exception:
        return ''
