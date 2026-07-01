export interface AgentStatus {
  name: string
  holding_name?: string
  memory_entries: number
  repertoire_count: number
  watchlist_count: number
  zhc_connection: string
  llm_configured?: boolean
  long_term_memory?: boolean
}

export interface AgentSetup {
  identity: string
  holding: string
  aria_title: string
  holding_structure?: string
  governance_rule?: string
  one_liner?: string
  public_url?: string
  holding_domain?: string
  x_handle?: string
  email?: string
  bio_suggestion?: string
  aria_role?: string
  pillars?: { id?: string; title: string; body: string }[]
  setup_steps?: string[]
  x_api_configured?: boolean
  telegram_configured?: boolean
}

export interface RepertoireItem {
  id: string
  name: string
  description: string
  status: string
  category: string
  priority: number
  zhc_aligned: boolean
  tags: string[]
  notes: string
  revenue_monthly: number
  created_at: string
  updated_at: string
  entity_type?: 'holding' | 'subsidiary' | 'venture'
  parent_id?: string | null
  slug?: string | null
}

export interface HoldingStructure {
  holding: RepertoireItem
  subsidiaries: RepertoireItem[]
  ventures: RepertoireItem[]
  aria_title: string
  holding_tagline?: string
  governance_rule?: string
  subsidiary_label?: string
}