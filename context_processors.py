# context_processors.py
from .models import BackgroundImage  # Replace with your actual model name

def site_background(request):
    # Fetch the active background image
    bg = BackgroundImage.objects.filter(is_active=True).first()
    return {
        'active_background': bg
    }
