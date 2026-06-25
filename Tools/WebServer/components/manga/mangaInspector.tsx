import React, { useEffect, useMemo, useState } from 'react';
import { Activity, AlertTriangle, ChevronDown, ExternalLink, FileJson, GitCompareArrows, ImageIcon, Link2, LocateFixed, PackageCheck, PlayCircle, RotateCcw, Search, Square, SquareStack, Trash2 } from 'lucide-react';

import { useI18n } from '../../contexts/I18nContext';
import { MangaPageDetail, MangaQualityIssue, MangaRuntimeValidationDiffResult, MangaRuntimeValidationHistoryItem, MangaRuntimeValidationResult, MangaTextBlock } from '../../types/manga';
import { MangaActiveJobSummary, MangaBlockDraft, MangaCanvasRuntimeBox, MangaEngineCard, translateMangaEnum } from './shared';

export interface MangaInspectorProps {
  page: MangaPageDetail | null;
  activeBlock: MangaTextBlock | null;
  activeBlockDraft: MangaBlockDraft | null;
  activeJob: MangaActiveJobSummary | null;
  engineCards: MangaEngineCard[];
  runtimeValidation: MangaRuntimeValidationResult | null;
  runtimeValidationHistory: MangaRuntimeValidationHistoryItem[];
  runtimeValidationDiff: MangaRuntimeValidationDiffResult | null;
  activeRuntimeStage: string;
  busyAction: string;
  hasProject: boolean;
  canCancelRuntimeValidation: boolean;
  dirtyBlockCount: number;
  activeBlockDirty: boolean;
  onSelectRuntimeStage: (stage: string) => void;
  onLoadRuntimeValidationHistory: (runId: string) => void;
  onDiffRuntimeValidationHistory: (beforeRunId: string, afterRunId: string) => void;
  onDeleteRuntimeValidationHistory: (runId: string) => void;
  onCancelRuntimeValidation: () => void;
  onRetryRuntimeValidationStage: (stage: string) => void;
  onValidateRuntime: () => void;
  onDownloadModel: (modelId: string) => void;
  onFocusRuntimeBox: (box: MangaCanvasRuntimeBox) => void;
}

interface OcrSeedRow {
  seedId: string;
  sourceText: string;
  confidence: number | null;
  direction: string;
  bbox: number[];
  searchText: string;
}

interface DetectRegionRow {
  regionId: string;
  kind: string;
  score: number | null;
  bbox: number[];
  searchText: string;
}

interface DetectOcrMatchRow {
  region: DetectRegionRow;
  seeds: Array<{
    seed: OcrSeedRow;
    overlap: number;
  }>;
}

interface InspectorSectionProps {
  title: string;
  meta?: React.ReactNode;
  defaultOpen?: boolean;
  children: React.ReactNode;
}

const InspectorSection: React.FC<InspectorSectionProps> = ({
  title,
  meta,
  defaultOpen = false,
  children,
}) => (
  <details
    open={defaultOpen}
    className="group rounded-xl border border-slate-800/85 bg-slate-900/48 shadow-[0_16px_42px_rgba(2,6,23,0.18)]"
  >
    <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-2.5">
      <div className="min-w-0">
        <div className="truncate text-xs font-bold uppercase tracking-[0.18em] text-slate-400">{title}</div>
        {meta && <div className="mt-0.5 truncate text-[11px] text-slate-500">{meta}</div>}
      </div>
      <ChevronDown size={15} className="shrink-0 text-slate-500 transition-transform group-open:rotate-180" />
    </summary>
    <div className="border-t border-slate-800/75 px-3 py-3">{children}</div>
  </details>
);

const MetricTile: React.FC<{ label: string; value: React.ReactNode; tone?: string }> = ({
  label,
  value,
  tone = 'text-slate-100',
}) => (
  <div className="rounded-lg border border-slate-800 bg-slate-950/55 px-2.5 py-2">
    <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">{label}</div>
    <div className={`mt-1 text-sm font-bold ${tone}`}>{value}</div>
  </div>
);

const StatusPill: React.FC<{ children: React.ReactNode; tone?: 'cyan' | 'emerald' | 'amber' | 'rose' | 'slate' }> = ({
  children,
  tone = 'slate',
}) => {
  const classes = {
    cyan: 'border-cyan-300/25 bg-cyan-300/10 text-cyan-100',
    emerald: 'border-emerald-300/25 bg-emerald-300/10 text-emerald-100',
    amber: 'border-amber-300/25 bg-amber-300/10 text-amber-100',
    rose: 'border-rose-300/25 bg-rose-300/10 text-rose-100',
    slate: 'border-slate-700/80 bg-slate-800/60 text-slate-200',
  };

  return (
    <span className={`shrink-0 rounded-full border px-2 py-1 text-[10px] font-bold uppercase tracking-[0.13em] ${classes[tone]}`}>
      {children}
    </span>
  );
};

const clampPercent = (value: number) => Math.max(0, Math.min(100, value || 0));

const formatDateTime = (value: string) => {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
};

const getExecutionTone = (mode: string): 'cyan' | 'emerald' | 'amber' | 'rose' | 'slate' => {
  if (mode === 'configured_runtime') return 'emerald';
  if (mode === 'fallback_runtime') return 'cyan';
  if (mode === 'failed') return 'rose';
  return 'amber';
};

const isImageArtifact = (path: string) => /\.(png|jpe?g|webp|gif|bmp)$/i.test(path.split('?')[0] || '');

const formatArtifactText = (value: string) => {
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
};

const truncateArtifactText = (value: string) => (
  value.length > 16000 ? `${value.slice(0, 16000)}\n...` : value
);

const normalizeBoxValues = (value: unknown) => {
  if (!Array.isArray(value) || value.length < 4) return [];
  const bbox = value.slice(0, 4).map((item) => Number(item));
  return bbox.some((item) => Number.isNaN(item)) ? [] : bbox;
};

const normalizeSeedBbox = (record: Record<string, any>) => {
  const bbox = Array.isArray(record.bbox)
    ? record.bbox
    : Array.isArray(record.inner_bbox)
      ? record.inner_bbox
      : Array.isArray(record.component_bbox)
        ? record.component_bbox
        : [];
  return normalizeBoxValues(bbox);
};

const normalizeOcrSeeds = (records: unknown): OcrSeedRow[] => {
  if (!Array.isArray(records)) return [];
  return records.flatMap((record, index) => {
    if (!record || typeof record !== 'object') return [];
    const data = record as Record<string, any>;
    const bbox = normalizeSeedBbox(data);
    if (bbox.length < 4) return [];
    const seedId = String(data.seed_id || data.id || `seed_${index + 1}`);
    const sourceText = String(data.source_text || data.text || '');
    const direction = String(data.direction || data.source_direction || '');
    const confidenceValue = Number(data.confidence ?? data.ocr_confidence);
    const confidence = Number.isFinite(confidenceValue) ? confidenceValue : null;
    return [{
      seedId,
      sourceText,
      confidence,
      direction,
      bbox,
      searchText: [seedId, sourceText, direction, bbox.join(',')].join(' ').toLowerCase(),
    }];
  });
};

const normalizeDetectRegions = (records: unknown): DetectRegionRow[] => {
  if (!Array.isArray(records)) return [];
  return records.flatMap((record, index) => {
    if (!record || typeof record !== 'object') return [];
    const data = record as Record<string, any>;
    const bbox = normalizeBoxValues(data.bbox);
    if (bbox.length < 4) return [];
    const regionId = String(data.region_id || data.id || `region_${index + 1}`);
    const kind = String(data.kind || data.type || 'text');
    const scoreValue = Number(data.score ?? data.confidence);
    const score = Number.isFinite(scoreValue) ? scoreValue : null;
    return [{
      regionId,
      kind,
      score,
      bbox,
      searchText: [regionId, kind, bbox.join(',')].join(' ').toLowerCase(),
    }];
  });
};

