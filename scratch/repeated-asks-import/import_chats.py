"""Insert the repeated-ask test chats into the app's database. Insert only, never changes or
deletes anything. A session already in its folder with the same name is skipped, so it can
run again with more sessions. Starts no job: producer_running stays False.
Run inside the app's Django container:  python manage.py shell < import_chats.py
with the data file at /tmp/ra-import.json (or env RA_IMPORT)."""
import json
import os

from django.db import transaction

from chat.models import Folder, Message, Session

data = json.load(open(os.environ.get("RA_IMPORT", "/tmp/ra-import.json")))
added = skipped = 0
for f in data["folders"]:
    if not f["sessions"]:
        continue
    folder = Folder.objects.filter(name__iexact=f["name"]).first() or Folder.objects.create(name=f["name"])
    # Newest first in the sidebar, so add the last one first and run 1 of case 1 ends on top.
    for s in reversed(f["sessions"]):
        if Session.objects.filter(folder=folder, name=s["name"]).exists():
            skipped += 1
            continue
        with transaction.atomic():
            session = Session.objects.create(name=s["name"], folder=folder, producer_running=False)
            Message.objects.bulk_create([
                Message(session=session, seq=i, role=m["role"],
                        level=Message.Level.INFO if m["role"] == "notice" else "", text=m["text"])
                for i, m in enumerate(s["messages"], start=1)
            ])
        added += 1
print(f"added {added} sessions, skipped {skipped} already there")
