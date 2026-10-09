---
name: PRIME Discord audit logging
description: Owner constraints for category isolation, truthful actor attribution, message-content handling, and invite tracking.
---

Keep Discord log categories independent per guild: each category's enabled state, destination, and selected event types belong to that category. Disabling one category must not suppress another category or leak unchecked event details into a combined log.

**Why:** The owner explicitly required category-local controls so administrators can turn off one type of logging without affecting the rest.

**How to apply:** Gate sends and fields by the exact category/event selection, including legacy logger paths and combined gateway updates.

Never claim an actor or invite source unless Discord provides a recent, unique match. For invite attribution, require a stable complete snapshot and exactly one invite's use count to increase by one; ambiguous concurrent joins, invite-set changes, missing permissions, or unavailable data remain unknown. Persist snapshots so comparisons remain restart-safe.

**Why:** The owner explicitly prohibited presenting unknown information as fact and required conservative attribution that survives bot restarts.

**How to apply:** Prefer unknown over a best guess; clearly name Discord permission and gateway limits, and do not state coverage for events the API or bot access cannot support.

Message text is sensitive: log it only when its specific content event is selected, default content capture off, and do not retain general server-wide message history.

**Why:** The owner required content logging to be individually controlled and asked to preserve the existing database without turning it into a message-history store.

**How to apply:** Keep content out of persistent database records; show what intents are needed and state when Discord did not provide message content.
