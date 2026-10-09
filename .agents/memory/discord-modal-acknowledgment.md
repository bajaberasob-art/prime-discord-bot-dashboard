---
name: Discord modal acknowledgements
description: Preserve Discord's requirement that modal launches are the initial response to component interactions.
---

When a UI callback delegates to another method that sends a modal, explicitly mark the callback as modal-opening so the shared interaction guard does not defer first.

**Why:** The runtime can detect a direct `send_modal` call from callback source, but delegation hides that call. An automatic defer makes the later modal response fail with `InteractionResponded`.

**How to apply:** Mark callbacks for every action that can launch a modal; leave ordinary actions on the shared early-acknowledgement path. Add a regression test that checks the runtime classifies delegated modal actions correctly.
