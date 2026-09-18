from django.contrib import admin

from .models import ModelCall


@admin.register(ModelCall)
class ModelCallAdmin(admin.ModelAdmin[ModelCall]):
    list_display = [
        "created_at",
        "purpose",
        "model",
        "attempt",
        "outcome",
        "cost_usd",
        "duration_ms",
        "decision",
        "job",
    ]
    list_filter = ["outcome", "purpose", "model", "provider"]
    list_select_related = ["job"]
    search_fields = ["reason", "error"]
