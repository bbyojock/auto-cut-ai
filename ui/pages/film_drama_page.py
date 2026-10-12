"""Film and drama editing workspace.

This page is intentionally separate from the general automatic editor. It is
the entry point for script/storyboard-driven projects; the actual scene and
take matching can be added without changing the existing EditPlan pipeline.
"""

from __future__ import annotations

import customtkinter as ctk

from ui.pages.base_page import BasePage


class FilmDramaPage(BasePage):
    """Project setup page for screenplay and storyboard-driven editing."""

    def build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

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

        self._add_path_row(setup, 0, "프로젝트 이름", self._project_name, "예: 단편영화 1차 편집")
        self._add_path_row(setup, 1, "대본 파일", self._script_path, "TXT / DOCX / PDF 또는 직접 작성")
        self._add_path_row(setup, 2, "콘티 폴더", self._storyboard_path, "콘티 이미지 폴더")
        self._add_path_row(setup, 3, "촬영본 폴더", self._footage_path, "씬별 촬영본 폴더")

        ctk.CTkButton(
            setup,
            text="영화·드라마 프로젝트 준비",
            command=self._prepare_project,
        ).grid(row=4, column=0, columnspan=2, padx=12, pady=(16, 12), sticky="e")

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
        self._status_label.grid(row=4, column=0, padx=30, pady=(10, 30), sticky="new")

    @staticmethod
    def _add_path_row(
        parent: ctk.CTkFrame,
        row: int,
        label: str,
        variable: ctk.StringVar,
        placeholder: str,
    ) -> None:
        ctk.CTkLabel(parent, text=label, width=110, anchor="w").grid(
            row=row, column=0, padx=(12, 8), pady=7, sticky="w"
        )
        ctk.CTkEntry(parent, textvariable=variable, placeholder_text=placeholder).grid(
            row=row, column=1, padx=(0, 12), pady=7, sticky="ew"
        )

    def _prepare_project(self) -> None:
        name = self._project_name.get().strip() or "이름 없는 영화·드라마 프로젝트"
        missing = [
            label
            for label, variable in (
                ("대본 파일", self._script_path),
                ("콘티 폴더", self._storyboard_path),
                ("촬영본 폴더", self._footage_path),
            )
            if not variable.get().strip()
        ]
        if missing:
            self._status_label.configure(
                text=f"{name}\n\n다음 입력을 등록해주세요: {', '.join(missing)}",
                text_color="#e0a458",
            )
            return
        self._status_label.configure(
            text=(
                f"{name}\n\n"
                "프로젝트 입력이 등록되었습니다. 다음 단계에서 대본 장면 분석과 "
                "촬영본 매칭 기능을 연결할 수 있습니다."
            ),
            text_color=("gray10", "gray90"),
        )
