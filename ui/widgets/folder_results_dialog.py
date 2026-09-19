"""Folder batch-analysis results dialog (Version 5.3, extended in 5.4).

Shown once :class:`services.folder_analysis_service.FolderAnalysisService`
finishes analyzing every clip in a folder. Lists each clip -- in the
auto-arranged order when the folder was detected as documentary footage --
along with its detected content type, and lets the user load any one clip
straight into the AI Editor page's existing single-video EditPlan flow
with the recommended :mod:`ai.game_profiles` profile already applied.

Version 5.4 adds a section for detected multicam takes: each one gets a
button that runs :mod:`ai.multicam_angle_planner`'s automatic
angle-switching decision and exports the result as a Resolve-importable
XML, via the ``on_build_multicam_plan`` callback.
"""

from __future__ import annotations

from typing import Callable, Optional

import customtkinter as ctk

from ai.game_profiles import GAME_PROFILE_DISPLAY_NAMES
from models.folder_analysis_result import FolderAnalysisResult, FolderClipEntry

_CONTENT_TYPE_LABELS = {
    "documentary": "다큐멘터리 (Documentary)",
    "variety": "예능 (Variety)",
    "unknown": "미분류 (Unknown)",
}


def _format_duration(seconds: Optional[float]) -> str:
    if not seconds:
        return "--:--"
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes:02d}:{secs:02d}"


