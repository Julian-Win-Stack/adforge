from django.contrib import admin

from gateway.models import ModelCall

from .models import Job, ProducedItem, ProductPhoto, Scene, SceneStep


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
    fields = [
        "kind",
        "version",
        "scene",
        "step",
        "file",
        "voice_id",
        "words_per_second",
        "made_from",
        "seconds",
        "text",
        "words",
        "created_at",
    ]
    readonly_fields = [
        "kind",
        "version",
        "scene",
        "step",
        "file",
        "voice_id",
        "words_per_second",
        "made_from",
        "seconds",
        "text",
        "words",
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


class SceneStepInline(admin.TabularInline[SceneStep, Scene]):
    model = SceneStep
    fields = [
        "kind",
        "status",
        "reason",
        "photo",
        "prompt",
        "made_from",
        "started_at",
        "finished_at",
    ]
    readonly_fields = [
        "kind",
        "status",
        "reason",
        "photo",
        "prompt",
        "made_from",
        "started_at",
        "finished_at",
    ]
    extra = 0
    can_delete = False
    show_change_link = True


@admin.register(Scene)
class SceneAdmin(admin.ModelAdmin[Scene]):
    list_display = ["job", "number", "line", "fact_checked", "status"]
    list_select_related = ["job"]
    list_filter = ["status"]
    search_fields = ["line"]
    inlines = [SceneStepInline]


@admin.register(SceneStep)
class SceneStepAdmin(admin.ModelAdmin[SceneStep]):
    list_display = ["scene", "kind", "status", "started_at", "finished_at", "producer_read_at"]
    list_select_related = ["scene__job"]
    list_filter = ["kind", "status"]
    readonly_fields = ["started_at"]
    raw_id_fields = ["made_from"]


@admin.register(ProducedItem)
class ProducedItemAdmin(admin.ModelAdmin[ProducedItem]):
    list_display = [
        "job",
        "kind",
        "version",
        "scene",
        "made_from",
        "seconds",
        "words_per_second",
        "created_at",
    ]
    list_select_related = ["job", "scene"]
    list_filter = ["kind"]
