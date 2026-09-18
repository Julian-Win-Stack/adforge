from django.contrib import admin

from gateway.models import ModelCall

from .models import ActivityEntry, Job, ProductPhoto, Question, Scene


class ActivityEntryInline(admin.TabularInline[ActivityEntry, Job]):
    model = ActivityEntry
    fields = ["seq", "message", "reason", "created_at"]
    readonly_fields = ["seq", "message", "reason", "created_at"]
    extra = 0
    can_delete = False


class ProductPhotoInline(admin.TabularInline[ProductPhoto, Job]):
    model = ProductPhoto
    fields = ["position", "source_url", "file", "shows_product_colour"]
    readonly_fields = ["position", "source_url", "file", "shows_product_colour"]
    extra = 0
    can_delete = False


class SceneInline(admin.TabularInline[Scene, Job]):
    model = Scene
    fields = ["number", "line", "slot_seconds", "status"]
    readonly_fields = ["number", "line", "slot_seconds", "status"]
    extra = 0
    can_delete = False


class QuestionInline(admin.TabularInline[Question, Job]):
    model = Question
    fields = ["kind", "question", "reason", "answer", "asked_at", "answered_at"]
    readonly_fields = ["kind", "question", "reason", "answer", "asked_at", "answered_at"]
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
    inlines = [
        ActivityEntryInline,
        ProductPhotoInline,
        SceneInline,
        QuestionInline,
        ModelCallInline,
    ]


@admin.register(ActivityEntry)
class ActivityEntryAdmin(admin.ModelAdmin[ActivityEntry]):
    list_display = ["job", "seq", "message", "reason", "created_at"]
    list_select_related = ["job"]
    search_fields = ["message", "reason"]


@admin.register(Scene)
class SceneAdmin(admin.ModelAdmin[Scene]):
    list_display = ["job", "number", "line", "slot_seconds", "status"]
    list_select_related = ["job"]
    list_filter = ["status"]
    search_fields = ["line"]


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin[Question]):
    list_display = ["job", "kind", "question", "answer", "asked_at", "answered_at"]
    list_select_related = ["job"]
    list_filter = ["kind"]
    search_fields = ["question", "answer"]
