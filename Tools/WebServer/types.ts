// 1:1 Mapping of the provided configuration JSON

export interface LogEntry {
  id: string;
  timestamp: string;
  message: string;
  type: 'error' | 'warning' | 'success' | 'system' | 'info';
}

export interface TaskStats {
  rpm: number;
  tpm: number;
  totalProgress: number;
  completedProgress: number;
  totalTokens: number;
  elapsedTime: number;
  status: 'idle' | 'running' | 'paused' | 'completed' | 'error' | 'stopping';
  currentFile: string;
  successRate?: number;
  errorRate?: number;
}

export interface ChartDataPoint {
  time: string;
  rpm: number;
  tpm: number;
}

export enum TaskType {
  TRANSLATE = 'translate',
  POLISH = 'polish',
  EXPORT = 'export',
  ALL_IN_ONE = 'all_in_one',
  QUEUE = 'queue'
}

export interface QueueTaskItem {
  task_type: number;
  input_path: string;
  output_path?: string;
  profile?: string;
  rules_profile?: string;
  source_lang?: string;
  target_lang?: string;
  project_type?: string;

  // API Overrides
  platform?: string;
  api_url?: string;
  api_key?: string;
  model?: string;

  // Performance Overrides
  threads?: number;
  retry?: number;
  timeout?: number;
  rounds?: number;
  pre_lines?: number;
  lines_limit?: number;
  tokens_limit?: number;
  think_depth?: string;
  thinking_budget?: number;

  status: string;

  // Processing Status (Legacy)
  locked?: boolean;

  // New Accurate Processing Status
  is_actually_processing?: boolean;
  is_processing?: boolean;
  process_start_time?: string;
  last_activity_time?: string;
}

export type ThemeType = 'default' | 'elysia' | 'eden' | 'mobius' | 'pardofelis' | 'griseo' | 'kevin' | 'kalpas' | 'aponia' | 'villv' | 'su' | 'sakura' | 'kosma' | 'hua' | 'herrscher_of_human';

// Payload structure to match ainiee_cli.py arguments
export interface TaskPayload {
  task: TaskType;
  input_path: string;
  output_path?: string;
  project_type?: string;
  resume?: boolean;
  profile?: string;
  rules_profile?: string; // New field
  queue_file?: string;
  run_all_in_one?: boolean;
  manga?: boolean;
  
  // Overrides
  source_lang?: string;
  target_lang?: string;
  threads?: number;
  retry?: number;
  timeout?: number;
  rounds?: number;
  pre_lines?: number;
  
  // Platform Overrides
  platform?: string;
  model?: string;
  api_url?: string;
  api_key?: string;
  failover?: boolean;
  think_depth?: string | number;
  
  // Segmentation
  lines?: number;
  tokens?: number;
}

export interface PlatformConfig {
  tag: string;
  group: 'online' | 'local' | 'custom';
  name: string;
  api_url?: string;
  api_key?: string;
  api_format?: string;
  icon?: string;
  rpm_limit?: number;
  tpm_limit?: number;
  model?: string;
  top_p?: number;
  temperature?: number;
  presence_penalty?: number;
  frequency_penalty?: number;
  think_switch?: boolean;
  think_depth?: string | number;
  thinking_budget?: number;
  auto_complete?: boolean;
  model_datas?: string[];
  region?: string;
  access_key?: string;
  secret_key?: string;
  extra_body?: Record<string, any>;
  key_in_settings?: string[];
}

export interface ResponseCheckSwitch {
  newline_character_count_check: boolean;
  return_to_original_text_check: boolean;
  residual_original_text_check: boolean;
  reply_format_check: boolean;
}

export interface PromptSelection {
  last_selected_id?: string;
  prompt_content?: string;
}

export interface GlossaryItem {
  src: string;
  dst: string;
  info?: string;
  source?: string;
  volume?: number;
  updated_in?: string;
  updated_volume?: number;
  history?: Record<string, any>[];
  [key: string]: any;
}

export interface ExclusionItem {
  markers: string;
  info?: string;
  regex?: string;
  [key: string]: any;
}

export interface ReplacementRuleItem {
  src?: string;
  dst?: string;
  regex?: string;
  info?: string;
  [key: string]: any;
}

export interface CharacterizationItem {
  original_name: string;
  translated_name: string;
  aliases?: string[] | string;
  gender?: string;
  age?: string;
  personality?: string;
  speech_style?: string;
  pronouns?: string;
  speech_quirks?: string;
  additional_info?: string;
  source?: string;
  volume?: number;
  updated_in?: string;
  updated_volume?: number;
  history?: Record<string, any>[];
  [key: string]: any;
}

export interface TranslationExampleItem {
  src: string;
  dst: string;
  [key: string]: any;
}

export interface TermOption {
  dst: string;      // 翻译结果
  info: string;     // 说明（音译/直译/意译/不译）
}

