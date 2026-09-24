from django.contrib import admin

from gateway.models import ModelCall

from .models import Job, ProducedItem, ProductPhoto, Scene


class ProductPhotoInline(admin.TabularInline[ProductPhoto, Job]):
    model = ProductPhoto
    fields = ["position", "source_url", "file", "shows_product_colour"]
    readonly_fields = ["position", "source_url", "file", "shows_product_colour"]
    extra = 0
    can_delete = False


class SceneInline(admin.TabularInline[Scene, Job]):
    model = Scene
    fields = ["number", "line", "fact_checked", "fact_problems", "status"]
    readonly_fields = ["number", "line", "fact_checked", "fact_problems", "status"]
    extra = 0
    can_delete = False


class ProducedItemInline(admin.TabularInline[ProducedItem, Job]):
    model = ProducedItem
    fields = ["kind", "version", "scene", "file", "voice_id", "words_per_second", "created_at"]
    readonly_fields = [
        "kind",
        "version",
        "scene",
        "file",
        "voice_id",
        "words_per_second",
        "created_at",
    ]
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
    list_display = [
        "product_url",
        "session",
        "variant_of",
        "status",
        "target_seconds",
        "created_at",
    ]
    list_filter = ["status"]
    list_select_related = ["session", "variant_of"]
    search_fields = ["product_url"]
    readonly_fields = ["id", "created_at"]
    inlines = [
        ProductPhotoInline,
        SceneInline,
        ProducedItemInline,
        ModelCallInline,
    ]


@admin.register(Scene)
class SceneAdmin(admin.ModelAdmin[Scene]):
    list_display = ["job", "number", "line", "fact_checked", "status"]
    list_select_related = ["job"]
    list_filter = ["status"]
    search_fields = ["line"]


@admin.register(ProducedItem)
class ProducedItemAdmin(admin.ModelAdmin[ProducedItem]):
    list_display = ["job", "kind", "version", "scene", "words_per_second", "created_at"]
    list_select_related = ["job", "scene"]
    list_filter = ["kind"]