const bboxArea = (bbox: number[]) => (
  Math.max(0, (bbox[2] || 0) - (bbox[0] || 0)) * Math.max(0, (bbox[3] || 0) - (bbox[1] || 0))
);

const bboxIntersectionArea = (left: number[], right: number[]) => {
  const x1 = Math.max(left[0] || 0, right[0] || 0);
  const y1 = Math.max(left[1] || 0, right[1] || 0);
  const x2 = Math.min(left[2] || 0, right[2] || 0);
  const y2 = Math.min(left[3] || 0, right[3] || 0);
  return Math.max(0, x2 - x1) * Math.max(0, y2 - y1);
};

const seedKey = (seed: OcrSeedRow) => `${seed.seedId}:${seed.bbox.map((value) => Math.round(value)).join(',')}`;

const buildDetectOcrMatches = (regions: DetectRegionRow[], seeds: OcrSeedRow[]) => {
  const matchedSeedKeys = new Set<string>();
  const rows: DetectOcrMatchRow[] = regions.map((region) => {
    const regionArea = Math.max(1, bboxArea(region.bbox));
    const matchedSeeds = seeds
      .map((seed) => {
        const intersection = bboxIntersectionArea(region.bbox, seed.bbox);
        const seedArea = Math.max(1, bboxArea(seed.bbox));
        const union = Math.max(1, regionArea + seedArea - intersection);
        const seedCoverage = intersection / seedArea;
        const regionCoverage = intersection / regionArea;
        const iou = intersection / union;
        return {
          seed,
          overlap: Math.max(seedCoverage, regionCoverage, iou),
        };
      })
      .filter((match) => match.overlap >= 0.2)
      .sort((left, right) => right.overlap - left.overlap);

    matchedSeeds.forEach((match) => matchedSeedKeys.add(seedKey(match.seed)));
    return { region, seeds: matchedSeeds };
  });

  return {
    rows,
    unmatchedSeeds: seeds.filter((seed) => !matchedSeedKeys.has(seedKey(seed))),
    emptyRegions: rows.filter((row) => row.seeds.length === 0).map((row) => row.region),
  };
};

const formatConfidence = (confidence: number | null) => (
  confidence === null ? '-' : confidence.toFixed(3)
);

const formatScore = (score: number | null) => (
  score === null ? '-' : score.toFixed(3)
);

const formatDiffValue = (value: any) => {
  if (value === undefined || value === null || value === '') return '-';
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(3);
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
};

const extractRuntimeRunId = (outputDir: string) => (
  outputDir.replace(/\\/g, '/').split('/').filter(Boolean).pop() || ''
);

const formatMetricValue = (value: any, digits = 2) => {
  if (value === undefined || value === null || value === '') return '-';
  const numberValue = Number(value);
  if (Number.isFinite(numberValue)) {
    return Number.isInteger(numberValue) ? String(numberValue) : numberValue.toFixed(digits);
  }
  return String(value);
};

const extractFlagValue = (flags: string[] | undefined, prefix: string) => {
  const match = (flags || []).find((flag) => String(flag).startsWith(prefix));
  return match ? match.slice(prefix.length) : '';
};

const getRenderLayoutPlans = (qualityGate: any) => {
  const plans = qualityGate?.stage_modes?.render?.layout_plans;
  return Array.isArray(plans) ? plans : [];
};

const formatQualityIssue = (
  issue: MangaQualityIssue,
  t: (key: string, ...args: any[]) => string,
) => {
  const key = String(issue?.message_key || '');
  if (key) {
    const args = Array.isArray(issue?.message_args) ? issue.message_args : [];
    const translated = t(key, ...args);
    if (translated !== key) return translated;
  }
  return String(issue?.message || issue?.code || '').trim();
};

const formatLayoutDiagnostic = (diagnostic: any) => {
  if (!diagnostic || typeof diagnostic !== 'object') return '';
  const message = String(diagnostic.message || diagnostic.code || '').trim();
  const warnings = Array.isArray(diagnostic.warnings)
    ? diagnostic.warnings.map((warning: any) => String(warning || '').trim()).filter(Boolean).join(', ')
    : '';
  return warnings ? `${message}: ${warnings}` : message;
};

const formatI18nPayload = (
  key: string | undefined,
  args: any[] | undefined,
  fallback: string,
  t: (key: string, ...args: any[]) => string,
) => {
  if (!key) return fallback;
  const translated = t(key, ...(Array.isArray(args) ? args : []));
  return translated !== key ? translated : fallback;
};

