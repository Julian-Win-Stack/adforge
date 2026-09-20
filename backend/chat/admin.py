from django.contrib import admin

from .models import Attachment, Message, Session


class MessageInline(admin.TabularInline[Message, Session]):
    model = Message
    fields = ["seq", "role", "text", "created_at"]
    readonly_fields = ["seq", "role", "text", "created_at"]
    extra = 0
    can_delete = False
    show_change_link = True


class AttachmentInline(admin.TabularInline[Attachment, Message]):
    model = Attachment
    fields = ["position", "kind", "file"]
    readonly_fields = ["position", "kind", "file"]
    extra = 0
    can_delete = False


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin[Session]):
    list_display = ["__str__", "created_at"]
    search_fields = ["name"]
    readonly_fields = ["id", "created_at"]
    inlines = [MessageInline]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin[Message]):
    list_display = ["session", "seq", "role", "text", "created_at"]
    list_select_related = ["session"]
    list_filter = ["role"]
    search_fields = ["text"]
    inlines = [AttachmentInline]
