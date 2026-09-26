from pathlib import PurePath

from django.contrib import admin
from django.utils.html import format_html

from adforge import file_store
from gateway.models import ModelCall

from .models import Job, ProducedItem, ProductPhoto, Scene, SceneStep

_PICTURE_ENDINGS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_AUDIO_ENDINGS = {".wav", ".mp3"}
_VIDEO_ENDINGS = {".mp4"}


class ShowsFile:
    """Shows a stored file so it can be seen or heard in the admin, with a link to open it."""

    @admin.display(description="File")
    def file_preview(self, item: ProductPhoto | ProducedItem) -> str:
        if not item.file:
            return "-"
        url = file_store.url(item.file)
        ending = PurePath(item.file).suffix.lower()
        if ending in _PICTURE_ENDINGS:
            shown = format_html('<img src="{}" style="max-height: 160px">', url)
        elif ending in _AUDIO_ENDINGS:
            shown = format_html('<audio controls preload="none" src="{}"></audio>', url)
        elif ending in _VIDEO_ENDINGS:
            shown = format_html(
                '<video controls preload="none" src="{}" style="max-height: 240px"></video>', url
            )
        else:
            shown = format_html("")
        return format_html('{}<br><a href="{}" target="_blank">{}</a>', shown, url, item.file)


class ProductPhotoInline(ShowsFile, admin.TabularInline[ProductPhoto, Job]):
    model = ProductPhoto
    fields = ["position", "source_url", "file_preview", "shows_product_colour"]
    readonly_fields = ["position", "source_url", "file_preview", "shows_product_colour"]
    extra = 0
    can_delete = False


class SceneInline(admin.TabularInline[Scene, Job]):
    model = Scene
    fields = ["number", "line", "fact_checked", "fact_problems", "status"]
    readonly_fields = ["number", "line", "fact_checked", "fact_problems", "status"]
    extra = 0
    can_delete = False


class ProducedItemInline(ShowsFile, admin.TabularInline[ProducedItem, Job]):
    model = ProducedItem
    fields = [
        "kind",
        "version",
        "scene",
        "step",
        "file_preview",
        "voice_id",
        "words_per_second",
        "made_from",
        "picture",
        "seconds",
        "text",
        "words",
        "cuts",
        "captions",
        "created_at",
    ]
    readonly_fields = [
        "kind",
        "version",
        "scene",
        "step",
        "file_preview",
        "voice_id",
        "words_per_second",
        "made_from",
        "picture",
        "seconds",
        "text",
        "words",
        "cuts",
        "captions",
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
        "picture",
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
        "picture",
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
    raw_id_fields = ["made_from", "picture"]


@admin.register(ProducedItem)
class ProducedItemAdmin(ShowsFile, admin.ModelAdmin[ProducedItem]):
    list_display = [
        "job",
        "kind",
        "version",
        "scene",
        "made_from",
        "picture",
        "file_preview",
        "seconds",
        "words_per_second",
        "text",
        "created_at",
    ]
    list_select_related = ["job", "scene"]
    list_filter = ["kind"]
    readonly_fields = ["file_preview"]
