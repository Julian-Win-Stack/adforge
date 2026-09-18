from django.contrib import admin

from gateway.models import ModelCall

from .models import ActivityEntry, Job, ProductPhoto


class ActivityEntryInline(admin.TabularInline[ActivityEntry, Job]):
    model = ActivityEntry
    fields = ["seq", "message", "reason", "created_at"]
    readonly_fields = ["seq", "message", "reason", "created_at"]
    extra = 0
    can_delete = False


class ProductPhotoInline(admin.TabularInline[ProductPhoto, Job]):
    model = ProductPhoto
    fields = ["position", "source_url", "file"]
    readonly_fields = ["position", "source_url", "file"]
    extra = 0
    can_delete = False


class ModelCallInline(admin.TabularInline[ModelCall, Job]):
    model = ModelCall
    fields = ["purpose", "model", "attempt", "outcome", "cost_usd", "duration_ms", "reason"]
    readonly_fields = [
        "purpose",
        "model",
        "attempt",
        "outcome",
        "cost_usd",
        "duration_ms",
        "reason",
    ]
    extra = 0
    can_delete = False
    show_change_link = True


@admin.register(Job)
class JobAdmin(admin.ModelAdmin[Job]):
    list_display = ["product_url", "status", "target_seconds", "created_at"]
    list_filter = ["status"]
    search_fields = ["product_url"]
    readonly_fields = ["id", "created_at"]
    inlines = [ActivityEntryInline, ProductPhotoInline, ModelCallInline]


@admin.register(ActivityEntry)
class ActivityEntryAdmin(admin.ModelAdmin[ActivityEntry]):
    list_display = ["job", "seq", "message", "reason", "created_at"]
    list_select_related = ["job"]
    search_fields = ["message", "reason"]
