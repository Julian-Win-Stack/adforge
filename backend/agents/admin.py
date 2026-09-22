from django.contrib import admin

from .models import ToolCall


@admin.register(ToolCall)
class ToolCallAdmin(admin.ModelAdmin[ToolCall]):
    list_display = ["created_at", "session", "agent", "tool", "finished", "job"]
    list_filter = ["agent", "tool", "finished"]
    list_select_related = ["session", "job"]
    search_fields = ["result"]
