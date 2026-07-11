import React, { useMemo, useState } from 'react';
import {
  Archive,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Clock3,
  FileCheck2,
  RotateCcw,
  X,
} from 'lucide-react';

export type ProofreadSuggestionStatus = 'pending' | 'accepted' | 'rejected' | 'ignored' | 'conflict' | 'stale';

export interface WebProofreadSuggestion {
  suggestion_id: string;
  item_id: string;
  file_path: string;
  text_index: number;
  line_no: number;
  source_text: string;
  current_translation: string;
  suggested_translation: string;
  reason: string;
  severity: string;
  issue_type: string;
  confidence: number;
  status: ProofreadSuggestionStatus;
  undo_available?: boolean;
}

export interface ProofreadLineGroup {
  item_id: string;
  file_path: string;
  text_index: number;
  suggestions: WebProofreadSuggestion[];
  displayStatus: ProofreadSuggestionStatus;
  highestSeverity: string;
}

export interface ProofreadReportOption {
  file: string;
  is_archive: boolean;
  modified_time?: string;
  run?: Record<string, any>;
  summary?: ProofreadSummary;
}

export interface ProofreadSummary {
  total_suggestions?: number;
  unique_lines?: number;
  status_counts?: Record<string, number>;
}

type Translator = (key: string, ...args: any[]) => string;

const markerFor = (status: ProofreadSuggestionStatus) => ({
  pending: '#', accepted: '*', rejected: '-', ignored: '~', conflict: '!', stale: 'x',
}[status]);

const toneFor = (status: ProofreadSuggestionStatus) => ({
  pending: 'text-amber-300', accepted: 'text-emerald-300', rejected: 'text-slate-500',
  ignored: 'text-cyan-300', conflict: 'text-rose-300', stale: 'text-slate-600',
}[status]);

interface ReportPanelProps {
  t: Translator;
  options: ProofreadReportOption[];
  activeFile: string;
  run: Record<string, any>;
  summary: ProofreadSummary;
  filter: string;
  collapsed: boolean;
  onSelectReport: (file: string) => void;
  onFilter: (filter: string) => void;
  onUndo: () => void;
  onToggle: () => void;
}

