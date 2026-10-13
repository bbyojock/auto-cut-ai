"""Film and drama editing workspace.

This page is intentionally separate from the general automatic editor. It is
the entry point for script/storyboard-driven projects; the actual scene and
take matching can be added without changing the existing EditPlan pipeline.
"""

from __future__ import annotations

from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from models.film_drama_project import FilmDramaProject
from services.film_drama_service import FilmDramaService
from ui.pages.base_page import BasePage


class FilmDramaPage(BasePage):
    """Project setup page for screenplay and storyboard-driven editing."""

    def build(self) -> None:
        self._service = FilmDramaService()
        self._project: FilmDramaProject | None = None
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)

        ctk.CTkLabel(
            self,
            text="Film / Drama Editor",
            font=ctk.CTkFont(size=28, weight="bold"),
        ).grid(row=0, column=0, padx=30, pady=(30, 4), sticky="w")

        ctk.CTkLabel(
            self,
            text="대본과 콘티를 기준으로 촬영본을 씬·테이크 단위로 정리하는 편집 모드",
            text_color="gray60",
        ).grid(row=1, column=0, padx=30, pady=(0, 20), sticky="w")

        setup = ctk.CTkFrame(self)
        setup.grid(row=2, column=0, padx=30, pady=10, sticky="ew")
        setup.grid_columnconfigure(1, weight=1)

        self._project_name = ctk.StringVar()
        self._script_path = ctk.StringVar()
        self._storyboard_path = ctk.StringVar()
        self._footage_path = ctk.StringVar()
        self._shooting_plan_path = ctk.StringVar()

        self._add_path_row(setup, 0, "프로젝트 이름", self._project_name, "예: 단편영화 1차 편집")
        self._add_path_row(setup, 1, "대본 파일", self._script_path, "TXT 대본 파일", self._choose_script)
        self._add_path_row(setup, 2, "콘티 폴더", self._storyboard_path, "콘티 이미지 폴더", self._choose_storyboard)
        self._add_path_row(setup, 3, "촬영본 폴더", self._footage_path, "씬별 촬영본 폴더", self._choose_footage)
        self._add_path_row(
            setup, 4, "촬영계획표", self._shooting_plan_path,
            "TXT / CSV 촬영계획표 (선택)", self._choose_shooting_plan,
        )

        ctk.CTkButton(
            setup,
            text="대본·콘티·촬영본 분석",
            command=self._prepare_project,
        ).grid(row=5, column=0, columnspan=3, padx=12, pady=(16, 12), sticky="e")

        info = ctk.CTkFrame(self)
        info.grid(row=3, column=0, padx=30, pady=(14, 10), sticky="ew")
        ctk.CTkLabel(
            info,
            text="준비 단계",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", padx=18, pady=(14, 6))
        ctk.CTkLabel(
            info,
            text=(
                "1. 대본의 씬·대사 구조 읽기  →  "
                "2. 콘티와 촬영본 연결  →  "
                "3. 테이크 후보 분석  →  "
                "4. Resolve용 편집 초안 생성"
            ),
            text_color="gray60",
            wraplength=900,
            justify="left",
        ).pack(anchor="w", padx=18, pady=(0, 14))

        self._status_label = ctk.CTkLabel(
            self,
            text="프로젝트 파일을 등록하면 영화·드라마 편집 준비를 시작할 수 있습니다.",
            anchor="nw",
            justify="left",
        )
        self._status_label.grid(row=4, column=0, padx=30, pady=(10, 10), sticky="new")

        self._results = ctk.CTkTextbox(self, height=180)
        self._results.grid(row=5, column=0, padx=30, pady=(0, 30), sticky="nsew")
        self._results.configure(state="disabled")

    @staticmethod
    def _add_path_row(
        parent: ctk.CTkFrame,
        row: int,
        label: str,
        variable: ctk.StringVar,
        placeholder: str,
        browse_command=None,
    ) -> None:
        ctk.CTkLabel(parent, text=label, width=110, anchor="w").grid(
            row=row, column=0, padx=(12, 8), pady=7, sticky="w"
        )
        ctk.CTkEntry(parent, textvariable=variable, placeholder_text=placeholder).grid(
            row=row, column=1, padx=(0, 12), pady=7, sticky="ew"
        )
        if browse_command is not None:
            ctk.CTkButton(parent, text="찾기", width=70, command=browse_command).grid(
                row=row, column=2, padx=(0, 12), pady=7
            )

    def _choose_script(self) -> None:
        path = filedialog.askopenfilename(
            title="대본 파일 선택",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self._script_path.set(path)

    def _choose_storyboard(self) -> None:
        path = filedialog.askdirectory(title="콘티 이미지 폴더 선택")
        if path:
            self._storyboard_path.set(path)

    def _choose_footage(self) -> None:
        path = filedialog.askdirectory(title="촬영본 폴더 선택")
        if path:
            self._footage_path.set(path)

    def _choose_shooting_plan(self) -> None:
        path = filedialog.askopenfilename(
            title="촬영계획표 선택",
            filetypes=[
                ("Text and CSV files", "*.txt *.csv"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self._shooting_plan_path.set(path)

    def _prepare_project(self) -> None:
        name = self._project_name.get().strip() or "이름 없는 영화·드라마 프로젝트"
        script_path = self._optional_path(self._script_path, file=True)
        storyboard_path = self._optional_path(self._storyboard_path, directory=True)
        footage_path = self._optional_path(self._footage_path, directory=True)
        shooting_plan_path = self._optional_path(self._shooting_plan_path, file=True)
        invalid = [
            label for label, variable, path in (
                ("대본 파일", self._script_path, script_path),
                ("콘티 폴더", self._storyboard_path, storyboard_path),
                ("촬영본 폴더", self._footage_path, footage_path),
                ("촬영계획표", self._shooting_plan_path, shooting_plan_path),
            )
            if variable.get().strip() and path is None
        ]
        if invalid:
            self._status_label.configure(
                text=f"{name}\n\n경로를 확인해주세요: {', '.join(invalid)}",
                text_color="#e0a458",
            )
            return
        try:
            scenes = self._service.parse_script(script_path) if script_path else []
            panels = self._service.scan_storyboard(storyboard_path) if storyboard_path else []
            takes = self._service.scan_footage(footage_path) if footage_path else []
            shooting_plan_text = (
                self._service.read_text_file(shooting_plan_path)
                if shooting_plan_path else ""
            )
            self._project = FilmDramaProject(
                name=name,
                script_path=script_path,
                storyboard_path=storyboard_path,
                footage_path=footage_path,
                shooting_plan_path=shooting_plan_path,
                shooting_plan_text=shooting_plan_text,
                scenes=scenes,
                storyboard_panels=panels,
                footage_takes=takes,
            )
        except (OSError, UnicodeError) as exc:
            messagebox.showerror("Film / Drama", f"입력 분석에 실패했습니다.\n\n{exc}")
            return
        self._status_label.configure(
            text=f"{name}\n\n입력된 자료의 분석이 완료되었습니다. 비어 있는 자료는 나중에 추가할 수 있습니다.",
            text_color=("gray10", "gray90"),
        )
        self._render_results()

    def _render_results(self) -> None:
        if self._project is None:
            return
        project = self._project
        lines = [
            f"씬 {len(project.scenes)}개 · 콘티 패널 {len(project.storyboard_panels)}개 · 촬영본 {len(project.footage_takes)}개",
            f"씬 번호 매칭: {len(project.matched_scene_numbers)}개",
        ]
        if project.shooting_plan_text:
            lines.append(f"촬영계획표: {len(project.shooting_plan_text.splitlines())}줄")
        if not any((project.script_path, project.storyboard_path, project.footage_path, project.shooting_plan_path)):
            lines.append("등록된 입력 없이 프로젝트를 만들었습니다. 자료는 나중에 추가할 수 있습니다.")
        if project.unmatched_scene_numbers:
            lines.append(f"촬영본 미매칭 씬: {', '.join(project.unmatched_scene_numbers)}")
        lines.append("")
        for scene in project.scenes:
            takes = [
                take for take in project.footage_takes if take.scene_number == scene.scene_number
            ]
            panels = [
                panel for panel in project.storyboard_panels if panel.scene_number == scene.scene_number
            ]
            lines.append(
                f"[씬 {scene.scene_number}] {scene.heading} · 촬영본 {len(takes)}개 · 콘티 {len(panels)}개"
            )
            if scene.text:
                lines.append(f"  {scene.text[:140].replace(chr(10), ' ')}")
        self._results.configure(state="normal")
        self._results.delete("1.0", "end")
        self._results.insert("1.0", "\n".join(lines))
        self._results.configure(state="disabled")

    @staticmethod
    def _optional_path(variable: ctk.StringVar, *, file: bool = False, directory: bool = False) -> Path | None:
        raw = variable.get().strip()
        if not raw:
            return None
        path = Path(raw)
        if file and path.is_file():
            return path
        if directory and path.is_dir():
            return path
        return None
