"""AI provider abstraction layer, plus the Version 3 AI editing brain.

New providers (OpenRouter, local models, Claude, ...) should be added by
creating a new module implementing :class:`ai.base_provider.AIProvider` and
registering it with :class:`ai.provider_factory.ProviderFactory`; no existing
code needs to change.

Version 3 adds a provider-agnostic "editing brain" built on top of that same
abstraction, turning a Version 2 :class:`models.analysis_result.AnalysisResult`
into a :class:`models.edit_plan.EditPlan`:

- :class:`ai.context_builder.ContextBuilder` -- compacts an AnalysisResult
  into the minimal, token-efficient data the AI actually needs.
- :class:`ai.prompt_templates.PromptTemplates` -- the "professional YouTube
  editor" persona, editing principles, and strict JSON output contract.
- :class:`ai.prompt_builder.PromptBuilder` -- combines the two above into
  one provider-agnostic prompt string.
- :class:`ai.response_parser.ResponseParser` -- validates the AI's raw JSON
  response and turns it into an :class:`models.edit_plan.EditPlan`.
- :class:`ai.edit_planner.EditPlanner` -- orchestrates all four stages
  above using any :class:`ai.base_provider.AIProvider`.

This layer only ever *decides* how a video should be edited -- it never
edits video, renders anything, or talks to DaVinci Resolve.

Version 3.5 improves the quality, safety, and consistency of that decision
layer without changing what any Version 3 caller sees:

- :class:`ai.editing_rules.EditingRules` -- a configurable, extendable set
  of what to keep/remove and how much each matters, rendered into the
  prompt in place of a hardcoded rules string.
- :class:`ai.editing_scorer.EditingScorer` -- best-effort scoring of an
  EditPlan's segments against the active rules, for logging/diagnostics.
- :class:`ai.edit_plan_validator.EditPlanValidator` -- catches and, where
  possible, automatically repairs structural problems (overlaps,
  duplicates, gaps, invalid actions/timestamps, tiny clips) in every
  EditPlan before it's returned.
- :class:`ai.edit_simulator.EditSimulator` -- computes what an EditPlan
  would produce (final length, cut count, clip stats, ...) without editing
  any video.
- :class:`ai.prompt_templates.PromptTemplates` now also exposes multiple
  content-style templates (gaming, vlog, tutorial, podcast, reaction,
  short-form) selectable via ``ai.edit_planner.EditPlanner.create_edit_plan(style=...)``.
- :class:`ai.provider_factory.ProviderFactory` can register providers that
  aren't implemented yet (:meth:`ai.provider_factory.ProviderFactory.register_planned`),
  so a future Settings screen can list every provider AutoCutAI knows
  about (Gemini, OpenRouter, Claude, Groq, local models, generic
  OpenAI-compatible APIs) without importing classes that don't exist.
"""