export const ProofreadReportPanel: React.FC<ReportPanelProps> = ({
  t, options, activeFile, run, summary, filter, collapsed, onSelectReport, onFilter, onUndo, onToggle,
}) => {
  const [open, setOpen] = useState(false);
  const counts = summary.status_counts || {};
  if (collapsed) {
    return (
      <button type="button" onClick={onToggle} title={t('cache_editor_proofread_report')} className="pointer-events-auto inline-flex h-10 items-center gap-2 rounded-lg border border-slate-700 bg-slate-950/95 px-3 text-xs font-semibold shadow-xl">
        <FileCheck2 size={15} className="text-amber-300" />
        <span className="text-amber-300">#{counts.pending || 0}</span>
        <span className="text-rose-300">!{counts.conflict || 0}</span>
      </button>
    );
  }
  return (
    <section className="pointer-events-auto w-[270px] rounded-lg border border-slate-700 bg-slate-950/95 text-slate-100 shadow-2xl">
      <div className="flex items-start justify-between border-b border-slate-800 px-3 py-2.5">
        <div className="min-w-0">
          <div className="truncate text-xs font-semibold">{t('cache_editor_proofread_report')}</div>
          <div className="mt-0.5 truncate text-[10px] text-slate-500">
            {t('cache_editor_proofread_run_meta', Number(run.sequence || 1), String(run.model || '-'))}
          </div>
        </div>
        <button type="button" onClick={onToggle} title={t('cache_editor_proofread_collapse')} className="rounded-md p-1.5 text-slate-500 hover:bg-slate-800 hover:text-white"><ChevronLeft size={14} /></button>
      </div>
      <div className="grid grid-cols-3 gap-px bg-slate-800">
        {([['pending', '#', 'text-amber-300'], ['conflict', '!', 'text-rose-300'], ['accepted', '*', 'text-emerald-300']] as const).map(([status, mark, tone]) => (
          <button type="button" key={status} onClick={() => onFilter(status)} className={`bg-slate-950 py-2 text-sm font-black ${tone} ${filter === status ? 'ring-1 ring-inset ring-slate-600' : ''}`}>{mark}{counts[status] || 0}</button>
        ))}
      </div>
      <div className="p-2">
        <div className="relative">
          <button type="button" onClick={() => setOpen((value) => !value)} className="flex w-full items-center justify-between gap-2 rounded-md border border-slate-800 bg-slate-900/70 px-2.5 py-2 text-left text-[11px] text-slate-300">
            <span className="flex min-w-0 items-center gap-2"><Archive size={13} className="shrink-0 text-slate-500" /><span className="truncate">{activeFile || t('cache_editor_proofread_no_report')}</span></span>
            <ChevronDown size={13} className={open ? 'rotate-180' : ''} />
          </button>
          {open && (
            <div className="absolute left-0 right-0 top-[calc(100%+6px)] z-50 max-h-52 overflow-y-auto rounded-md border border-slate-700 bg-slate-950 p-1 shadow-2xl custom-scrollbar">
              {options.map((option) => (
                <button type="button" key={option.file} onClick={() => { onSelectReport(option.file); setOpen(false); }} className={`block w-full rounded px-2 py-2 text-left text-[11px] hover:bg-slate-900 ${option.file === activeFile ? 'bg-primary/10 text-primary' : 'text-slate-300'}`}>
                  <span className="block truncate font-semibold">{option.is_archive ? t('cache_editor_proofread_archive') : t('cache_editor_proofread_current')}</span>
                  <span className="mt-0.5 block truncate text-[9px] text-slate-600">{option.modified_time || option.file}</span>
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="mt-2 flex gap-1 overflow-x-auto pb-0.5">
          {['pending', 'ignored', 'conflict', 'accepted', 'rejected', 'all'].map((value) => (
            <button type="button" key={value} onClick={() => onFilter(value)} className={`shrink-0 rounded px-2 py-1 text-[9px] font-semibold ${filter === value ? 'bg-primary text-slate-950' : 'bg-slate-900 text-slate-500 hover:text-white'}`}>{t(`cache_editor_proofread_filter_${value}`)}</button>
          ))}
        </div>
        <div className="mt-2 flex items-center justify-between text-[10px] text-slate-600">
          <span>{t('cache_editor_proofread_line_count', summary.unique_lines || 0)}</span>
        <button type="button" onClick={onUndo} className="inline-flex min-h-9 items-center gap-1 rounded px-2 text-slate-400 hover:bg-slate-900 hover:text-white"><RotateCcw size={12} />{t('cache_editor_proofread_undo')}</button>
        </div>
      </div>
    </section>
  );
};

interface NavigatorProps {
  t: Translator;
  groups: ProofreadLineGroup[];
  index: number;
  collapsed: boolean;
  onSelect: (index: number) => void;
  onToggle: () => void;
}

export const ProofreadQuickNavigator: React.FC<NavigatorProps> = ({ t, groups, index, collapsed, onSelect, onToggle }) => {
  const range = useMemo(() => {
    if (groups.length <= 5) return { start: 0, end: groups.length };
    const start = Math.min(Math.max(index - 2, 0), groups.length - 5);
    return { start, end: start + 5 };
  }, [groups.length, index]);
  const move = (delta: number) => groups.length && onSelect(Math.min(Math.max(index + delta, 0), groups.length - 1));
  if (collapsed) {
    return <button type="button" onClick={onToggle} title={t('cache_editor_proofread_quick_nav', index + 1, groups.length)} className="pointer-events-auto inline-flex h-10 items-center gap-2 rounded-lg border border-slate-700 bg-slate-950/95 px-3 text-xs font-semibold text-amber-300 shadow-xl"><ChevronLeft size={14} />#{groups.length}</button>;
  }
  return (
    <section onWheel={(event) => { event.preventDefault(); move(event.deltaY > 0 ? 1 : -1); }} className="pointer-events-auto w-[310px] rounded-lg border border-slate-700 bg-slate-950/94 p-2 shadow-2xl">
      <div className="mb-1 flex items-center justify-between px-1 text-[10px] text-slate-500">
        <span>{t('cache_editor_proofread_quick_nav', index + 1, groups.length)}</span>
        <span className="flex gap-1"><button type="button" aria-label={t('cache_editor_previous')} onClick={() => move(-1)} className="rounded p-1 hover:bg-slate-800"><ChevronLeft size={13} /></button><button type="button" aria-label={t('cache_editor_next')} onClick={() => move(1)} className="rounded p-1 hover:bg-slate-800"><ChevronRight size={13} /></button><button type="button" aria-label={t('cache_editor_proofread_collapse')} onClick={onToggle} className="rounded p-1 hover:bg-slate-800"><X size={13} /></button></span>
      </div>
      <div className="grid gap-0.5">
        {groups.slice(range.start, range.end).map((group, offset) => {
          const absoluteIndex = range.start + offset;
          const selected = absoluteIndex === index;
          return (
            <button type="button" key={group.item_id} onClick={() => onSelect(absoluteIndex)} className={`flex h-9 min-w-0 items-center gap-2 rounded-md px-2 text-left ${selected ? 'bg-slate-800 text-white' : 'text-slate-400 hover:bg-slate-900'}`}>
              <span className={`w-4 text-center text-sm font-black ${toneFor(group.displayStatus)}`}>{markerFor(group.displayStatus)}</span>
              <span className="min-w-0 flex-1 truncate text-[11px]">{group.suggestions[0]?.reason || group.item_id}</span>
              {group.suggestions.length > 1 && <span className="text-[9px] text-slate-600">{group.suggestions.length}</span>}
              {selected && <span className="h-0.5 w-3 rounded bg-primary" />}
            </button>
          );
        })}
      </div>
    </section>
  );
};

interface InlineProps {
  t: Translator;
  suggestion: WebProofreadSuggestion;
  index: number;
  count: number;
  busy: boolean;
  onPrevious: () => void;
  onNext: () => void;
  onAccept: () => void;
  onReject: () => void;
  onIgnore: () => void;
  onRestore: () => void;
}

export const ProofreadInlineSuggestion: React.FC<InlineProps> = ({ t, suggestion, index, count, busy, onPrevious, onNext, onAccept, onReject, onIgnore, onRestore }) => (
  <div onClick={(event) => event.stopPropagation()} className="mt-3 rounded-lg border border-amber-300/20 bg-amber-300/[0.045] p-3 text-xs">
    <div className="flex items-center justify-between gap-3">
      <div className="flex min-w-0 items-center gap-2"><span className={`text-sm font-black ${toneFor(suggestion.status)}`}>{markerFor(suggestion.status)}</span><span className="truncate font-semibold text-slate-200">{t('cache_editor_proofread_inline_title')}</span><span className="text-[9px] text-slate-600">{suggestion.severity} · {suggestion.issue_type} · {Number(suggestion.confidence || 0).toFixed(2)}</span></div>
      {count > 1 && <div className="flex items-center gap-1 text-[9px] text-slate-500"><button type="button" aria-label={t('cache_editor_previous')} onClick={onPrevious} className="rounded p-1 hover:bg-slate-800"><ChevronLeft size={12} /></button><span>{index + 1}/{count}</span><button type="button" aria-label={t('cache_editor_next')} onClick={onNext} className="rounded p-1 hover:bg-slate-800"><ChevronRight size={12} /></button></div>}
    </div>
    <div className="mt-3 text-[9px] font-semibold uppercase text-slate-600">{t('cache_editor_proofread_corrected')}</div>
    <div className="mt-1 whitespace-pre-wrap rounded-md bg-slate-950/55 p-2.5 leading-relaxed text-emerald-200">{suggestion.suggested_translation}</div>
    <div className="mt-2 text-[9px] font-semibold uppercase text-slate-600">{t('cache_editor_proofread_reason')}</div>
    <div className="mt-1 leading-relaxed text-slate-400">{suggestion.reason}</div>
    <div className="mt-3 flex flex-wrap justify-end gap-2">
      {suggestion.status === 'pending' || suggestion.status === 'conflict' ? <>
        <button type="button" onClick={onAccept} disabled={busy || suggestion.status === 'conflict'} className="inline-flex h-8 items-center gap-1.5 rounded-md bg-emerald-500 px-3 text-[11px] font-semibold text-slate-950 disabled:opacity-40"><Check size={13} />{t('cache_editor_proofread_accept')}</button>
        <button type="button" onClick={onReject} disabled={busy} className="inline-flex h-8 items-center gap-1.5 rounded-md border border-rose-300/25 bg-rose-300/10 px-3 text-[11px] font-semibold text-rose-200 disabled:opacity-40"><X size={13} />{t('cache_editor_proofread_reject')}</button>
        <button type="button" onClick={onIgnore} disabled={busy} className="inline-flex h-8 items-center gap-1.5 rounded-md border border-cyan-300/20 bg-cyan-300/10 px-3 text-[11px] font-semibold text-cyan-200 disabled:opacity-40"><Clock3 size={13} />{t('cache_editor_proofread_ignore')}</button>
      </> : <button type="button" onClick={onRestore} disabled={busy || suggestion.status === 'accepted'} className="inline-flex h-8 items-center gap-1.5 rounded-md border border-slate-700 bg-slate-900 px-3 text-[11px] font-semibold text-slate-300 disabled:opacity-40"><RotateCcw size={13} />{t('cache_editor_proofread_restore')}</button>}
    </div>
  </div>
);

export const getProofreadMarker = markerFor;
