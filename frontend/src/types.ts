// Shapes returned by the Python API (src/erm_bench/api.py).

export type BucketKey = 'large' | 'mid' | 'small';
export type Availability = 'good' | 'partial' | 'none';
export type RunStatus =
  | 'created' | 'researching' | 'awaiting_scope' | 'analysing' | 'awaiting_findings' | 'exported';

export type Source = { url: string; title: string; cited_text: string };

export type Fact = { id: string; field: string; text: string; sources: Source[] };

export type Profile = {
  legal_name: string; short_name: string; listed: boolean; exchange: string | null;
  ticker: string | null; market_cap_sgd_millions: number | null; market_cap_as_of: string | null;
  bucket: BucketKey | null; headquarters: string | null; sector: string; sector_guide: string;
  business_description: string; segments: string[]; countries: string[]; main_markets: string[];
  total_assets: string | null; revenue: string | null; ebitda: string | null;
  employees: string | null; parent: string | null; related_listed_entities: string[];
  latest_annual_report_year: number | null; facts: Fact[]; warnings: string[];
};

export type PeerCandidate = {
  id: string; name: string; legal_name: string; listed: boolean; exchange: string | null;
  ticker: string | null; market_cap_sgd_millions: number | null; market_cap_as_of: string | null;
  bucket: BucketKey | null; headquarters: string | null; segments: string[];
  operating_countries: string | null; main_markets: string[]; total_assets: string | null;
  revenue: string | null; ebitda: string | null; employees: string | null;
  related_entity_warning: string | null; rationale: string; key_limitation: string;
  recommended: boolean; selected: boolean; added_by: 'ai' | 'consultant'; sources: Source[];
  warnings: string[];
};

export type PeerSet = {
  bucket_filter: BucketKey | null; peer_count: number; scan_notes: string;
  candidates: PeerCandidate[];
};

export type DocStatus =
  'found' | 'ok' | 'check_failed' | 'download_failed' | 'not_pdf' | 'uploaded' | 'na';

export type Document = {
  id: string; company_id: string;
  doc_type: 'annual_report' | 'sustainability_report' | 'corporate_governance_report' | 'other';
  title: string; fiscal_year: number | null; url: string | null; status: DocStatus;
  sha256: string | null; page_count: number | null;
  checks: { company_match: boolean | null; year_found: number | null; year_match: boolean | null;
            has_risk_section: boolean | null; low_text_pages: number };
  risk_pages: number[]; note: string;
};

export type Job = {
  kind: string; running: boolean; started_at: string; finished_at: string | null;
  progress: { at: string; message: string }[]; error: string | null;
};

export type Theme = {
  key: string; name: string; category: string; category_name: string; description: string;
  removed: boolean;
};

export type Risk = {
  id: string; company_id: string; theme_key: string | null; title: string; summary: string;
  company_category: string | null; quote: string; page: number; doc_id: string;
  doc_title: string | null; url: string | null; verified: boolean; score: number; note: string;
  rejected: boolean; counts: boolean;
};

export type ContextRisk = {
  id: string; title: string; category: string; driver_fact_ids: string[]; driver: string;
  consequence: string; affected: string; theme_key: string | null; warnings: string[];
  driver_facts: { id: string; text: string }[];
};

export type Findings = {
  companies: { id: string; name: string }[];
  client_disclosed: boolean;
  stats: { risks: number; verified: number; unverified: number; rejected: number; themes: number;
           context_risks: number; gaps: number };
  rows: { theme: Theme; context_risk_ids: string[]; cells: Record<string, string[]> }[];
  removed_themes: Theme[];
  gaps: { type: string; label: string; theme_key: string; theme_name: string; category: string;
          detail: string }[];
  structure: {
    columns: { company_id: string; has_structure: boolean; note: string;
               categories: { id: string; name: string; sub_categories: string[];
                             taxonomy_key: string | null; mapping_note: string; quote: string;
                             page: number }[] }[];
    by_taxonomy: Record<string, Record<string, string[]>>;
    observations: string[];
  };
  risks: Risk[];
  context_risks: ContextRisk[];
  extractions: { company_id: string; doc_id: string | null; note: string;
                 has_risk_disclosure: boolean }[];
  remarks: string[];
};

export type ChatEntry = { at: string; role: 'user' | 'assistant'; text: string };

/** Estimated from reported token counts and web searches; caps are per run (method.yaml). */
export type Spend = { usd: number; max_usd: number | null; searches: number; max_searches: number | null;
                      input_tokens: number; output_tokens: number };

export type RunState = {
  run: { run_id: string; status: RunStatus; created_at: string; updated_at: string | null;
         method: string; setup_version: string; provider: string | null; model: string };
  request: { client_name: string; reporting_year: number | null; peer_count: number;
             market_cap_bucket: BucketKey | null; sector: string | null; focus_regions: string[];
             notes: string };
  job: Job | null;
  chat: ChatEntry[];
  exports: string[];
  spend: Spend;
  profile: Profile | null;
  peers: PeerSet | null;
  documents: Document[];
  companies: Record<string, { availability: Availability; primary_doc_id: string | null }>;
  scope: { locked_at: string; peer_ids: string[]; bucket_filter: BucketKey | null;
           documents: Record<string, string | null> } | null;
  findings: Findings | null;
};

export type RunSummary = {
  run_id: string; status: RunStatus; created_at: string; updated_at: string | null;
  client: string; legal_name: string | null; listing: string | null;
  reporting_year: number | null; peers: string[]; model: string | null; busy: boolean; usd: number;
};

export type Setup = {
  name: string; version: string; provider: string; model: string; bucket_currency: string;
  bucket_status: string | null;
  buckets: { key: BucketKey; label: string; min: number | null; max: number | null;
             description: string }[];
  taxonomy: { key: string; name: string; description: string; examples: string[] }[];
  default_peer_count: number;
  time_limits: { research?: number; analysis?: number; per_call?: number };
  verify: { min_score: number; min_quote_chars: number };
};