export interface TermItem {
  src: string;           // 原文术语
  type: string;          // 类型（人名/地名/专有名词等）
  analysis_info?: string; // AI分析说明
  options: TermOption[]; // 翻译选项列表
  selected_index: number; // 当前选中的选项索引
  saved: boolean;        // 是否已保存
}

export interface StringContent {
  content: string;
}

export interface ApiSettings {
  translate: string;
  polish: string;
}

export interface TaskRunnerState {
  logs: LogEntry[];
  stats: TaskStats;
  chartData: ChartDataPoint[];
  isRunning: boolean;
  taskType: TaskType;
  customInputPath: string;
  isResuming?: boolean; // New flag for Dashboard navigation
  isMangaMode?: boolean;
  comparison?: {
    source: string;
    translation: string;
  };
}

export interface AppConfig {
  // --- Root / Meta ---
  version?: string; // Injected from version.json
  active_profile?: string; // Injected by backend to identify active profile
  active_rules_profile?: string; // Injected by backend

  // --- Core Settings ---
  label_input_path: string;
  label_output_path: string;
  translated_output_path: string;
  polishing_output_path: string;
  auto_set_output_path: boolean;
  keep_original_encoding: boolean;
  
  source_language: string;
  target_language: string;
  
  translation_project: string;
  polishing_mode_selection?: 'translated_text_polish' | 'source_text_polish';
  manga_ocr_engine?: string;
  manga_detect_engine?: string;
  manga_segment_engine?: string;
  manga_inpaint_engine?: string;
  manga_runtime_device?: string;
  manga_detect_device?: string;
  manga_ocr_device?: string;
  manga_inpaint_device?: string;
  
  // --- Performance ---
  user_thread_counts: number;
  request_timeout: number;
  retry_count: number;
  round_limit: number;
  enable_smart_round_limit: boolean;
  smart_round_limit_multiplier: number;
  enable_rate_limit?: boolean;
  custom_rpm_limit?: number;
  custom_tpm_limit?: number;
  
  // --- Processing ---
  enable_fast_translate: boolean;
  enable_line_breaks: boolean;
  line_breaks_style: number;
  response_conversion_toggle: boolean;
  enable_context_enhancement?: boolean;
  translation_consistency_enhancement?: boolean;
  opencc_preset: string;
  
  // --- Limits ---
  tokens_limit_switch: boolean;
  lines_limit: number;
  chunk_soft_limit_extra_lines?: number;
  line_split_optimization_mode?: 'off' | 'dynamic' | 'tail';
  retry_split_min_lines?: number;
  tokens_limit?: number;
  pre_line_counts: number;

  // --- Switches ---
  pre_translation_switch: boolean;
  post_translation_switch: boolean;
  prompt_dictionary_switch: boolean;
  exclusion_list_switch: boolean;
  characterization_switch: boolean;
  character_recall_switch: boolean;
  character_recall_context_lines: number;
  character_recall_lookahead_lines: number;
  world_building_switch: boolean;
  writing_style_switch: boolean;
  translation_example_switch: boolean;
  few_shot_and_example_switch: boolean;
  auto_process_text_code_segment: boolean;

  // --- Data Containers ---
  pre_translation_data: ReplacementRuleItem[];
  post_translation_data: ReplacementRuleItem[];
  exclusion_list_data: ExclusionItem[];
  prompt_dictionary_data: GlossaryItem[];
  characterization_data: CharacterizationItem[];
  world_building_content: string;
  world_building_history?: Record<string, any>[];
  writing_style_content: string;
  writing_style_history?: Record<string, any>[];
  translation_example_data: TranslationExampleItem[];

  // --- API & Platform ---
  enable_api_failover: boolean;
  backup_apis: string[];
  api_failover_threshold: number;
  
  platforms: Record<string, PlatformConfig>;
  target_platform: string;
  base_url: string;
  model: string;
  api_settings: ApiSettings;

  // --- Prompts ---
  interactive_mode: boolean;
  translation_prompt_selection: PromptSelection;
  polishing_prompt_selection: PromptSelection;

  // --- System ---
  show_detailed_logs: boolean;
  recent_projects: string[];
  interface_language: string;
  enable_github_promotion: boolean;
  enable_cache_backup: boolean;
  cache_save_interval: number;
  enable_auto_restore_ebook: boolean;
  enable_xlsx_conversion: boolean;
  enable_dry_run: boolean;
  enable_auto_proofread?: boolean;
  proofread_report_mode?: 'archive' | 'overwrite';
  proofread_archive_limit?: number;
  proofread_suggestion_mode?: 'proofread' | 'annotation';
  temp_file_limit?: number;
  cache_editor_page_size?: number;
  unlocked_themes?: string[]; // Persist unlocked themes
  response_check_switch: {
    newline_character_count_check: boolean;
    return_to_original_text_check: boolean;
    residual_original_text_check: boolean;
    reply_format_check: boolean;
  };
}
