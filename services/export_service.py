"""Builds exportable report text for a generated EditPlan (Version 4, Feature 17).

Three formats share the same underlying facts (summary, reasons,
confidence, warnings, statistics) so they can never drift apart: JSON (the
plan's own :meth:`~models.edit_plan.EditPlan.to_dict`, machine-readable),
plain TXT, and Markdown for human reading/sharing.
"""

from __future__ import annotations

from typing import Optional

from models.edit_plan import EditPlan
from models.edit_simulation_result import EditSimulationResult
from utils.constants import EDIT_ACTION_COMPRESS


def _format_seconds(seconds: float) -> str:
    minutes, remainder = divmod(max(0.0, seconds), 60)
    return f"{int(minutes):02d}:{remainder:05.2f}"


class ExportService:
    """Stateless: every method takes exactly the data it needs to render."""

    @staticmethod
    def to_json(plan: EditPlan) -> str:
        """Machine-readable export -- identical to what Save EditPlan writes."""
        return plan.to_json()

    @staticmethod
    def to_txt(
        plan: EditPlan,
        source_video_name: str = "",
        simulation: Optional[EditSimulationResult] = None,
    ) -> str:
        lines = [
            "AutoCutAI - Edit Plan Report",
            "=" * 32,
            "",
        ]
        if source_video_name:
            lines.append(f"Source video: {source_video_name}")
        lines.append(f"Model used: {plan.model_used}")
        lines.append(f"Target length: {_format_seconds(plan.target_length_seconds)} ({plan.target_length_seconds:.1f}s)")
        lines.append(f"Confidence: {plan.confidence * 100:.0f}%")
        lines.append(f"Processing time: {plan.processing_seconds:.1f}s")
        lines.append("")

        if simulation is not None:
            lines.append("Statistics:")
            lines.append(f"  Original length:      {_format_seconds(simulation.original_length_seconds)}")
            lines.append(f"  Estimated final length: {_format_seconds(simulation.estimated_final_length_seconds)}")
            lines.append(f"  Removed:              {simulation.removed_percentage:.1f}%")
            lines.append(f"  Cuts (kept clips):    {simulation.cut_count}")
            lines.append(f"  Average clip length:  {simulation.average_clip_length_seconds:.1f}s")
            lines.append(f"  Longest clip:         {simulation.longest_clip_seconds:.1f}s")
            lines.append(f"  Shortest clip:        {simulation.shortest_clip_seconds:.1f}s")
            lines.append("")

        lines.append(f"Summary reasons ({len(plan.reasons)}):")
        lines.extend(f"  - {reason}" for reason in plan.reasons) if plan.reasons else lines.append("  (none)")
        lines.append("")

        if plan.mood_recommendation:
            mood = plan.mood_recommendation
            lines.append("Suggested mood & background music:")
            lines.append(f"  Mood:  {mood.overall_mood}")
            if mood.tempo_description:
                lines.append(f"  Tempo: {mood.tempo_description}")
            if mood.music_genre_suggestions:
                lines.append(f"  BGM style: {', '.join(mood.music_genre_suggestions)}")
            if mood.reasoning:
                lines.append(f"  Why:   {mood.reasoning}")
            lines.append("")

        lines.append(f"Warnings ({len(plan.warnings)}):")
        lines.extend(f"  - {warning}" for warning in plan.warnings) if plan.warnings else lines.append("  (none)")
        lines.append("")

        lines.append(f"Timeline ({len(plan.all_segments_sorted)} segment(s)):")
        for segment in plan.all_segments_sorted:
            action_label = segment.action.upper()
            if segment.action == EDIT_ACTION_COMPRESS and segment.compression_speed_factor:
                action_label += f" {segment.compression_speed_factor:.1f}x"
            lines.append(
                f"  [{_format_seconds(segment.start_seconds)} -> {_format_seconds(segment.end_seconds)}] "
                f"{action_label:<12} - {segment.reason}"
            )
        return "\n".join(lines)

    @staticmethod
    def to_markdown(
        plan: EditPlan,
        source_video_name: str = "",
        simulation: Optional[EditSimulationResult] = None,
    ) -> str:
        lines = ["# AutoCutAI - Edit Plan Report", ""]
        if source_video_name:
            lines.append(f"**Source video:** {source_video_name}  ")
        lines.append(f"**Model used:** {plan.model_used}  ")
        lines.append(f"**Target length:** {_format_seconds(plan.target_length_seconds)} ({plan.target_length_seconds:.1f}s)  ")
        lines.append(f"**Confidence:** {plan.confidence * 100:.0f}%  ")
        lines.append(f"**Processing time:** {plan.processing_seconds:.1f}s")
        lines.append("")

        if simulation is not None:
            lines.append("## Statistics")
            lines.append("")
            lines.append("| Metric | Value |")
            lines.append("|---|---|")
            lines.append(f"| Original length | {_format_seconds(simulation.original_length_seconds)} |")
            lines.append(f"| Estimated final length | {_format_seconds(simulation.estimated_final_length_seconds)} |")
            lines.append(f"| Removed | {simulation.removed_percentage:.1f}% |")
            lines.append(f"| Cuts (kept clips) | {simulation.cut_count} |")
            lines.append(f"| Average clip length | {simulation.average_clip_length_seconds:.1f}s |")
            lines.append(f"| Longest clip | {simulation.longest_clip_seconds:.1f}s |")
            lines.append(f"| Shortest clip | {simulation.shortest_clip_seconds:.1f}s |")
            lines.append("")

        lines.append("## Summary")
        lines.append("")
        if plan.reasons:
            lines.extend(f"- {reason}" for reason in plan.reasons)
        else:
            lines.append("_(no summary reasons provided)_")
        lines.append("")

        if plan.mood_recommendation:
            mood = plan.mood_recommendation
            lines.append("## Suggested Mood & Background Music")
            lines.append("")
            lines.append(f"**Mood:** {mood.overall_mood}  ")
            if mood.tempo_description:
                lines.append(f"**Tempo:** {mood.tempo_description}  ")
            if mood.music_genre_suggestions:
                lines.append(f"**BGM style:** {', '.join(mood.music_genre_suggestions)}  ")
            if mood.reasoning:
                lines.append(f"**Why:** {mood.reasoning}")
            lines.append("")

        lines.append(f"## Warnings ({len(plan.warnings)})")
        lines.append("")
        if plan.warnings:
            lines.extend(f"- ⚠️ {warning}" for warning in plan.warnings)
        else:
            lines.append("_(none)_")
        lines.append("")

        lines.append(f"## Timeline ({len(plan.all_segments_sorted)} segments)")
        lines.append("")
        lines.append("| Start | End | Action | Reason |")
        lines.append("|---|---|---|---|")
        for segment in plan.all_segments_sorted:
            if segment.action == EDIT_ACTION_COMPRESS:
                icon = f"⏩ COMPRESS {segment.compression_speed_factor:.1f}x" if segment.compression_speed_factor else "⏩ COMPRESS"
            elif segment.is_keep:
                icon = "✅ KEEP"
            else:
                icon = "✂️ REMOVE"
            reason = segment.reason.replace("|", "\\|")
            lines.append(
                f"| {_format_seconds(segment.start_seconds)} | {_format_seconds(segment.end_seconds)} | "
                f"{icon} | {reason} |"
            )
        return "\n".join(lines)