export const MangaInspector: React.FC<MangaInspectorProps> = ({
  page,
  activeBlock,
  activeBlockDraft,
  activeJob,
  engineCards,
  runtimeValidation,
  runtimeValidationHistory,
  runtimeValidationDiff,
  activeRuntimeStage,
  busyAction,
  hasProject,
  canCancelRuntimeValidation,
  dirtyBlockCount,
  activeBlockDirty,
  onSelectRuntimeStage,
  onLoadRuntimeValidationHistory,
  onDiffRuntimeValidationHistory,
  onDeleteRuntimeValidationHistory,
  onCancelRuntimeValidation,
  onRetryRuntimeValidationStage,
  onValidateRuntime,
  onDownloadModel,
  onFocusRuntimeBox,
}) => {
  const { t } = useI18n();
  const blockWidth = activeBlock ? Math.max(0, activeBlock.bbox[2] - activeBlock.bbox[0]) : 0;
  const blockHeight = activeBlock ? Math.max(0, activeBlock.bbox[3] - activeBlock.bbox[1]) : 0;
  const fontFamily = activeBlockDraft?.font_family ?? activeBlock?.style.font_family ?? '-';
  const fontSize = activeBlockDraft?.font_size ?? activeBlock?.style.font_size ?? 0;
  const lineSpacing = activeBlockDraft?.line_spacing ?? activeBlock?.style.line_spacing ?? 0;
  const fill = activeBlockDraft?.fill ?? activeBlock?.style.fill ?? '#000000';
  const strokeColor = activeBlockDraft?.stroke_color ?? activeBlock?.style.stroke_color ?? '#ffffff';
  const strokeWidth = activeBlockDraft?.stroke_width ?? activeBlock?.style.stroke_width ?? 0;
  const sourcePreview = activeBlockDraft?.source_text ?? activeBlock?.source_text ?? '';
  const translationPreview = activeBlockDraft?.translation ?? activeBlock?.translation ?? '';
  const isBusy = Boolean(busyAction);
  const isRuntimeBusy = busyAction === 'validate runtime';
  const isRuntimeStageRetryBusy = busyAction === 'retry runtime validation stage';
  const canValidateRuntime = Boolean(hasProject && page && !isBusy);
  const qualityGate = page?.quality_gate || null;
  const qualityIssues = (qualityGate?.issues || []).filter((issue) => issue.blocks_final);
  const missingEngineCount = engineCards.filter((card) => !card.available).length;
  const missingPackages = engineCards
    .flatMap((card) => card.packages)
    .filter((pkg) => !pkg.available);
  const selectedRuntimeStage = runtimeValidation?.stages.find((stage) => stage.stage === activeRuntimeStage) || runtimeValidation?.stages[0] || null;
  const selectedStageArtifacts = useMemo(() => (
    Object.entries(selectedRuntimeStage?.artifacts || {}).map(([key, value]) => {
      const path = typeof value === 'string' ? value : '';
      return {
      key,
      path,
      url: selectedRuntimeStage?.artifact_urls?.[key] || '',
      isImage: isImageArtifact(path),
      };
    })
  ), [selectedRuntimeStage]);
  const [activeArtifactKey, setActiveArtifactKey] = useState('');
  const activeArtifact = selectedStageArtifacts.find((artifact) => artifact.key === activeArtifactKey) || selectedStageArtifacts[0] || null;
  const [artifactText, setArtifactText] = useState('');
  const [artifactError, setArtifactError] = useState('');
  const [artifactLoading, setArtifactLoading] = useState(false);
  const [seedSearch, setSeedSearch] = useState('');
  const [lowConfidenceOnly, setLowConfidenceOnly] = useState(false);
  const [confidenceThreshold, setConfidenceThreshold] = useState(0.85);
  const ocrStage = useMemo(() => (
    runtimeValidation?.stages.find((stage) => stage.stage === 'ocr') || null
  ), [runtimeValidation]);
  const detectStage = useMemo(() => (
    runtimeValidation?.stages.find((stage) => stage.stage === 'detect') || null
  ), [runtimeValidation]);
  const ocrSeeds = useMemo(() => normalizeOcrSeeds(ocrStage?.metrics?.seeds), [ocrStage]);
  const detectRegions = useMemo(() => normalizeDetectRegions(detectStage?.metrics?.text_regions), [detectStage]);
  const detectOcrMatches = useMemo(() => buildDetectOcrMatches(detectRegions, ocrSeeds), [detectRegions, ocrSeeds]);
  const visibleRuntimeHistory = useMemo(() => runtimeValidationHistory.slice(0, 6), [runtimeValidationHistory]);
  const activeRuntimeRunId = useMemo(() => extractRuntimeRunId(runtimeValidation?.output_dir || ''), [runtimeValidation?.output_dir]);
  const activeBlockSourceMetrics = activeBlock?.source_metrics || {};
  const activeBlockRenderPlan = useMemo(() => {
    if (!activeBlock) return null;
    return getRenderLayoutPlans(qualityGate).find((plan) => plan?.block_id === activeBlock.block_id) || null;
  }, [activeBlock, qualityGate]);
  const activeBlockRenderWarnings = Array.isArray(activeBlockRenderPlan?.warnings) ? activeBlockRenderPlan.warnings : [];
  const activeBlockRenderDiagnostics = Array.isArray(activeBlockRenderPlan?.diagnostics) ? activeBlockRenderPlan.diagnostics : [];
  const activeBlockDiagnosticTexts = activeBlockRenderDiagnostics.map(formatLayoutDiagnostic).filter(Boolean);
  const sourceCharSize = activeBlockSourceMetrics.source_char_size_px ?? activeBlockRenderPlan?.source_char_size_px;
  const sourceCharConfidence = activeBlockSourceMetrics.source_char_size_confidence ?? activeBlockRenderPlan?.source_char_size_confidence;
  const sourceCharMethod = activeBlockSourceMetrics.source_char_size_method || '-';
  const initialFontSize = activeBlockSourceMetrics.initial_font_size ?? activeBlockRenderPlan?.initial_font_size;
  const finalFontSize = activeBlockRenderPlan?.font_size ?? fontSize;
  const fontScaleRatio = activeBlockRenderPlan?.font_scale_ratio || '';
  const sourceSeedCount = activeBlockSourceMetrics.source_seed_count ?? extractFlagValue(activeBlock?.flags, 'seed_count:');
  const sourceSeedIds = Array.isArray(activeBlockSourceMetrics.source_seed_ids) ? activeBlockSourceMetrics.source_seed_ids : [];
  const sourceBbox = Array.isArray(activeBlockSourceMetrics.source_bbox) ? activeBlockSourceMetrics.source_bbox : [];
  const sourceLayoutBbox = Array.isArray(activeBlockSourceMetrics.source_layout_bbox) ? activeBlockSourceMetrics.source_layout_bbox : [];
  const sourceSizeSplit = Boolean(activeBlock?.flags?.includes('source_size_split'));
  const filteredOcrSeeds = useMemo(() => {
    const query = seedSearch.trim().toLowerCase();
    return ocrSeeds.filter((seed) => {
      if (query && !seed.searchText.includes(query)) return false;
      if (lowConfidenceOnly && (seed.confidence === null || seed.confidence >= confidenceThreshold)) return false;
      return true;
    });
  }, [confidenceThreshold, lowConfidenceOnly, ocrSeeds, seedSearch]);
  const focusDetectRegion = (region: DetectRegionRow) => {
    onSelectRuntimeStage('detect');
    onFocusRuntimeBox({
      bbox: region.bbox,
      label: `${region.regionId} · ${region.kind}`,
      tone: 'cyan',
    });
  };
  const focusOcrSeed = (seed: OcrSeedRow) => {
    onSelectRuntimeStage('ocr');
    onFocusRuntimeBox({
      bbox: seed.bbox,
      label: seed.sourceText || seed.seedId,
      tone: 'amber',
    });
  };
  const getHistoryDiffTarget = (item: MangaRuntimeValidationHistoryItem, index: number) => {
    if (activeRuntimeRunId && activeRuntimeRunId !== item.run_id) return activeRuntimeRunId;
    return visibleRuntimeHistory[index + 1]?.run_id || visibleRuntimeHistory[index - 1]?.run_id || '';
  };

  useEffect(() => {
    setActiveArtifactKey('');
    setSeedSearch('');
    setLowConfidenceOnly(false);
  }, [activeRuntimeStage, runtimeValidation?.created_at]);

  useEffect(() => {
    setArtifactText('');
    setArtifactError('');
    if (!activeArtifact?.url || activeArtifact.isImage) {
      setArtifactLoading(false);
      return;
    }

    let cancelled = false;
    setArtifactLoading(true);
    fetch(activeArtifact.url)
      .then((response) => {
        if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
        return response.text();
      })
      .then((text) => {
        if (!cancelled) setArtifactText(truncateArtifactText(formatArtifactText(text)));
      })
      .catch((error: any) => {
        if (!cancelled) setArtifactError(error?.message || String(error));
      })
      .finally(() => {
        if (!cancelled) setArtifactLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [activeArtifact]);

  return (
    <div className="space-y-3 px-3 py-3">
      <div className="rounded-2xl border border-cyan-300/10 bg-[radial-gradient(circle_at_top_left,rgba(34,211,238,0.16),transparent_45%),rgba(15,23,42,0.72)] px-3 py-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-xs font-bold uppercase tracking-[0.24em] text-cyan-200/85">{t('manga_inspector_title')}</div>
            <div className="mt-1 truncate text-sm font-semibold text-slate-100">
              {page ? t('manga_page_summary', page.index, page.width, page.height) : t('manga_no_page_selected')}
            </div>
          </div>
          <StatusPill tone={page ? 'cyan' : 'slate'}>
            {page ? translateMangaEnum('manga_state', page.status, t) : t('manga_idle')}
          </StatusPill>
        </div>
        {activeBlock && (
          <div className="mt-2 truncate text-xs text-slate-500">
            {t('manga_active_block_inline')} <span className="font-semibold text-slate-200">{activeBlock.block_id}</span>
            {activeBlockDirty && <span className="ml-2 text-amber-200">{t('manga_unsaved')}</span>}
          </div>
        )}
        {dirtyBlockCount > 0 && (
          <div className="mt-2 rounded-lg border border-amber-300/20 bg-amber-300/10 px-2.5 py-1.5 text-xs text-amber-100">
            {t('manga_dirty_block_count', dirtyBlockCount)}
          </div>
        )}
      </div>

      {qualityGate?.blocked_from_final && (
        <section className="rounded-xl border border-amber-300/25 bg-amber-300/10 px-3 py-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.18em] text-amber-100">
                <AlertTriangle size={14} />
                {t('manga_quality_gate_title')}
              </div>
              <div className="mt-1 text-sm font-semibold text-amber-50">
                {t('manga_quality_gate_draft_only')}
              </div>
            </div>
            <StatusPill tone="amber">
              {t('manga_quality_gate_issue_count', qualityGate.issue_count || qualityIssues.length)}
            </StatusPill>
          </div>

          <div className="mt-3 space-y-1.5">
            {qualityIssues.slice(0, 6).map((issue) => (
              <div key={`${issue.stage}-${issue.code}`} className="rounded-lg border border-amber-300/15 bg-slate-950/35 px-2.5 py-2 text-xs text-amber-50">
                <div className="font-semibold">{formatQualityIssue(issue, t)}</div>
                <div className="mt-0.5 text-[11px] uppercase tracking-[0.12em] text-amber-100/55">{issue.stage || issue.code}</div>
              </div>
            ))}
          </div>

          <div className="mt-3 grid gap-1.5 text-[11px] text-amber-100/70">
            {qualityGate.draft_rendered_path && (
              <div className="truncate" title={qualityGate.draft_rendered_path}>
                {t('manga_quality_gate_draft_path', qualityGate.draft_rendered_path)}
              </div>
            )}
            {qualityGate.artifact_path && (
              <div className="truncate" title={qualityGate.artifact_path}>
                {t('manga_quality_gate_report_path', qualityGate.artifact_path)}
              </div>
            )}
            {qualityGate.final_page_path && (
              <div className="truncate" title={qualityGate.final_page_path}>
                {t('manga_quality_gate_final_path', qualityGate.final_page_path)}
              </div>
            )}
          </div>
        </section>
      )}

      <section className="rounded-xl border border-slate-800/85 bg-slate-900/55 px-3 py-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.18em] text-slate-400">
              <Activity size={14} className="text-cyan-300" />
              {t('manga_panel_runtime_validation')}
            </div>
            <div className="mt-1 text-[11px] text-slate-500">
              {runtimeValidation
                ? t('manga_last_checked', formatDateTime(runtimeValidation.created_at))
                : t('manga_runtime_validation_empty')}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            {canCancelRuntimeValidation && (
              <button
                type="button"
                onClick={onCancelRuntimeValidation}
                className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-rose-300/25 bg-rose-300/10 px-2.5 text-[11px] font-bold text-rose-100 transition-colors hover:border-rose-200"
              >
                <Square size={12} fill="currentColor" />
                {t('manga_action_cancel_runtime_validation')}
              </button>
            )}
            <button
              type="button"
              onClick={onValidateRuntime}
              disabled={!canValidateRuntime}
              className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-cyan-300/25 bg-cyan-300/10 px-2.5 text-[11px] font-bold text-cyan-100 transition-colors hover:border-cyan-200 disabled:cursor-not-allowed disabled:opacity-45"
            >
              {isRuntimeBusy ? <Activity size={13} className="animate-pulse" /> : <PlayCircle size={13} />}
              {t('manga_action_validate_runtime')}
            </button>
          </div>
        </div>

        {runtimeValidation && (
          <div className="mt-3 space-y-3">
            <div className="flex items-center justify-between gap-2">
              <div className="text-sm font-semibold text-slate-100">{t('manga_page_short')} {runtimeValidation.page_index}</div>
              <StatusPill tone={runtimeValidation.ok ? 'emerald' : 'amber'}>
                {runtimeValidation.ok ? t('manga_complete') : t('manga_needs_review')}
              </StatusPill>
            </div>
            <div className="grid grid-cols-3 gap-2">
              <MetricTile label={t('manga_summary_runtime')} value={runtimeValidation.summary?.runtime_stage_count ?? 0} tone="text-emerald-200" />
              <MetricTile label={t('manga_summary_fallback')} value={runtimeValidation.summary?.fallback_stage_count ?? 0} tone="text-amber-200" />
              <MetricTile label={t('manga_summary_seeds')} value={runtimeValidation.summary?.seed_count ?? 0} tone="text-cyan-200" />
            </div>
            <details className="group rounded-lg border border-slate-800 bg-slate-950/38">
              <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-2.5 py-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">
                {t('manga_artifact_dir')}
                <ChevronDown size={14} className="transition-transform group-open:rotate-180" />
              </summary>
              <div className="border-t border-slate-800 px-2.5 py-2 text-xs text-slate-400 break-all">{runtimeValidation.output_dir}</div>
            </details>
          </div>
        )}

        {missingPackages.length > 0 && (
          <div className="mt-3 rounded-lg border border-amber-300/20 bg-amber-300/10 px-3 py-2">
            <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-amber-100">
              {t('manga_runtime_missing_models')}
            </div>
            <div className="mt-2 space-y-1.5">
              {missingPackages.map((pkg) => {
                const isPreparing = busyAction === `download model:${pkg.modelId}`;
                return (
                  <div key={pkg.modelId} className="flex items-center justify-between gap-2 text-xs">
                    <span className="min-w-0 truncate text-amber-50" title={pkg.label}>{pkg.label}</span>
                    <button
                      type="button"
                      onClick={() => onDownloadModel(pkg.modelId)}
                      disabled={Boolean(busyAction)}
                      className="shrink-0 rounded-md border border-amber-200/25 bg-amber-200/10 px-2 py-1 text-[10px] font-bold uppercase tracking-[0.12em] text-amber-50 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {isPreparing ? t('manga_preparing') : t('manga_prepare')}
                    </button>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {runtimeValidationHistory.length > 0 && (
          <details className="group mt-3 rounded-lg border border-slate-800 bg-slate-950/38">
            <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-2.5 py-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">
              {t('manga_validation_history')}
              <ChevronDown size={14} className="transition-transform group-open:rotate-180" />
            </summary>
            <div className="space-y-2 border-t border-slate-800 px-2.5 py-2">
              <div className="grid gap-1.5">
                {visibleRuntimeHistory.map((item, index) => {
                  const diffTarget = getHistoryDiffTarget(item, index);
                  return (
                    <div
                      key={item.run_id}
                      className={`grid gap-1 rounded-md border px-2.5 py-2 text-xs transition-colors ${
                        activeRuntimeRunId === item.run_id
                          ? 'border-cyan-300/55 bg-cyan-300/10'
                          : 'border-slate-800 bg-slate-900/65'
                      }`}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <button
                          type="button"
                          onClick={() => onLoadRuntimeValidationHistory(item.run_id)}
                          disabled={Boolean(busyAction)}
                          className="min-w-0 flex-1 text-left disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          <span className="flex items-center justify-between gap-2">
                            <span className="truncate font-semibold text-slate-200">{formatDateTime(item.created_at) || item.run_id}</span>
                            <StatusPill tone={item.ok ? 'emerald' : 'amber'}>
                              {item.ok ? t('manga_complete') : t('manga_needs_review')}
                            </StatusPill>
                          </span>
                          <span className="mt-1 block text-slate-500">
                            {t('manga_history_summary', item.runtime_stage_count, item.fallback_stage_count, item.seed_count)}
                          </span>
                        </button>
                        <div className="flex shrink-0 items-center gap-1">
                          <button
                            type="button"
                            onClick={() => diffTarget && onDiffRuntimeValidationHistory(item.run_id, diffTarget)}
                            disabled={Boolean(busyAction) || !diffTarget}
                            title={t('manga_diff_history')}
                            className="flex h-7 w-7 items-center justify-center rounded-md border border-slate-800 bg-slate-950/70 text-slate-400 transition-colors hover:border-cyan-300/40 hover:text-cyan-100 disabled:cursor-not-allowed disabled:opacity-40"
                          >
                            <GitCompareArrows size={13} />
                          </button>
                          <button
                            type="button"
                            onClick={() => onDeleteRuntimeValidationHistory(item.run_id)}
                            disabled={Boolean(busyAction)}
                            title={t('manga_delete_history')}
                            className="flex h-7 w-7 items-center justify-center rounded-md border border-slate-800 bg-slate-950/70 text-slate-500 transition-colors hover:border-rose-300/35 hover:text-rose-200 disabled:cursor-not-allowed disabled:opacity-40"
                          >
                            <Trash2 size={13} />
                          </button>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>

              {runtimeValidationDiff && (
                <div className="rounded-md border border-slate-800 bg-slate-900/58 px-2.5 py-2">
                  <div className="flex items-center justify-between gap-2">
                    <div className="min-w-0 text-[11px] font-semibold uppercase tracking-[0.14em] text-cyan-100">
                      {t('manga_runtime_diff')}
                    </div>
                    <span className="shrink-0 text-[10px] text-slate-500">
                      {runtimeValidationDiff.before_run_id}{' -> '}{runtimeValidationDiff.after_run_id}
                    </span>
                  </div>
                  {runtimeValidationDiff.summary_changes.length === 0 && runtimeValidationDiff.stage_changes.length === 0 ? (
                    <div className="mt-2 text-xs text-slate-500">{t('manga_runtime_diff_empty')}</div>
                  ) : (
                    <div className="mt-2 space-y-2">
                      {runtimeValidationDiff.summary_changes.length > 0 && (
                        <div>
                          <div className="mb-1 text-[10px] font-bold uppercase tracking-[0.14em] text-slate-500">{t('manga_runtime_diff_summary')}</div>
                          <div className="space-y-1">
                            {runtimeValidationDiff.summary_changes.slice(0, 8).map((change) => (
                              <div key={change.key} className="grid gap-0.5 rounded border border-slate-800/80 bg-slate-950/45 px-2 py-1.5 text-[11px]">
                                <span className="font-semibold text-slate-300">{change.key}</span>
                                <span className="truncate text-slate-500" title={`${formatDiffValue(change.before)} -> ${formatDiffValue(change.after)}`}>
                                  {formatDiffValue(change.before)}{' -> '}{formatDiffValue(change.after)}
                                </span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                      {runtimeValidationDiff.stage_changes.length > 0 && (
                        <div>
                          <div className="mb-1 text-[10px] font-bold uppercase tracking-[0.14em] text-slate-500">{t('manga_runtime_diff_stages')}</div>
                          <div className="space-y-1">
                            {runtimeValidationDiff.stage_changes.slice(0, 6).map((stage) => (
                              <div key={stage.stage} className="rounded border border-slate-800/80 bg-slate-950/45 px-2 py-1.5 text-[11px]">
                                <div className="font-semibold text-slate-300">{translateMangaEnum('manga_runtime_stage', stage.stage, t)}</div>
                                <div className="mt-1 space-y-0.5">
                                  {stage.changes.slice(0, 4).map((change) => (
                                    <div key={`${stage.stage}-${change.key}`} className="truncate text-slate-500" title={`${change.key}: ${formatDiffValue(change.before)} -> ${formatDiffValue(change.after)}`}>
                                      {change.key}: {formatDiffValue(change.before)}{' -> '}{formatDiffValue(change.after)}
                                    </div>
                                  ))}
                                </div>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          </details>
        )}

        {runtimeValidation && (detectRegions.length > 0 || ocrSeeds.length > 0) && (
          <details className="group mt-3 rounded-lg border border-slate-800 bg-slate-950/38">
            <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-2.5 py-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">
              <span>{t('manga_detect_ocr_matches')}</span>
              <span className="flex items-center gap-2">
                <span>{detectOcrMatches.rows.filter((row) => row.seeds.length > 0).length}/{detectRegions.length}</span>
                <ChevronDown size={14} className="transition-transform group-open:rotate-180" />
              </span>
            </summary>
            <div className="space-y-2 border-t border-slate-800 px-2.5 py-2">
              <div className="grid grid-cols-3 gap-2">
                <MetricTile label={t('manga_detect_regions')} value={detectRegions.length} tone="text-cyan-200" />
                <MetricTile label={t('manga_summary_seeds')} value={ocrSeeds.length} tone="text-amber-200" />
                <MetricTile label={t('manga_empty_detect_regions')} value={detectOcrMatches.emptyRegions.length} tone="text-slate-200" />
              </div>

              <div className="max-h-80 space-y-1.5 overflow-auto pr-1">
                {detectOcrMatches.rows.length === 0 && (
                  <div className="rounded-md border border-dashed border-slate-800 bg-slate-950/40 px-3 py-4 text-sm text-slate-500">
                    {t('manga_no_detect_ocr_matches')}
                  </div>
                )}
                {detectOcrMatches.rows.map((row) => (
                  <div key={`${row.region.regionId}-${row.region.bbox.join(',')}`} className="rounded-md border border-slate-800 bg-slate-900/58 px-2.5 py-2">
                    <button
                      type="button"
                      onClick={() => focusDetectRegion(row.region)}
                      className="w-full text-left transition-colors hover:text-cyan-100"
                      title={t('manga_focus_region')}
                    >
                      <span className="flex items-center justify-between gap-2">
                        <span className="inline-flex min-w-0 items-center gap-1.5 truncate text-xs font-semibold text-slate-100">
                          <SquareStack size={12} className="shrink-0 text-cyan-300" />
                          <span className="truncate">{row.region.regionId}</span>
                        </span>
                        <span className="shrink-0 text-[11px] font-semibold text-slate-500">
                          {t('manga_match_seed_count', row.seeds.length)}
                        </span>
                      </span>
                      <span className="mt-1 flex items-center justify-between gap-2 text-[11px] text-slate-500">
                        <span className="min-w-0 truncate">{row.region.kind} · {formatScore(row.region.score)}</span>
                        <span className="inline-flex shrink-0 items-center gap-1">
                          <LocateFixed size={11} />
                          {row.region.bbox.map((value) => Math.round(value)).join(',')}
                        </span>
                      </span>
                    </button>

                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {row.seeds.length === 0 ? (
                        <span className="rounded-full border border-slate-800 bg-slate-950/55 px-2 py-1 text-[11px] text-slate-500">
                          {t('manga_no_ocr_text')}
                        </span>
                      ) : row.seeds.map((match) => (
                        <button
                          key={`${row.region.regionId}-${seedKey(match.seed)}`}
                          type="button"
                          onClick={() => focusOcrSeed(match.seed)}
                          className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-amber-300/20 bg-amber-300/10 px-2 py-1 text-[11px] text-amber-100 transition-colors hover:border-amber-200/55"
                          title={`${match.seed.sourceText || match.seed.seedId} · ${Math.round(match.overlap * 100)}%`}
                        >
                          <Link2 size={11} className="shrink-0" />
                          <span className="truncate">{match.seed.sourceText || match.seed.seedId}</span>
                          <span className="shrink-0 text-amber-100/55">{Math.round(match.overlap * 100)}%</span>
                        </button>
                      ))}
                    </div>
                  </div>
                ))}
              </div>

              {detectOcrMatches.unmatchedSeeds.length > 0 && (
                <details className="group rounded-md border border-slate-800 bg-slate-900/58">
                  <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-2.5 py-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">
                    {t('manga_unmatched_ocr_seeds')}
                    <span className="inline-flex items-center gap-2">
                      {detectOcrMatches.unmatchedSeeds.length}
                      <ChevronDown size={13} className="transition-transform group-open:rotate-180" />
                    </span>
                  </summary>
                  <div className="flex flex-wrap gap-1.5 border-t border-slate-800 px-2.5 py-2">
                    {detectOcrMatches.unmatchedSeeds.slice(0, 24).map((seed) => (
                      <button
                        key={`unmatched-${seedKey(seed)}`}
                        type="button"
                        onClick={() => focusOcrSeed(seed)}
                        className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-slate-700/80 bg-slate-950/55 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:border-amber-300/45 hover:text-amber-100"
                        title={seed.sourceText || seed.seedId}
                      >
                        <LocateFixed size={11} className="shrink-0" />
                        <span className="truncate">{seed.sourceText || seed.seedId}</span>
                      </button>
                    ))}
                  </div>
                </details>
              )}
            </div>
          </details>
        )}

        {runtimeValidation && ocrSeeds.length > 0 && (
          <details className="group mt-3 rounded-lg border border-slate-800 bg-slate-950/38">
            <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-2.5 py-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">
              <span>{t('manga_ocr_seed_browser')}</span>
              <span className="flex items-center gap-2">
                <span>{filteredOcrSeeds.length}/{ocrSeeds.length}</span>
                <ChevronDown size={14} className="transition-transform group-open:rotate-180" />
              </span>
            </summary>
            <div className="space-y-2 border-t border-slate-800 px-2.5 py-2">
              <label className="flex items-center gap-2 rounded-md border border-slate-800 bg-slate-900/70 px-2.5 py-2 text-xs text-slate-300">
                <Search size={13} className="shrink-0 text-slate-500" />
                <input
                  type="search"
                  value={seedSearch}
                  onChange={(event) => setSeedSearch(event.target.value)}
                  placeholder={t('manga_ocr_seed_search_placeholder')}
                  className="min-w-0 flex-1 bg-transparent outline-none placeholder:text-slate-600"
                />
              </label>

              <div className="rounded-md border border-slate-800 bg-slate-900/58 px-2.5 py-2">
                <div className="flex items-center justify-between gap-3">
                  <label className="flex items-center gap-2 text-xs text-slate-200">
                    <input
                      type="checkbox"
                      checked={lowConfidenceOnly}
                      onChange={(event) => setLowConfidenceOnly(event.target.checked)}
                      className="h-4 w-4 rounded border-slate-700 bg-slate-950 text-primary"
                    />
                    {t('manga_ocr_low_confidence_only')}
                  </label>
                  <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">
                    {confidenceThreshold.toFixed(2)}
                  </span>
                </div>
                <input
                  type="range"
                  min="0.5"
                  max="1"
                  step="0.01"
                  value={confidenceThreshold}
                  onChange={(event) => setConfidenceThreshold(Number(event.target.value))}
                  className="mt-2 w-full accent-primary"
                />
              </div>

              <div className="max-h-72 space-y-1.5 overflow-auto pr-1">
                {filteredOcrSeeds.length === 0 && (
                  <div className="rounded-md border border-dashed border-slate-800 bg-slate-950/40 px-3 py-4 text-sm text-slate-500">
                    {t('manga_ocr_seed_empty')}
                  </div>
                )}
                {filteredOcrSeeds.map((seed) => (
                  <button
                    key={`${seed.seedId}-${seed.bbox.join(',')}`}
                    type="button"
                    onClick={() => focusOcrSeed(seed)}
                    className="w-full rounded-md border border-slate-800 bg-slate-900/65 px-2.5 py-2 text-left text-xs transition-colors hover:border-amber-300/45 hover:bg-amber-300/10"
                  >
                    <span className="flex items-center justify-between gap-2">
                      <span className="min-w-0 truncate font-semibold text-slate-100" title={seed.sourceText || seed.seedId}>
                        {seed.sourceText || seed.seedId}
                      </span>
                      <span className={`shrink-0 font-bold ${seed.confidence !== null && seed.confidence < confidenceThreshold ? 'text-amber-200' : 'text-slate-400'}`}>
                        {formatConfidence(seed.confidence)}
                      </span>
                    </span>
                    <span className="mt-1 flex items-center justify-between gap-2 text-[11px] text-slate-500">
                      <span className="min-w-0 truncate">{seed.seedId} · {seed.direction || '-'}</span>
                      <span className="inline-flex shrink-0 items-center gap-1">
                        <LocateFixed size={11} />
                        {seed.bbox.map((value) => Math.round(value)).join(',')}
                      </span>
                    </span>
                  </button>
                ))}
              </div>
            </div>
          </details>
        )}
      </section>

      {runtimeValidation && (
        <InspectorSection
          title={t('manga_validation_stages')}
          meta={t('manga_validation_stage_count', runtimeValidation.stages.length)}
        >
          <div className="space-y-2">
            {runtimeValidation.stages.map((stage) => {
              const mode = stage.execution_mode || (stage.used_runtime ? 'configured_runtime' : 'heuristic_fallback');
              return (
                <div
                  key={stage.stage}
                  className={`rounded-lg border px-3 py-2.5 transition-colors ${
                    activeRuntimeStage === stage.stage
                      ? 'border-cyan-300/60 bg-cyan-300/10'
                      : 'border-slate-800 bg-slate-950/42'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <button
                      type="button"
                      onClick={() => onSelectRuntimeStage(stage.stage)}
                      className="min-w-0 flex-1 text-left"
                    >
                      <div className="truncate text-sm font-semibold text-slate-100">
                        {translateMangaEnum('manga_runtime_stage', stage.stage, t)}
                      </div>
                    </button>
                    <div className="flex shrink-0 items-center gap-1.5">
                      <StatusPill tone={getExecutionTone(mode)}>
                        {translateMangaEnum('manga_execution_mode', mode, t)}
                      </StatusPill>
                      <button
                        type="button"
                        onClick={() => onRetryRuntimeValidationStage(stage.stage)}
                        disabled={Boolean(busyAction)}
                        title={t('manga_retry_runtime_stage')}
                        className="flex h-7 w-7 items-center justify-center rounded-md border border-slate-800 bg-slate-950/70 text-slate-400 transition-colors hover:border-cyan-300/40 hover:text-cyan-100 disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        {isRuntimeStageRetryBusy && activeRuntimeStage === stage.stage ? (
                          <Activity size={13} className="animate-pulse" />
                        ) : (
                          <RotateCcw size={13} />
                        )}
                      </button>
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => onSelectRuntimeStage(stage.stage)}
                    className="mt-2 grid w-full grid-cols-2 gap-2 text-left text-[11px] text-slate-500"
                  >
                    <div>
                      <div className="uppercase tracking-[0.14em]">{t('manga_runtime')}</div>
                      <div className="mt-0.5 truncate text-slate-300" title={stage.runtime_engine_id || '-'}>
                        {stage.runtime_engine_id || '-'}
                      </div>
                    </div>
                    <div>
                      <div className="uppercase tracking-[0.14em]">{t('manga_elapsed')}</div>
                      <div className="mt-0.5 text-slate-300">{t('manga_elapsed_ms', stage.elapsed_ms)}</div>
                    </div>
                  </button>
                  {(stage.warning_message || stage.error_message) && (
                    <div className="mt-2 rounded-md border border-amber-300/20 bg-amber-300/10 px-2.5 py-2 text-xs text-amber-100">
                      {stage.error_message || stage.warning_message}
                    </div>
                  )}
                  {stage.fallback_reason && (
                    <div className="mt-2 rounded-md border border-slate-700/70 bg-slate-900/70 px-2.5 py-2 text-xs text-slate-400">
                      {stage.fallback_reason}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          {selectedRuntimeStage && Object.keys(selectedRuntimeStage.artifacts || {}).length > 0 && (
            <div className="mt-3 rounded-lg border border-slate-800 bg-slate-950/45 px-3 py-2.5">
              <div className="flex items-center justify-between gap-2">
                <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">
                  {t('manga_stage_artifacts')}
                </div>
                {activeArtifact?.url && (
                  <a
                    href={activeArtifact.url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-[11px] font-semibold text-cyan-200 hover:text-cyan-100"
                  >
                    {t('manga_open_artifact')}
                    <ExternalLink size={12} />
                  </a>
                )}
              </div>
              <div className="mt-2 grid gap-1.5">
                {selectedStageArtifacts.map((artifact) => (
                  <button
                    key={artifact.key}
                    type="button"
                    onClick={() => setActiveArtifactKey(artifact.key)}
                    className={`grid gap-0.5 rounded-md border px-2.5 py-2 text-left text-xs transition-colors ${
                      activeArtifact?.key === artifact.key
                        ? 'border-cyan-300/55 bg-cyan-300/10'
                        : 'border-slate-800 bg-slate-900/65 hover:border-cyan-300/40'
                    }`}
                  >
                    <span className="flex items-center gap-1.5 font-semibold text-cyan-100">
                      {artifact.isImage ? <ImageIcon size={12} /> : <FileJson size={12} />}
                      {artifact.key}
                    </span>
                    <span className="truncate text-slate-500" title={artifact.path}>{artifact.path}</span>
                  </button>
                ))}
              </div>
              {activeArtifact && (
                <div className="mt-3 overflow-hidden rounded-lg border border-slate-800 bg-slate-950/70">
                  {activeArtifact.isImage && activeArtifact.url ? (
                    <img
                      src={activeArtifact.url}
                      alt={activeArtifact.key}
                      className="max-h-64 w-full object-contain"
                    />
                  ) : artifactLoading ? (
                    <div className="px-3 py-4 text-sm text-slate-500">{t('manga_loading_artifact')}</div>
                  ) : artifactError ? (
                    <div className="px-3 py-4 text-sm text-rose-200">{t('manga_artifact_load_failed', artifactError)}</div>
                  ) : artifactText ? (
                    <pre className="max-h-80 overflow-auto p-3 text-[11px] leading-relaxed text-slate-300">{artifactText}</pre>
                  ) : (
                    <div className="px-3 py-4 text-sm text-slate-500">{t('manga_artifact_preview_empty')}</div>
                  )}
                </div>
              )}
            </div>
          )}
        </InspectorSection>
      )}

      <section className="rounded-xl border border-slate-800/85 bg-slate-900/48 px-3 py-3">
        <div className="flex items-center justify-between gap-3">
          <div className="text-xs font-bold uppercase tracking-[0.18em] text-slate-400">{t('manga_panel_task_status')}</div>
          {activeJob && <StatusPill tone={activeJob.status === 'failed' ? 'rose' : 'cyan'}>{translateMangaEnum('manga_state', activeJob.status, t)}</StatusPill>}
        </div>
        {activeJob ? (
          <div className="mt-3">
            <div className="flex items-center justify-between gap-3 text-sm">
              <span className="truncate font-semibold text-slate-100">{activeJob.stageLabel}</span>
              <span className="text-xs text-slate-500">{clampPercent(activeJob.progress)}%</span>
            </div>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-950/80">
              <div
                className={`h-full ${activeJob.status === 'failed' ? 'bg-rose-400' : 'bg-cyan-400'}`}
                style={{ width: `${clampPercent(activeJob.progress)}%` }}
              />
            </div>
            <div className="mt-2 line-clamp-2 text-xs text-slate-400">{activeJob.message || t('manga_waiting_job_details')}</div>
          </div>
        ) : (
          <div className="mt-2 text-sm text-slate-500">{t('manga_no_active_job')}</div>
        )}
      </section>

      <InspectorSection
        title={t('manga_panel_engine_status')}
        meta={missingEngineCount > 0 ? t('manga_engine_missing_count', missingEngineCount) : t('manga_engine_all_ready')}
      >
        <div className="grid gap-2">
          {engineCards.length === 0 && (
            <div className="rounded-lg border border-dashed border-slate-800 bg-slate-950/38 px-3 py-4 text-sm text-slate-500">
              {t('manga_engine_status_empty')}
            </div>
          )}
          {engineCards.map((card) => (
            <div key={card.label} className="rounded-lg border border-slate-800 bg-slate-950/42 px-3 py-2.5">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0 truncate text-sm font-semibold text-slate-100">{card.label}</div>
                <StatusPill tone={card.available ? 'emerald' : 'amber'}>
                  {card.available ? t('manga_ready') : t('manga_missing')}
                </StatusPill>
              </div>
              <div className="mt-1 truncate text-xs text-slate-500" title={card.packageLabel || '-'}>
                {card.packageLabel || t('manga_unknown_package')}
              </div>

              {card.packages.length > 0 && (
                <div className="mt-2 space-y-2">
                  {card.packages.map((pkg) => {
                    const isPreparing = busyAction === `download model:${pkg.modelId}`;
                    const progress = isPreparing ? clampPercent(activeJob?.progress || 0) : 0;
                    return (
                      <div key={pkg.modelId} className="rounded-md border border-slate-800 bg-slate-900/58 px-2.5 py-2">
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0">
                            <div className="flex items-center gap-1.5">
                              <PackageCheck size={13} className={pkg.available ? 'text-emerald-300' : 'text-amber-300'} />
                              <div className="truncate text-xs font-semibold text-slate-200">{pkg.label}</div>
                            </div>
                            {pkg.readinessMessageKey && (
                              <div className="mt-0.5 line-clamp-2 text-[11px] text-amber-100">
                                {formatI18nPayload(
                                  pkg.readinessMessageKey,
                                  pkg.readinessMessageArgs,
                                  pkg.readinessStatus || '',
                                  t,
                                )}
                              </div>
                            )}
                            <div className="mt-0.5 truncate text-[11px] text-slate-500" title={pkg.repoId || pkg.storagePath}>
                              {pkg.repoId || pkg.storagePath || t('manga_unknown_package')}
                            </div>
                          </div>
                          {pkg.available ? (
                            <StatusPill tone="emerald">{t('manga_ready')}</StatusPill>
                          ) : (
                            <button
                              type="button"
                              onClick={() => onDownloadModel(pkg.modelId)}
                              disabled={Boolean(busyAction)}
                              className="shrink-0 rounded-md border border-amber-300/25 bg-amber-300/10 px-2 py-1 text-[10px] font-bold uppercase tracking-[0.12em] text-amber-100 transition-colors hover:border-amber-200 disabled:cursor-not-allowed disabled:opacity-50"
                            >
                              {isPreparing ? t('manga_preparing') : t('manga_prepare')}
                            </button>
                          )}
                        </div>

                        {isPreparing && (
                          <div className="mt-2">
                            <div className="flex items-center justify-between gap-2 text-[11px] text-slate-500">
                              <span>{t('manga_model_progress')}</span>
                              <span>{progress}%</span>
                            </div>
                            <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-slate-950/85">
                              <div className="h-full bg-amber-300" style={{ width: `${progress}%` }} />
                            </div>
                          </div>
                        )}

                        <details className="group mt-2">
                          <summary className="flex cursor-pointer list-none items-center justify-between gap-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">
                            {t('manga_model_details')}
                            <ChevronDown size={13} className="transition-transform group-open:rotate-180" />
                          </summary>
                          <div className="mt-2 grid grid-cols-1 gap-2 text-[11px] text-slate-500">
                            <div>
                              <div className="uppercase tracking-[0.14em]">{t('manga_runtime')}</div>
                              <div className="mt-0.5 truncate text-slate-300" title={pkg.runtimeEngineId || '-'}>
                                {pkg.runtimeEngineId || '-'}
                              </div>
                            </div>
                            <div>
                              <div className="uppercase tracking-[0.14em]">{t('manga_storage')}</div>
                              <div className="mt-0.5 truncate text-slate-300" title={pkg.storagePath || '-'}>
                                {pkg.storagePath || '-'}
                              </div>
                            </div>
                          </div>
                        </details>

                        {Array.isArray(pkg.missingModules) && pkg.missingModules.length > 0 && (
                          <div className="mt-2 text-[11px] text-amber-100">
                            {t('manga_runtime_readiness_missing_modules', pkg.missingModules.join(', '))}
                          </div>
                        )}

                        {Array.isArray(pkg.missingAssetPaths) && pkg.missingAssetPaths.length > 0 && (
                          <div className="mt-2 space-y-1 text-[11px] text-slate-500">
                            <div>{t('manga_runtime_readiness_missing_assets', pkg.missingAssetPaths.length)}</div>
                            {pkg.missingAssetPaths.slice(0, 2).map((assetPath) => (
                              <div key={assetPath} className="truncate" title={assetPath}>{assetPath}</div>
                            ))}
                          </div>
                        )}

                        {!pkg.runtimeSupported && (
                          <div className="mt-2 text-[11px] text-slate-500">{t('manga_model_no_runtime_bridge')}</div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}

              <details className="group mt-2 rounded-md border border-slate-800/75 bg-slate-900/40">
                <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-2 py-1.5 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">
                  {t('manga_configured')}
                  <ChevronDown size={13} className="transition-transform group-open:rotate-180" />
                </summary>
                <div className="space-y-2 border-t border-slate-800 px-2 py-2 text-[11px] text-slate-500">
                  <div>
                    <div className="uppercase tracking-[0.14em]">{t('manga_configured')}</div>
                    <div className="mt-0.5 break-all text-slate-300">{card.configured || '-'}</div>
                  </div>
                  <div>
                    <div className="uppercase tracking-[0.14em]">{t('manga_runtime')}</div>
                    <div className="mt-0.5 break-all text-slate-300">{card.runtime || '-'}</div>
                  </div>
                </div>
              </details>
            </div>
          ))}
        </div>
      </InspectorSection>

      <InspectorSection
        title={t('manga_panel_active_block')}
        meta={activeBlock ? activeBlock.block_id : t('manga_active_block_empty')}
      >
        {!activeBlock ? (
          <div className="rounded-lg border border-dashed border-slate-800 bg-slate-950/38 px-3 py-4 text-sm text-slate-500">
            {t('manga_active_block_empty')}
          </div>
        ) : (
          <div className="space-y-3">
            <div className="rounded-lg border border-slate-800 bg-slate-950/42 px-3 py-2.5">
              <div className="flex items-center justify-between gap-3">
                <div className="truncate font-semibold text-slate-100">{activeBlock.block_id}</div>
                <StatusPill>{activeBlock.origin}</StatusPill>
              </div>
              <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
                <MetricTile label={t('manga_field_position')} value={`x ${activeBlock.bbox[0]} / y ${activeBlock.bbox[1]}`} />
                <MetricTile label={t('manga_field_size')} value={`${blockWidth} x ${blockHeight}`} />
                <MetricTile label={t('manga_field_direction')} value={`${activeBlock.source_direction} -> ${activeBlock.rendered_direction}`} />
                <MetricTile label={t('manga_field_confidence')} value={activeBlock.ocr_confidence.toFixed(3)} />
              </div>
            </div>

            <div className="rounded-lg border border-slate-800 bg-slate-950/42 px-3 py-2.5">
              <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">{t('manga_text_preview')}</div>
              <div className="mt-2 text-[11px] text-slate-500">{t('manga_ocr_label')}</div>
              <div className="mt-1 line-clamp-4 whitespace-pre-wrap text-sm text-slate-200">{sourcePreview || t('manga_no_ocr_text')}</div>
              <div className="mt-2 text-[11px] text-slate-500">{t('manga_translation_label')}</div>
              <div className="mt-1 line-clamp-5 whitespace-pre-wrap text-sm text-slate-100">{translationPreview || t('manga_no_translation_yet')}</div>
            </div>

            <details className="group rounded-lg border border-slate-800 bg-slate-950/42">
              <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-3 py-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">
                {t('manga_render_style')}
                <ChevronDown size={13} className="transition-transform group-open:rotate-180" />
              </summary>
              <div className="grid grid-cols-2 gap-2 border-t border-slate-800 px-3 py-3 text-xs">
                <MetricTile label={t('manga_field_font')} value={<span className="break-all">{fontFamily || '-'}</span>} />
                <MetricTile label={t('manga_field_font_prediction')} value={<span className="break-all">{activeBlock.font_prediction || '-'}</span>} />
                <MetricTile label={t('manga_field_font_size')} value={fontSize} />
                <MetricTile label={t('manga_field_line_spacing')} value={lineSpacing} />
                <MetricTile
                  label={t('manga_field_fill')}
                  value={(
                    <span className="flex items-center gap-2">
                      <span className="h-3.5 w-3.5 rounded border border-slate-700" style={{ backgroundColor: fill }} />
                      {fill}
                    </span>
                  )}
                />
                <MetricTile
                  label={t('manga_field_stroke')}
                  value={(
                    <span className="flex items-center gap-2">
                      <span className="h-3.5 w-3.5 rounded border border-slate-700" style={{ backgroundColor: strokeColor }} />
                      {strokeWidth}px
                    </span>
                  )}
                />
              </div>
            </details>

            <details className="group rounded-lg border border-slate-800 bg-slate-950/42">
              <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-3 py-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">
                <span>{t('manga_source_metrics')}</span>
                <span className="flex items-center gap-2">
                  {sourceSizeSplit && <StatusPill tone="amber">{t('manga_source_size_split')}</StatusPill>}
                  <ChevronDown size={13} className="transition-transform group-open:rotate-180" />
                </span>
              </summary>
              <div className="space-y-3 border-t border-slate-800 px-3 py-3">
                <div className="grid grid-cols-2 gap-2 text-xs">
                  <MetricTile label={t('manga_source_char_size')} value={formatMetricValue(sourceCharSize)} tone="text-cyan-100" />
                  <MetricTile label={t('manga_source_char_confidence')} value={formatMetricValue(sourceCharConfidence, 3)} tone="text-cyan-100" />
                  <MetricTile label={t('manga_initial_font_size')} value={formatMetricValue(initialFontSize)} />
                  <MetricTile label={t('manga_final_font_size')} value={formatMetricValue(finalFontSize)} />
                  <MetricTile label={t('manga_font_scale_ratio')} value={formatMetricValue(fontScaleRatio, 3)} tone={Number(fontScaleRatio) > 0 && Number(fontScaleRatio) < 0.7 ? 'text-amber-200' : 'text-slate-100'} />
                  <MetricTile label={t('manga_source_seed_count')} value={formatMetricValue(sourceSeedCount)} />
                </div>
                <div className="grid gap-1.5 text-[11px] text-slate-500">
                  <div>{t('manga_source_char_method')}: <span className="text-slate-300">{sourceCharMethod}</span></div>
                  {sourceBbox.length >= 4 && (
                    <div>{t('manga_source_bbox')}: <span className="text-slate-300">{sourceBbox.join(', ')}</span></div>
                  )}
                  {sourceLayoutBbox.length >= 4 && (
                    <div>{t('manga_source_layout_bbox')}: <span className="text-slate-300">{sourceLayoutBbox.join(', ')}</span></div>
                  )}
                  {sourceSeedIds.length > 0 && (
                    <div>{t('manga_source_seed_ids')}: <span className="text-slate-300">{sourceSeedIds.join(', ')}</span></div>
                  )}
                </div>
                {activeBlockRenderWarnings.length > 0 && (
                  <div className="rounded-lg border border-amber-300/20 bg-amber-300/10 px-2.5 py-2">
                    <div className="text-[10px] font-bold uppercase tracking-[0.14em] text-amber-100">{t('manga_layout_warnings')}</div>
                    <div className="mt-1 text-xs text-amber-50">{activeBlockRenderWarnings.join(', ')}</div>
                  </div>
                )}
                {activeBlockDiagnosticTexts.length > 0 && (
                  <div className="rounded-lg border border-cyan-300/20 bg-cyan-300/10 px-2.5 py-2">
                    <div className="text-[10px] font-bold uppercase tracking-[0.14em] text-cyan-100">{t('manga_layout_diagnostics')}</div>
                    <div className="mt-1 space-y-1 text-xs text-cyan-50">
                      {activeBlockDiagnosticTexts.map((text, index) => (
                        <div key={`${text}-${index}`}>{text}</div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </details>

            <details className="group rounded-lg border border-slate-800 bg-slate-950/42">
              <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-3 py-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">
                {t('manga_block_geometry')}
                <ChevronDown size={13} className="transition-transform group-open:rotate-180" />
              </summary>
              <div className="space-y-2 border-t border-slate-800 px-3 py-3 text-xs text-slate-400">
                <div>{t('manga_field_placement')}: <span className="text-slate-200">{activeBlock.placement_mode} · {activeBlock.editable ? t('manga_editable') : t('manga_locked')}</span></div>
                <div>{t('manga_field_bbox')}: <span className="text-slate-200">{activeBlock.bbox.join(', ')}</span></div>
                <div>{t('manga_field_flags')}: <span className="text-slate-200">{activeBlock.flags.join(', ') || t('manga_no_flags')}</span></div>
              </div>
            </details>
          </div>
        )}
      </InspectorSection>
    </div>
  );
};
