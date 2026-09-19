"""Dependency container passed explicitly to every UI page.

Rather than relying on module-level globals or singletons, the composition
root (``app.py``) builds one :class:`AppContext` and hands it to the main
window, which in turn hands it to every page. Pages pull exactly the
collaborators they need from it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ai.edit_planner import EditPlanner
from ai.key_manager import APIKeyManager
from ai.provider_factory import ProviderFactory
from config.config_manager import ConfigManager
from core.session import ConversationSession
from services.ai_request_log_service import AIRequestLogService
from services.chat_service import ChatService
from services.edit_generation_service import EditGenerationService
from services.edit_plan_cache_service import EditPlanCacheService
from services.edit_plan_chat_service import EditPlanChatService
from services.edit_plan_io_service import EditPlanIOService
from services.edit_plan_revision_service import EditPlanRevisionService
from services.editing_instructions_service import EditingInstructionsService
from services.folder_analysis_service import FolderAnalysisService
from services.multicam_edit_service import MulticamEditService
from services.personal_profile_service import PersonalProfileService
from services.resolve_export_service import ResolveExportService


@dataclass
class AppContext:
    """Bundles every service/manager the UI layer is allowed to depend on."""

    config_manager: ConfigManager
    provider_factory: ProviderFactory
    key_manager: APIKeyManager
    session: ConversationSession
    chat_service: ChatService
    edit_planner: EditPlanner

    # Version 4 additions. All optional-with-defaults so any existing code
    # (and every earlier test/harness in this codebase) that constructs an
    # ``AppContext`` with only the original six fields keeps working
    # unchanged -- these simply default to a fresh, working instance.
    ai_request_log_service: AIRequestLogService = field(default_factory=AIRequestLogService)
    edit_plan_cache_service: EditPlanCacheService = field(default_factory=EditPlanCacheService)
    edit_plan_io_service: EditPlanIOService = field(default_factory=EditPlanIOService)
    edit_generation_service: EditGenerationService = None  # type: ignore[assignment]

    # Version 4.5 additions (Features 1, 2, 3, 13, 14, 15, 17). Same
    # pattern: optional-with-defaults, built in __post_init__ when they
    # need to be wired to *this* context's collaborators.
    personal_profile_service: PersonalProfileService = field(default_factory=PersonalProfileService)
    editing_instructions_service: EditingInstructionsService = None  # type: ignore[assignment]
    edit_plan_revision_service: EditPlanRevisionService = field(default_factory=EditPlanRevisionService)
    edit_plan_chat_service: EditPlanChatService = None  # type: ignore[assignment]

    # Version 5 addition (real cut-editing via DaVinci Resolve). Stateless,
    # so a fresh default instance is always fine -- no wiring needed.
    resolve_export_service: ResolveExportService = field(default_factory=ResolveExportService)

    # Version 5.3 addition (folder-based batch import/analysis + automatic
    # documentary/variety content-type routing). Also stateless -- it owns
    # no config of its own, so a fresh default instance needs no wiring.
    folder_analysis_service: FolderAnalysisService = field(default_factory=FolderAnalysisService)

    # Version 5.4 addition (automatic multicam angle-switching + XML
    # export). Stateless -- no wiring needed.
    multicam_edit_service: MulticamEditService = field(default_factory=MulticamEditService)

    def __post_init__(self) -> None:
        # Built here (rather than a bare field default) because it needs
        # to be wired to *this* context's edit_planner/cache/log service
        # instances, not fresh ones of its own.
        if self.edit_generation_service is None:
            self.edit_generation_service = EditGenerationService(
                edit_planner=self.edit_planner,
                cache_service=self.edit_plan_cache_service,
                ai_request_log_service=self.ai_request_log_service,
            )

        if self.editing_instructions_service is None:
            self.editing_instructions_service = EditingInstructionsService(
                personal_profile_service=self.personal_profile_service
            )

        if self.edit_plan_chat_service is None:
            self.edit_plan_chat_service = EditPlanChatService(
                provider=self.edit_planner.provider,
                model=self.edit_planner.model,
                revision_service=self.edit_plan_revision_service,
                personal_profile_service=self.personal_profile_service,
            )