class FolderResultsDialog(ctk.CTkToplevel):
    """Read-only summary of a folder batch analysis, with a per-clip "use this" action."""

    def __init__(
        self,
        master,
        result: FolderAnalysisResult,
        on_use_clip: Callable[[FolderClipEntry], None],
        on_build_multicam_plan: Optional[Callable[[int], None]] = None,
    ) -> None:
        super().__init__(master)
        self.title("Folder Analysis Results")
        self.geometry("700x620")
        self._result = result
        self._on_use_clip = on_use_clip
        self._on_build_multicam_plan = on_build_multicam_plan
        self._build()
        self.transient(master)

    def _build(self) -> None:
        self.grid_rowconfigure(3, weight=1)
        self.grid_columnconfigure(0, weight=1)

        header = ctk.CTkLabel(self, text="Folder Analysis Complete", font=ctk.CTkFont(size=18, weight="bold"))
        header.grid(row=0, column=0, padx=20, pady=(20, 4), sticky="w")

        content_label = _CONTENT_TYPE_LABELS.get(self._result.dominant_content_type, self._result.dominant_content_type)
        profile_label = GAME_PROFILE_DISPLAY_NAMES.get(self._result.recommended_profile, self._result.recommended_profile)
        summary_lines = [
            f"{self._result.folder_path}",
            f"{self._result.clip_count}개 클립 분석 완료 -- 감지된 콘텐츠 유형: {content_label}",
            f"자동 추천 편집 프로필: {profile_label}",
        ]
        if self._result.is_multicam:
            group_sizes = ", ".join(f"{g.camera_count}캠" for g in self._result.multicam_groups)
            summary_lines.append(
                f"파일명에서 멀티캠 촬영분 {len(self._result.multicam_groups)}개 세트를 감지했습니다 ({group_sizes})."
            )
        if self._result.is_documentary:
            summary_lines.append("다큐멘터리로 감지되어 클립을 자동으로 시간순 배열했습니다.")
        if self._result.skipped:
            summary_lines.append(f"건너뛴 파일: {len(self._result.skipped)}개 (분석 실패 또는 형식 미지원)")

        subheader = ctk.CTkLabel(
            self, text="\n".join(summary_lines), text_color="gray60", anchor="w", justify="left",
        )
        subheader.grid(row=1, column=0, padx=20, pady=(0, 8), sticky="w")

        if self._result.is_multicam and self._on_build_multicam_plan is not None:
            self._build_multicam_section().grid(row=2, column=0, padx=20, pady=(0, 8), sticky="ew")

        self._list_frame = ctk.CTkScrollableFrame(self, label_text="")
        self._list_frame.grid(row=3, column=0, padx=20, pady=(0, 12), sticky="nsew")
        self._list_frame.grid_columnconfigure(1, weight=1)

        for row, clip in enumerate(self._result.clips):
            self._build_clip_row(row, clip)

        if not self._result.clips:
            ctk.CTkLabel(
                self._list_frame, text="분석 가능한 영상이 없습니다.", text_color="gray60",
            ).grid(row=0, column=0, padx=8, pady=8, sticky="w")

        close_button = ctk.CTkButton(self, text="Close", fg_color="gray30", hover_color="gray20", command=self.destroy)
        close_button.grid(row=3, column=0, padx=20, pady=(0, 20), sticky="e")

    def _build_multicam_section(self) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self, fg_color="gray17")
        frame.grid_columnconfigure(1, weight=1)

        title = ctk.CTkLabel(frame, text="멀티캠 자동 앵글 전환", font=ctk.CTkFont(weight="bold"))
        title.grid(row=0, column=0, columnspan=3, padx=12, pady=(10, 2), sticky="w")

        for row, group in enumerate(self._result.multicam_groups, start=1):
            time_label = group.timestamp.strftime("%H:%M:%S")
            cams_label = " / ".join(group.camera_tags)
            info = ctk.CTkLabel(
                frame, text=f"Take @ {time_label}  ({cams_label})", anchor="w",
            )
            info.grid(row=row, column=0, columnspan=2, padx=(12, 8), pady=4, sticky="w")

            build_button = ctk.CTkButton(
                frame, text="자동 편집 생성 (XML)", width=170,
                command=lambda idx=row - 1: self._on_build_multicam_plan(idx),
            )
            build_button.grid(row=row, column=2, padx=(0, 12), pady=4, sticky="e")

        note = ctk.CTkLabel(
            frame,
            text="리액션/발화를 감지해 카메라 앵글을 자동으로 전환한 뒤, DaVinci Resolve로 Import 가능한 XML로 내보냅니다.",
            text_color="gray60", anchor="w", justify="left", wraplength=620,
        )
        note.grid(row=len(self._result.multicam_groups) + 1, column=0, columnspan=3, padx=12, pady=(2, 10), sticky="w")
        return frame

    def _build_clip_row(self, row: int, clip: FolderClipEntry) -> None:
        order_text = f"#{clip.suggested_order + 1}" if clip.suggested_order is not None else "--"
        order_label = ctk.CTkLabel(self._list_frame, text=order_text, width=36, text_color="gray60")
        order_label.grid(row=row, column=0, padx=(4, 8), pady=6, sticky="w")

        duration = _format_duration(clip.analysis.video.duration_seconds)
        content_label = _CONTENT_TYPE_LABELS.get(clip.content_type, clip.content_type)
        detail_lines = [f"{clip.file_name}  ({duration})", f"{content_label} -- confidence {clip.confidence:.0%}"]
        if clip.is_multicam:
            # Same multicam_group_index -> same take, so a stable 1-indexed
            # take number reads consistently across every row in the list.
            take_number = clip.multicam_group_index + 1
            detail_lines.append(f"멀티캠 Take #{take_number} -- 캠: {clip.camera_tag}")
        elif clip.camera_tag:
            detail_lines.append(f"캠 태그: {clip.camera_tag} (매칭되는 다른 앵글 없음)")
        detail_label = ctk.CTkLabel(self._list_frame, text="\n".join(detail_lines), anchor="w", justify="left")
        detail_label.grid(row=row, column=1, padx=(0, 8), pady=6, sticky="ew")

        use_button = ctk.CTkButton(
            self._list_frame, text="Use this clip", width=110,
            command=lambda c=clip: self._use_clip_clicked(c),
        )
        use_button.grid(row=row, column=2, padx=(0, 4), pady=6, sticky="e")

    def _use_clip_clicked(self, clip: FolderClipEntry) -> None:
        self._on_use_clip(clip)
        self.destroy()
