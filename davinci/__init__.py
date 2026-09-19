"""DaVinci Resolve integration layer.

Empty through Version 4.6 -- reserved for exactly this: Version 5's real
cut-editing integration.

- :mod:`davinci.edit_plan_applier` -- pure EditPlan -> REMOVE-ranges logic,
  no Resolve dependency, fully unit-testable on its own.
- :mod:`davinci.resolve_connection` -- connects to a running Resolve via
  Blackmagic's official Scripting API (no custom bridge/server).
- :mod:`davinci.timeline_editor` -- duplicates the current timeline and
  applies REMOVE ranges to the copy via split + ripple-delete.

See :class:`services.resolve_export_service.ResolveExportService` for the
single entry point that ties all three together.
"""
