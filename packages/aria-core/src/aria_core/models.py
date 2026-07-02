from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class MessageRole(str, Enum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class SkillName(str, Enum):
    ANALYZE_PORTFOLIO = "analyze_portfolio"
    MANAGE_REPERTOIRE = "manage_repertoire"
    ZHC_BRIDGE = "zhc_bridge"
    DEVELOP_REPERTOIRE = "develop_repertoire"
    SCAN_MARKET = "scan_market"
    MEMORY_RECALL = "memory_recall"
    BUILD_OPTIMIZE = "build_optimize"
    GITHUB_SANDBOX = "github_sandbox"
    LAUNCHPAD_SELECT = "launchpad_select"
    FAQ_CONTENT = "faq_content"
    MARKETING_COMMS = "marketing_comms"
    TRAINING_PORTFOLIO = "training_portfolio"
    HOLDING_SITE = "holding_site"
    ENTREPRENEUR_CULTIVATION = "entrepreneur_cultivation"
    CAPABILITY_QI = "capability_qi"
    EPISTEMIC_CHECK = "epistemic_check"
    ACP_MARKETPLACE = "acp_marketplace"
    INGEST_REPO = "ingest_repo"
    WORKER_DELEGATE = "worker_delegate"


class RepertoireItemStatus(str, Enum):
    IDEA = "idea"
    BUILDING = "building"
    LIVE = "live"
    PAUSED = "paused"
    ARCHIVED = "archived"


class EntityType(str, Enum):
    HOLDING = "holding"
    SUBSIDIARY = "subsidiary"
    VENTURE = "venture"


class RepertoireItem(BaseModel):
    id: str
    name: str
    description: str
    status: RepertoireItemStatus
    category: str
    revenue_monthly: float = 0.0
    priority: int = Field(ge=1, le=5, default=3)
    tags: list[str] = Field(default_factory=list)
    zhc_aligned: bool = False
    created_at: datetime
    updated_at: datetime
    notes: str = ""
    entity_type: EntityType = EntityType.SUBSIDIARY
    parent_id: str | None = None
    slug: str | None = None


class AgentMessage(BaseModel):
    id: str
    role: MessageRole
    content: str
    skill_used: SkillName | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class HoldingStructure(BaseModel):
    holding: RepertoireItem
    subsidiaries: list[RepertoireItem] = Field(default_factory=list)
    ventures: list[RepertoireItem] = Field(default_factory=list)
    aria_title: str = "Chief Autonomous Officer (CAO)"
    holding_tagline: str = ""
    governance_rule: str = ""
    subsidiary_label: str = ""


class ZHCAgentMessage(BaseModel):
    """Protocole inter-agents inspiré ZHC — pour communication avec JUNO et pairs."""
    protocol_version: str = "zhc-a2a/1.0"
    from_agent: str = "ARIA@AriaVanguardZHC"
    to_agent: str = "JUNO@ZHC"
    message_type: str
    payload: dict[str, Any]
    timestamp: datetime
    signature_hint: str = "human-approved"


class HeartbeatTask(BaseModel):
    id: str
    name: str
    description: str
    interval_minutes: int
    last_run: datetime | None = None
    enabled: bool = True


class AgentStatus(BaseModel):
    name: str = "ARIA"
    holding_name: str = "Aria Vanguard ZHC"
    version: str = "0.2.0"
    uptime_since: datetime
    memory_entries: int
    repertoire_count: int
    watchlist_count: int
    heartbeat_tasks: list[HeartbeatTask]
    last_heartbeat: datetime | None
    zhc_connection: str
    llm_configured: bool = False
    aria_llm_enabled: bool = False
    llm_provider_configured: bool = False
    grounded_mode: bool = True
    long_term_memory: bool = True


class ChatRequest(BaseModel):
    message: str
    visitor_id: str | None = None


class ChatResponse(BaseModel):
    reply: str
    skill_used: SkillName | None = None
    actions_taken: list[str] = Field(default_factory=list)
    zhc_message: ZHCAgentMessage | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class RepertoireCreateRequest(BaseModel):
    name: str
    description: str = ""
    category: str = "projet"
    status: RepertoireItemStatus = RepertoireItemStatus.IDEA
    priority: int = 3
    tags: list[str] = Field(default_factory=list)
    zhc_aligned: bool = True
    notes: str = ""
    entity_type: EntityType = EntityType.SUBSIDIARY
    parent_id: str | None = None
    slug: str | None = None