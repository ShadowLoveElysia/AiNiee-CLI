import React, { useState, useEffect, useMemo, useRef } from 'react';
import { AlertTriangle, ArchiveRestore, DatabaseBackup, FileCheck2, ListChecks, RefreshCw } from 'lucide-react';
import { useI18n } from '../contexts/I18nContext';
import { useGlobal } from '../contexts/GlobalContext';
import {
  ProofreadInlineSuggestion,
  ProofreadLineGroup,
  ProofreadQuickNavigator,
  ProofreadReportOption,
  ProofreadReportPanel,
  ProofreadSummary,
  ProofreadSuggestionStatus,
  WebProofreadSuggestion,
} from '../components/ProofreadReviewOverlays';

interface CacheItem {
  id: number;
  file_path: string;
  text_index: number;
  source: string;
  translation: string;
  original_translation: string;
  translation_status: number;
  modified: boolean;
}

interface CacheStatus {
  loaded: boolean;
  file_count: number;
  total_items: number;
  project_name: string | null;
}

interface CacheBackup {
  file: string;
  modified_time: string;
  size_label: string;
  project_name: string;
  item_count: number;
  translated_count: number;
  total_count: number;
  completion_rate: number;
  completion_label: string;
}

interface Pagination {
  current_page: number;
  page_size: number;
  total_items: number;
  total_pages: number;
  has_next: boolean;
  has_prev: boolean;
}

// AI Proofread interfaces
interface ProofreadIssue {
  id: number | string;
  text_index: number;
  file_path: string;
  source: string;
  original_translation: string;
  corrected_translation: string;
  issue_type: string;
  severity: string;
  description: string;
  accepted: boolean;
}

interface ProofreadReportPayload {
  report_file: string;
  is_archive: boolean;
  run: Record<string, any>;
  review_state: Record<string, any>;
  summary: ProofreadSummary;
  suggestions: WebProofreadSuggestion[];
}

interface ProofreadState {
  running: boolean;
  progress: number;
  total: number;
  issues: ProofreadIssue[];
  tokens_used: number;
  error: string | null;
  completed: boolean;
}

// State persistence keys
const CACHE_EDITOR_STATE_KEY = 'cache_editor_state';
const PROOFREAD_STATE_KEY = 'proofread_state';

// Helper functions for state persistence
const saveStateToStorage = (state: any) => {
  try {
    localStorage.setItem(CACHE_EDITOR_STATE_KEY, JSON.stringify(state));
  } catch (err) {
    console.warn('Failed to save cache editor state:', err);
  }
};

const loadStateFromStorage = () => {
  try {
    const saved = localStorage.getItem(CACHE_EDITOR_STATE_KEY);
    return saved ? JSON.parse(saved) : null;
  } catch (err) {
    console.warn('Failed to load cache editor state:', err);
    return null;
  }
};

// Proofread state persistence
const saveProofreadState = (state: ProofreadState) => {
  try {
    localStorage.setItem(PROOFREAD_STATE_KEY, JSON.stringify(state));
  } catch (err) {
    console.warn('Failed to save proofread state:', err);
  }
};

const loadProofreadState = (): ProofreadState | null => {
  try {
    const saved = localStorage.getItem(PROOFREAD_STATE_KEY);
    return saved ? JSON.parse(saved) : null;
  } catch (err) {
    console.warn('Failed to load proofread state:', err);
    return null;
  }
};

export const CacheEditor: React.FC = () => {
  const { t } = useI18n();
  const { activeTheme, config } = useGlobal();
  const elysiaActive = activeTheme === 'elysia' || activeTheme === 'herrscher_of_human';

  const getThemeColorClass = () => {
    switch(activeTheme) {
        case 'elysia': return 'text-pink-500';
        case 'herrscher_of_human': return 'text-[#ff4d6d]';
        case 'eden': return 'text-amber-500';
        case 'mobius': return 'text-green-500';
        case 'kalpas': return 'text-red-500';
        default: return 'text-primary';
    }
  };

  const getLabelColor = () => {
    if (elysiaActive) return 'text-pink-400';
    if (activeTheme === 'eden') return 'text-amber-500';
    if (activeTheme === 'mobius') return 'text-green-500';
    return 'text-slate-500';
  };

  // Load saved state - will be refreshed when needed
  const [savedState, setSavedState] = useState(() => loadStateFromStorage());

  const [cacheStatus, setCacheStatus] = useState<CacheStatus>({
    loaded: false,
    file_count: 0,
    total_items: 0,
    project_name: null
  });
  const [cacheItems, setCacheItems] = useState<CacheItem[]>([]);
  const [pagination, setPagination] = useState<Pagination | null>(null);
  const [currentPage, setCurrentPage] = useState(() => savedState?.currentPage || 1);
  const [pageSize, setPageSize] = useState(() => savedState?.pageSize || 15);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [projectPath, setProjectPath] = useState(() => savedState?.projectPath || '');
  const [cacheBackups, setCacheBackups] = useState<CacheBackup[]>([]);
  const [backupLoading, setBackupLoading] = useState(false);
  const [backupRestoring, setBackupRestoring] = useState<string | null>(null);
  const [showBackups, setShowBackups] = useState(false);
  const [backupMessage, setBackupMessage] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState(() => savedState?.searchQuery || '');
  const [editingItem, setEditingItem] = useState<number | null>(null);
  const [editingText, setEditingText] = useState('');
  const [currentLine, setCurrentLine] = useState(() => savedState?.currentLine || 0);

  // AI Proofread state
  const [proofreadPath, setProofreadPath] = useState('');
  const [proofreadState, setProofreadState] = useState<ProofreadState>(() =>
    loadProofreadState() || {
      running: false,
      progress: 0,
      total: 0,
      issues: [],
      tokens_used: 0,
      error: null,
      completed: false
    }
  );
  const [proofreadReports, setProofreadReports] = useState<ProofreadReportOption[]>([]);
  const [activeProofreadReport, setActiveProofreadReport] = useState('');
  const [proofreadSuggestions, setProofreadSuggestions] = useState<WebProofreadSuggestion[]>([]);
  const [proofreadRun, setProofreadRun] = useState<Record<string, any>>({});
  const [proofreadSummary, setProofreadSummary] = useState<ProofreadSummary>({});
  const [proofreadFilter, setProofreadFilter] = useState('pending');
  const [currentProofreadIndex, setCurrentProofreadIndex] = useState(0);
  const [expandedProofreadItemId, setExpandedProofreadItemId] = useState('');
  const [proofreadSuggestionOffsets, setProofreadSuggestionOffsets] = useState<Record<string, number>>({});
  const [proofreadActionBusy, setProofreadActionBusy] = useState(false);
  const [proofreadSuggestionMode, setProofreadSuggestionMode] = useState<'proofread' | 'annotation'>(
    () => savedState?.proofreadSuggestionMode === 'annotation' ? 'annotation' : 'proofread'
  );
  const [reportPanelCollapsed, setReportPanelCollapsed] = useState(
    () => typeof window !== 'undefined' && !window.matchMedia('(min-width: 1800px)').matches
  );
  const [quickNavigatorCollapsed, setQuickNavigatorCollapsed] = useState(
    () => typeof window !== 'undefined' && !window.matchMedia('(min-width: 1800px)').matches
  );
  const [mobileProofreadPanel, setMobileProofreadPanel] = useState<'report' | 'navigator' | null>(null);

  useEffect(() => {
    if (!savedState?.proofreadSuggestionMode && config?.proofread_suggestion_mode) {
      setProofreadSuggestionMode(config.proofread_suggestion_mode);
    }
  }, [config?.proofread_suggestion_mode, savedState?.proofreadSuggestionMode]);

  // Single line AI Analysis state
  const [analyzingLine, setAnalyzingLine] = useState(false);
  const [lineAnalysisResult, setLineAnalysisResult] = useState<{
    has_issues: boolean;
    message?: string;
    issues?: any[];
    corrected_translation?: string;
  } | null>(null);

  // Refs for scroll sync
  const sourceScrollRef = useRef<HTMLDivElement>(null);
  const translationScrollRef = useRef<HTMLDivElement>(null);
  const scrollSyncActive = useRef<boolean>(false);
  const pendingProofreadTarget = useRef<{ filePath: string; textIndex: number } | null>(null);

  const groupProofreadSuggestions = (suggestions: WebProofreadSuggestion[]): ProofreadLineGroup[] => {
    const grouped = new Map<string, WebProofreadSuggestion[]>();
    suggestions.forEach((suggestion) => {
      const items = grouped.get(suggestion.item_id) || [];
      items.push(suggestion);
      grouped.set(suggestion.item_id, items);
    });
    const statusPriority: Record<ProofreadSuggestionStatus, number> = {
      conflict: 7,
      pending: 6,
      discarded: 5,
      accepted: 4,
      ignored: 3,
      rejected: 2,
      completed: 1,
      stale: 0,
    };
    return Array.from(grouped.entries()).map(([itemId, items]) => {
      const displayStatus = [...items].sort(
        (left, right) => statusPriority[right.status] - statusPriority[left.status]
      )[0]?.status || 'stale';
      return {
        item_id: itemId,
        file_path: items[0].file_path,
        text_index: items[0].text_index,
        suggestions: items,
        displayStatus,
        highestSeverity: items.some((item) => item.severity === 'high')
          ? 'high'
          : items.some((item) => item.severity === 'medium') ? 'medium' : 'info',
      };
    });
  };

  const allProofreadGroups = useMemo(
    () => groupProofreadSuggestions(proofreadSuggestions),
    [proofreadSuggestions]
  );
  const visibleProofreadGroups = useMemo(
    () => proofreadFilter === 'all'
      ? allProofreadGroups
      : allProofreadGroups.filter((group) =>
          group.suggestions.some((suggestion) => suggestion.status === proofreadFilter)
        ),
    [allProofreadGroups, proofreadFilter]
  );
  const proofreadGroupByItemId = useMemo(
    () => new Map(allProofreadGroups.map((group) => [group.item_id, group])),
    [allProofreadGroups]
  );

  const applyProofreadReport = (payload: ProofreadReportPayload) => {
    const nextSuggestions = payload.suggestions || [];
    setActiveProofreadReport(payload.report_file || '');
    setProofreadSuggestions(nextSuggestions);
    setProofreadRun(payload.run || {});
    setProofreadSummary(payload.summary || {});
    const nextFilter = String(payload.review_state?.active_filter || 'pending');
    setProofreadFilter(nextFilter);
    const currentSuggestionId = String(payload.review_state?.current_suggestion_id || '');
    if (currentSuggestionId) {
      const suggestion = nextSuggestions.find(
        (item) => item.suggestion_id === currentSuggestionId
      );
      if (suggestion) {
        const sameLine = nextSuggestions.filter((item) => item.item_id === suggestion.item_id);
        setExpandedProofreadItemId(suggestion.item_id);
        setProofreadSuggestionOffsets((current) => ({
          ...current,
          [suggestion.item_id]: Math.max(0, sameLine.findIndex(
            (item) => item.suggestion_id === currentSuggestionId
          )),
        }));
      }
    } else {
      setExpandedProofreadItemId('');
    }
  };

  const reportQueryFile = (file = activeProofreadReport) =>
    file && file !== 'ProofreadSuggestions.json' ? file : '';

  const loadProofreadReport = async (file = activeProofreadReport, path = projectPath) => {
    if (!path.trim()) return;
    const params = new URLSearchParams({ project_path: path });
    const reportFile = reportQueryFile(file);
    if (reportFile) params.set('report_file', reportFile);
    const response = await fetch(`/api/proofread/suggestions?${params}`);
    if (response.status === 404) {
      setProofreadSuggestions([]);
      setProofreadSummary({});
      return;
    }
    if (!response.ok) throw new Error(t('cache_editor_proofread_report_load_failed'));
    applyProofreadReport(await response.json());
  };

  const loadProofreadReports = async (path = projectPath, preserveSelection = true) => {
    if (!path.trim()) return;
    const response = await fetch(`/api/proofread/reports?project_path=${encodeURIComponent(path)}`);
    if (!response.ok) throw new Error(t('cache_editor_proofread_report_load_failed'));
    const data = await response.json();
    const options: ProofreadReportOption[] = [];
    if (data.current) {
      options.push({
        ...data.current,
        file: data.current.file || 'ProofreadSuggestions.json',
        is_archive: false,
      });
    }
    (data.archives || []).forEach((archive: any) => options.push({
      file: archive.file,
      is_archive: true,
      modified_time: archive.modified_time,
      run: archive.run,
      summary: archive.summary,
    }));
    setProofreadReports(options);
    const selected = preserveSelection && options.some((option) => option.file === activeProofreadReport)
      ? activeProofreadReport
      : options[0]?.file || '';
    setActiveProofreadReport(selected);
    if (selected) {
      await loadProofreadReport(selected, path);
    } else {
      setProofreadSuggestions([]);
      setProofreadRun({});
      setProofreadSummary({});
      setExpandedProofreadItemId('');
      setCurrentProofreadIndex(0);
    }
  };

  const persistProofreadReviewState = async (suggestionId?: string, filter = proofreadFilter) => {
    if (!projectPath.trim() || !activeProofreadReport) return;
    await fetch('/api/proofread/review-state', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        project_path: projectPath,
        report_file: reportQueryFile() || null,
        current_suggestion_id: suggestionId,
        active_filter: filter,
      }),
    });
  };

  const runProofreadAction = async (
    suggestionId: string,
    action: 'accept' | 'reject' | 'ignore' | 'restore' | 'delete'
  ) => {
    const previousFilter = proofreadFilter;
    const previousSuggestion = proofreadSuggestions.find(
      (suggestion) => suggestion.suggestion_id === suggestionId
    );
    const previousGroupIndex = previousSuggestion
      ? visibleProofreadGroups.findIndex((group) => group.item_id === previousSuggestion.item_id)
      : currentProofreadIndex;
    let allowManualEditOverride = false;
    if (action === 'accept' && previousSuggestion?.was_discarded) {
      allowManualEditOverride = window.confirm(t('cache_editor_proofread_confirm_manual_edit_accept'));
      if (!allowManualEditOverride) return;
    }
    if (action === 'delete' && !window.confirm(t('cache_editor_proofread_confirm_delete'))) return;
    setProofreadActionBusy(true);
    try {
      const response = await fetch(`/api/proofread/suggestions/${suggestionId}/${action}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_path: projectPath,
          report_file: reportQueryFile() || null,
          allow_manual_edit_override: allowManualEditOverride,
        }),
      });
      const data = await response.json();
      const report = data.report as ProofreadReportPayload | undefined;
      if (report) applyProofreadReport(report);
      if (!response.ok) throw new Error(data.message || t('cache_editor_proofread_action_failed'));
      if (action === 'accept') await loadCacheItems(currentPage, searchQuery);

      if (report) {
        const nextFilter = String(report.review_state?.active_filter || previousFilter);
        const nextAllGroups = groupProofreadSuggestions(report.suggestions || []);
        const nextVisibleGroups = nextFilter === 'all'
          ? nextAllGroups
          : nextAllGroups.filter((group) =>
              group.suggestions.some((suggestion) => suggestion.status === nextFilter)
            );
        const sameLineGroup = previousSuggestion
          ? nextVisibleGroups.find((group) => group.item_id === previousSuggestion.item_id)
          : undefined;
        const sameLineSuggestion = sameLineGroup?.suggestions.find((suggestion) =>
          suggestion.suggestion_id !== suggestionId
          && (
            nextFilter === 'all'
              ? suggestion.status === 'pending' || suggestion.status === 'conflict'
              : suggestion.status === nextFilter
          )
        );
        let targetGroup = sameLineSuggestion ? sameLineGroup : undefined;
        if (!targetGroup && nextVisibleGroups.length > 0) {
          const targetIndex = Math.min(Math.max(previousGroupIndex, 0), nextVisibleGroups.length - 1);
          targetGroup = nextVisibleGroups[targetIndex];
        }
        if (targetGroup) {
          const targetIndex = nextVisibleGroups.findIndex(
            (group) => group.item_id === targetGroup?.item_id
          );
          const targetSuggestion = sameLineSuggestion
            || targetGroup.suggestions.find((suggestion) => suggestion.status === nextFilter)
            || targetGroup.suggestions.find((suggestion) =>
              suggestion.status === 'pending' || suggestion.status === 'conflict'
            )
            || targetGroup.suggestions[0];
          await navigateToProofreadGroupData(
            targetGroup,
            targetIndex,
            nextFilter,
            targetSuggestion?.suggestion_id,
          );
        } else {
          setCurrentProofreadIndex(0);
          setExpandedProofreadItemId('');
          await persistProofreadReviewState('', nextFilter);
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t('cache_editor_proofread_action_failed'));
    } finally {
      setProofreadActionBusy(false);
    }
  };

  const undoProofreadAction = async () => {
    setProofreadActionBusy(true);
    try {
      const response = await fetch('/api/proofread/undo', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_path: projectPath,
          report_file: reportQueryFile() || null,
        }),
      });
      const data = await response.json();
      if (data.report) applyProofreadReport(data.report);
      if (!response.ok) throw new Error(data.message || t('cache_editor_proofread_nothing_to_undo'));
      await loadCacheItems(currentPage, searchQuery);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('cache_editor_proofread_nothing_to_undo'));
    } finally {
      setProofreadActionBusy(false);
    }
  };

  // Save state to localStorage whenever key values change
  useEffect(() => {
    const stateToSave = {
      currentPage,
      pageSize,
      projectPath,
      searchQuery,
      currentLine,
      proofreadSuggestionMode,
    };
    saveStateToStorage(stateToSave);
  }, [currentPage, pageSize, projectPath, searchQuery, currentLine, proofreadSuggestionMode]);

  // Save proofread state to localStorage
  useEffect(() => {
    saveProofreadState(proofreadState);
  }, [proofreadState]);

  // Poll proofread status when running
  useEffect(() => {
    if (!proofreadState.running) return;

    const pollInterval = setInterval(async () => {
      try {
        const response = await fetch('/api/proofread/status');
        if (response.ok) {
          const status = await response.json();
          setProofreadState(status);
          if (!status.running) {
            clearInterval(pollInterval);
            setActiveProofreadReport('');
            loadProofreadReports(proofreadPath || projectPath, false).catch((err) => {
              console.error('Failed to refresh proofread reports:', err);
            });
          }
        }
      } catch (err) {
        console.error('Failed to poll proofread status:', err);
      }
    }, 1000);

    return () => clearInterval(pollInterval);
  }, [proofreadState.running]);

  // Auto-restore cache if we have a saved project path
  useEffect(() => {
    const initializeState = async () => {
      try {
        await checkCacheStatus();
        // Load page size, but don't fail if it's not available
        try {
          await loadPageSize();
        } catch (err) {
          console.warn('Could not load page size config:', err);
        }

        // Only try to restore if we have saved state and backend is accessible
        const currentSavedState = loadStateFromStorage(); // Re-check in case it was cleared
        if (currentSavedState?.projectPath && !cacheStatus.loaded) {
          setProjectPath(currentSavedState.projectPath);
          // Small delay to ensure state is set, then try to load
          setTimeout(async () => {
            try {
              const restoreResult = await loadCacheFromSavedPath(currentSavedState.projectPath);
              if (restoreResult?.needsBackupRestore) {
                setSavedState(currentSavedState);
                return;
              }
              // Update component state with the restored saved state
              setSavedState(currentSavedState);
              if (currentSavedState.currentPage) setCurrentPage(currentSavedState.currentPage);
              if (currentSavedState.pageSize) setPageSize(currentSavedState.pageSize);
              if (currentSavedState.searchQuery) setSearchQuery(currentSavedState.searchQuery);
              if (currentSavedState.currentLine !== undefined) setCurrentLine(currentSavedState.currentLine);
            } catch (err) {
              console.warn('Failed to restore cached project, backend may be unavailable:', err);
              // Clear the invalid saved state
              localStorage.removeItem(CACHE_EDITOR_STATE_KEY);
              setSavedState(null);
              setProjectPath('');
            }
          }, 100);
        }
      } catch (err) {
        console.warn('Backend not available, skipping auto-restore:', err);
        // Clear saved state if backend is not available
        localStorage.removeItem(CACHE_EDITOR_STATE_KEY);
        setSavedState(null);
      }
    };

    initializeState();
  }, []); // Only run on mount

  useEffect(() => {
    if (cacheStatus.loaded) {
      loadCacheItems();
    }
  }, [cacheStatus.loaded, currentPage, searchQuery, pageSize]);

  useEffect(() => {
    const target = pendingProofreadTarget.current;
    if (!target || cacheItems.length === 0) return;
    const rowIndex = cacheItems.findIndex(
      (item) => item.file_path === target.filePath && item.text_index === target.textIndex
    );
    if (rowIndex < 0) return;
    pendingProofreadTarget.current = null;
    setCurrentLine(rowIndex);
    setExpandedProofreadItemId(`${target.filePath}:${target.textIndex}`);
    alignRowsInBothPanes(rowIndex);
  }, [cacheItems]);

  useEffect(() => {
    if (visibleProofreadGroups.length === 0) {
      setCurrentProofreadIndex(0);
      return;
    }
    setCurrentProofreadIndex((value) => Math.min(value, visibleProofreadGroups.length - 1));
  }, [visibleProofreadGroups.length]);

  useEffect(() => {
    if (!expandedProofreadItemId) return;
    const index = visibleProofreadGroups.findIndex(
      (group) => group.item_id === expandedProofreadItemId
    );
    if (index >= 0) setCurrentProofreadIndex(index);
  }, [expandedProofreadItemId, visibleProofreadGroups]);

  // Restore row alignment after data loads
  useEffect(() => {
    if (cacheItems.length > 0 && currentLine >= 0 && currentLine < cacheItems.length) {
      // Small delay to ensure DOM is updated
      setTimeout(() => {
        scrollToRow(sourceScrollRef, currentLine);
        scrollToRow(translationScrollRef, currentLine);
      }, 100);
    }
  }, [cacheItems, currentLine]);

  const loadPageSize = async () => {
    try {
      const response = await fetch('/api/config');
      if (response.ok) {
        const config = await response.json();
        if (config.cache_editor_page_size) {
          setPageSize(config.cache_editor_page_size);
        }
      }
    } catch (err) {
      // Use default value if config loading fails
      console.warn('Failed to load page size from config:', err);
    }
  };

  const checkCacheStatus = async () => {
    const response = await fetch('/api/cache/status');
    if (!response.ok) {
      throw new Error('Backend not available');
    }
    const status = await response.json();
    setCacheStatus(status);
  };

  const loadCacheFromPath = async () => {
    if (!projectPath.trim()) {
      setError(t('cache_editor_enter_project_path'));
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const response = await fetch('/api/cache/load', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_path: projectPath })
      });

      if (!response.ok) {
        const errorData = await response.json();
        if (response.status === 409) {
          const detail = errorData.detail || {};
          setCacheBackups(detail.backups || []);
          setShowBackups(true);
          setBackupMessage(t('cache_editor_backup_select_after_corruption'));
          return;
        }
        throw new Error(errorData.detail || t('cache_editor_failed_load_cache'));
      }

      await response.json();
      await checkCacheStatus(); // Refresh status
      setProofreadPath(projectPath);
      await loadProofreadReports(projectPath, false);
      setError(null);
      setBackupMessage(null);
      if (showBackups) {
        await loadCacheBackups(projectPath);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t('cache_editor_failed_load_cache'));
    } finally {
      setLoading(false);
    }
  };

  const loadCacheFromSavedPath = async (path: string) => {
    const response = await fetch('/api/cache/load', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_path: path })
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({ detail: 'Unknown error' }));
      if (response.status === 409) {
        const detail = errorData.detail || {};
        setCacheBackups(typeof detail === 'object' ? detail.backups || [] : []);
        setShowBackups(true);
        setBackupMessage(t('cache_editor_backup_select_after_corruption'));
        setError(null);
        return { needsBackupRestore: true };
      }
      throw new Error(errorData.detail || 'Failed to load cache from saved path');
    }

    await checkCacheStatus(); // Refresh status
    setProofreadPath(path);
    await loadProofreadReports(path, false);
    console.log('Cache restored from saved path:', path);
    return { needsBackupRestore: false };
  };

  const loadCacheBackups = async (path = projectPath) => {
    if (!path.trim()) {
      setError(t('cache_editor_enter_project_path'));
      return;
    }

    setBackupLoading(true);
    setError(null);
    setBackupMessage(null);

    try {
      const params = new URLSearchParams({ project_path: path });
      const response = await fetch(`/api/cache/backups?${params}`);

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || t('cache_editor_backups_load_failed'));
      }

      const data = await response.json();
      setCacheBackups(data.backups || []);
      setShowBackups(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('cache_editor_backups_load_failed'));
    } finally {
      setBackupLoading(false);
    }
  };

  const restoreCacheBackup = async (backup: CacheBackup) => {
    if (!projectPath.trim()) {
      setError(t('cache_editor_enter_project_path'));
      return;
    }

    const confirmed = window.confirm(t('cache_editor_backup_restore_confirm', backup.file));
    if (!confirmed) {
      return;
    }

    setBackupRestoring(backup.file);
    setError(null);
    setBackupMessage(null);

    try {
      const response = await fetch('/api/cache/restore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_path: projectPath,
          backup_file: backup.file
        })
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || t('cache_editor_backup_restore_failed'));
      }

      await checkCacheStatus();
      setCurrentPage(1);
      await loadCacheItems(1);
      if (activeProofreadReport) {
        await loadProofreadReport(activeProofreadReport, projectPath);
      }
      setShowBackups(false);
      setCacheBackups([]);
      setError(null);
      setBackupMessage(t('cache_editor_backup_restored', backup.file));
    } catch (err) {
      setError(err instanceof Error ? err.message : t('cache_editor_backup_restore_failed'));
    } finally {
      setBackupRestoring(null);
    }
  };

  const loadCacheItems = async (page = currentPage, search = searchQuery) => {
    setLoading(true);
    setCurrentLine(0);
    try {
      const params = new URLSearchParams({
        page: page.toString(),
        page_size: pageSize.toString()
      });

      if (search) {
        params.append('search', search);
      }

      const response = await fetch(`/api/cache/items?${params}`);

      if (!response.ok) {
        throw new Error(t('cache_editor_failed_load_cache'));
      }

      const data = await response.json();
      setCacheItems(data.items);
      setPagination(data.pagination);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('cache_editor_failed_load_cache'));
    } finally {
      setLoading(false);
    }
  };

  const updateCacheItem = async (itemId: number, newTranslation: string) => {
    try {
      const response = await fetch(`/api/cache/items/${itemId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          item_id: itemId,
          translation: newTranslation,
          project_path: projectPath,
          report_file: reportQueryFile() || null,
        })
      });

      if (!response.ok) {
        throw new Error(t('cache_editor_failed_update_item'));
      }

      const data = await response.json();

      // Update local state
      setCacheItems(items =>
        items.map(item =>
          item.id === itemId
            ? { ...item, translation: newTranslation, modified: true }
            : item
        )
      );

      if (data.report) {
        applyProofreadReport(data.report as ProofreadReportPayload);
      } else if (activeProofreadReport) {
        await loadProofreadReport(activeProofreadReport, projectPath);
      }

      setEditingItem(null);
      setEditingText('');
    } catch (err) {
      setError(err instanceof Error ? err.message : t('cache_editor_failed_update_item'));
    }
  };

  const handleEditStart = (item: CacheItem) => {
    setEditingItem(item.id);
    setEditingText(item.translation);
    setLineAnalysisResult(null);
  };

  const runSingleLineAnalysis = async (item: CacheItem) => {
    setAnalyzingLine(true);
    setLineAnalysisResult(null);
    try {
      // Determine what text to send: the one currently in the editor or the one in the list
      const translationToSend = (editingItem === item.id) ? editingText : item.translation;

      const response = await fetch('/api/proofread/single_check', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_path: projectPath,
          file_path: item.file_path,
          text_index: item.text_index,
          translation: translationToSend
        })
      });
      
      const result = await response.json();
      setLineAnalysisResult(result);
    } catch (err) {
      setLineAnalysisResult({ has_issues: true, message: 'Analysis failed' });
    } finally {
      setAnalyzingLine(false);
    }
  };

  const acceptLineSuggestion = () => {
    if (lineAnalysisResult?.corrected_translation) {
      setEditingText(lineAnalysisResult.corrected_translation);
    }
  };

  const handleEditSave = async () => {
    if (editingItem !== null) {
      await updateCacheItem(editingItem, editingText);
    }
  };

  const handleEditCancel = async () => {
    if (editingItem !== null) {
      const originalItem = cacheItems.find(item => item.id === editingItem);
      const hasChanges = originalItem && editingText !== originalItem.translation;

      if (hasChanges && editingText.trim() !== '') {
        // Auto-save on cancel if there are changes
        await updateCacheItem(editingItem, editingText);
      }
    }

    setEditingItem(null);
    setEditingText('');
  };

  // AI Proofread functions
  const startProofread = async () => {
    if (!proofreadPath.trim()) return;
    
    // Auto-load cache for this path if not already loaded
    if (projectPath !== proofreadPath || !cacheStatus.loaded) {
      setLoading(true);
      try {
        setProjectPath(proofreadPath);
        await loadCacheFromSavedPath(proofreadPath);
      } catch (err) {
        setError(t('cache_editor_failed_load_cache'));
        setLoading(false);
        return;
      }
    }

    try {
      let response = await fetch('/api/proofread/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_path: proofreadPath,
          suggestion_mode: proofreadSuggestionMode,
        })
      });
      if (response.status === 409) {
        const conflict = await response.json();
        if (conflict.detail?.code === 'proofread_overwrite_confirmation_required') {
          const counts = conflict.detail.summary?.status_counts || {};
          const confirmed = window.confirm(
            t(
              'cache_editor_proofread_overwrite_confirm',
              counts.pending || 0,
              counts.accepted || 0,
              counts.conflict || 0,
            )
          );
          if (!confirmed) return;
          response = await fetch('/api/proofread/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              project_path: proofreadPath,
              report_mode: 'overwrite',
              suggestion_mode: proofreadSuggestionMode,
              overwrite_confirmed: true,
            }),
          });
        }
      }
      if (response.ok) {
        setProofreadState(prev => ({ ...prev, running: true, error: null, completed: false }));
      } else {
        const data = await response.json();
        setError(data.detail || t('cache_editor_proofread_error'));
      }
    } catch (err) {
      setError(t('cache_editor_proofread_error'));
    } finally {
      setLoading(false);
    }
  };

  const stopProofread = async () => {
    try {
      await fetch('/api/proofread/stop', { method: 'POST' });
      setProofreadState(prev => ({ ...prev, running: false }));
    } catch (err) {
      console.error('Failed to stop proofread:', err);
    }
  };

  // Shared function to scroll a container to align a specific row
  const scrollToRow = (containerRef: React.RefObject<HTMLDivElement>, rowIndex: number) => {
    const container = containerRef.current;
    if (container) {
      const rowElements = container.querySelectorAll('[data-row-index]');
      const targetRow = rowElements[rowIndex] as HTMLElement;

      if (targetRow) {
        if (rowIndex <= 0) {
          container.scrollTop = 0;
          return;
        }

        const containerRect = container.getBoundingClientRect();
        const rowRect = targetRow.getBoundingClientRect();
        const rowTop = rowRect.top - containerRect.top + container.scrollTop;

        // Center the target row in the viewport
        const scrollTop = rowTop - (container.clientHeight / 2) + (rowRect.height / 2);
        container.scrollTop = Math.max(0, scrollTop);
      }
    }
  };

  // Align both panes to show the same row at the same visual position
  const alignRowsInBothPanes = (index: number) => {
    setTimeout(() => {
      scrollToRow(sourceScrollRef, index);
      scrollToRow(translationScrollRef, index);
    }, 0);
  };

  const navigateToProofreadGroupData = async (
    group: ProofreadLineGroup,
    index: number,
    filter = proofreadFilter,
    suggestionId?: string,
  ) => {
    setCurrentProofreadIndex(index);
    setExpandedProofreadItemId(group.item_id);
    const activeSuggestion = group.suggestions.find((item) => item.suggestion_id === suggestionId)
      || group.suggestions.find((item) => item.status === filter)
      || group.suggestions.find((item) => item.status === 'pending' || item.status === 'conflict')
      || group.suggestions[0];
    if (activeSuggestion) {
      setProofreadSuggestionOffsets((current) => ({
        ...current,
        [group.item_id]: group.suggestions.indexOf(activeSuggestion),
      }));
    }
    persistProofreadReviewState(activeSuggestion?.suggestion_id, filter).catch(() => undefined);
    try {
      const params = new URLSearchParams({
        project_path: projectPath,
        file_path: group.file_path,
        text_index: String(group.text_index),
        page_size: String(pageSize),
      });
      const response = await fetch(`/api/proofread/locate?${params}`);
      if (!response.ok) throw new Error(t('cache_editor_proofread_locate_failed'));
      const location = await response.json();
      pendingProofreadTarget.current = {
        filePath: group.file_path,
        textIndex: group.text_index,
      };
      if (searchQuery) setSearchQuery('');
      if (currentPage !== location.page || searchQuery) {
        setCurrentPage(location.page);
        await loadCacheItems(location.page, '');
      } else {
        setCurrentLine(location.row_index);
        alignRowsInBothPanes(location.row_index);
        pendingProofreadTarget.current = null;
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t('cache_editor_proofread_locate_failed'));
    }
  };

  const navigateToProofreadGroup = async (index: number) => {
    const group = visibleProofreadGroups[index];
    if (!group) return;
    await navigateToProofreadGroupData(group, index);
  };

  const handleRowClick = (index: number) => {
    setCurrentLine(index);
    alignRowsInBothPanes(index);
  };

  const handleRowDoubleClick = (item: CacheItem, index: number) => {
    setCurrentLine(index);
    handleEditStart(item);
    alignRowsInBothPanes(index);
  };

  const activeSuggestionIndexForGroup = (group?: ProofreadLineGroup) => {
    if (!group || group.suggestions.length === 0) return -1;
    if (Object.prototype.hasOwnProperty.call(proofreadSuggestionOffsets, group.item_id)) {
      return Math.min(
        Math.max(proofreadSuggestionOffsets[group.item_id], 0),
        group.suggestions.length - 1,
      );
    }
    const filteredIndex = group.suggestions.findIndex(
      (suggestion) => suggestion.status === proofreadFilter
    );
    if (filteredIndex >= 0) return filteredIndex;
    const actionableIndex = group.suggestions.findIndex(
      (suggestion) => suggestion.status === 'pending' || suggestion.status === 'conflict'
    );
    return actionableIndex >= 0 ? actionableIndex : 0;
  };

  const activeSuggestionForGroup = (group?: ProofreadLineGroup) => {
    if (!group || group.suggestions.length === 0) return undefined;
    return group.suggestions[activeSuggestionIndexForGroup(group)];
  };

  const moveInlineSuggestion = (group: ProofreadLineGroup, delta: number) => {
    setProofreadSuggestionOffsets((current) => {
      const offset = activeSuggestionIndexForGroup(group);
      const next = (offset + delta + group.suggestions.length) % group.suggestions.length;
      return { ...current, [group.item_id]: next };
    });
  };

  const rowProofreadClass = (group?: ProofreadLineGroup) => {
    if (group?.displayStatus === 'conflict') return 'border-l-2 border-l-rose-400 bg-rose-400/[0.055]';
    if (group?.displayStatus === 'pending') return 'border-l-2 border-l-amber-300 bg-amber-300/[0.055]';
    return 'border-l-2 border-l-transparent';
  };

  const rowProofreadMarker = (group?: ProofreadLineGroup) => {
    if (!group) return '';
    if (group.displayStatus === 'completed') return '';
    if (group.displayStatus === 'pending') return '#';
    if (group.displayStatus === 'conflict') return '!';
    if (group.displayStatus === 'accepted') return '*';
    return '';
  };

  const rowProofreadMarkerClass = (group?: ProofreadLineGroup) => {
    if (group?.displayStatus === 'pending') return 'text-amber-300';
    if (group?.displayStatus === 'conflict') return 'text-rose-300';
    if (group?.displayStatus === 'accepted') return 'text-emerald-300';
    return 'text-slate-600';
  };

  // Keyboard navigation (only Enter and Esc)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (editingItem !== null && e.key === 'Escape') {
        e.preventDefault();
        handleEditCancel();
      } else if (editingItem === null && e.key === 'Enter' && cacheItems[currentLine]) {
        e.preventDefault();
        handleEditStart(cacheItems[currentLine]);
        setTimeout(() => {
          scrollToRow(sourceScrollRef, currentLine);
          scrollToRow(translationScrollRef, currentLine);
        }, 0);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [currentLine, editingItem, cacheItems]);

  // Scroll synchronization with debounce to prevent recursion
  const handleScrollSync = (source: 'left' | 'right') => (e: React.UIEvent<HTMLDivElement>) => {
    // Prevent recursive sync calls
    if (scrollSyncActive.current) return;

    const scrollTop = e.currentTarget.scrollTop;

    scrollSyncActive.current = true;

    if (source === 'left' && translationScrollRef.current) {
      translationScrollRef.current.scrollTop = scrollTop;
    } else if (source === 'right' && sourceScrollRef.current) {
      sourceScrollRef.current.scrollTop = scrollTop;
    }

    // Reset sync flag after a brief delay
    setTimeout(() => {
      scrollSyncActive.current = false;
    }, 10);
  };

  return (
    <div className="grid h-full min-h-0 grid-cols-1 grid-rows-[auto_auto_minmax(0,1fr)] gap-x-4 overflow-hidden bg-transparent cache-editor-container min-[1800px]:grid-cols-[280px_minmax(720px,1fr)_320px]">
      {/* Top Header Bar - Always visible */}
      <div className="col-start-1 row-start-1 flex items-center justify-between border-b border-white/5 bg-surface/50 p-4 backdrop-blur-md min-[1800px]:col-start-2">
        <h1 className={`text-xl font-black tracking-tighter uppercase ${elysiaActive ? 'text-pink-500' : ''}`}>
          {t('cache_editor_title')}
        </h1>
      </div>

      {/* Project Control Panel - Always visible */}
      <div className="col-start-1 row-start-2 space-y-3 border-b border-white/5 bg-surface/30 p-4 backdrop-blur-sm min-[1800px]:col-start-2">
        {/* AI Model Capability Warning */}
        <div className="p-3 bg-yellow-500/10 border border-yellow-500/30 rounded-lg flex items-center gap-3 animate-in fade-in slide-in-from-top-2">
            <AlertTriangle className="text-yellow-500 shrink-0" size={20} />
            <p className="text-xs md:text-sm font-bold text-yellow-500 leading-relaxed">
                {t('cache_editor_ai_model_warning')}
            </p>
        </div>

        {/* Load Cache Row */}
        <div className="grid grid-cols-2 gap-3 sm:flex sm:items-center sm:gap-4">
          <div className="col-span-2 w-full flex-1">
            <div className="relative group">
              <span className={`absolute left-3 top-1/2 -translate-y-1/2 text-[9px] font-black uppercase tracking-wider transition-colors ${getLabelColor()}`}>CACHE</span>
              <input
                type="text"
                value={projectPath}
                onChange={(e) => {
                  setProjectPath(e.target.value);
                  setCacheBackups([]);
                  setBackupMessage(null);
                }}
                placeholder={t('cache_editor_project_path_placeholder')}
                className="w-full pl-16 pr-3 py-2 bg-slate-950/50 border border-white/10 rounded-lg focus:border-primary focus:ring-1 focus:ring-primary transition-all text-sm text-white"
              />
            </div>
          </div>
          <button
            onClick={loadCacheFromPath}
            disabled={loading || !projectPath.trim()}
            className={`flex min-h-11 w-full min-w-[120px] items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-bold text-slate-900 shadow-lg transition-all sm:w-auto ${
                elysiaActive ? 'bg-pink-500 hover:bg-pink-600 shadow-pink-500/20' : 'bg-primary hover:bg-cyan-400 shadow-primary/20'
            } disabled:opacity-50`}
          >
            {loading ? (
              <span className="animate-spin h-4 w-4 border-2 border-white border-t-transparent rounded-full"></span>
            ) : null}
            {t('cache_editor_load_cache')}
          </button>
          <button
            onClick={() => showBackups ? setShowBackups(false) : loadCacheBackups()}
            disabled={backupLoading || !projectPath.trim()}
            className="flex min-h-11 w-full items-center justify-center gap-2 rounded-lg border border-white/10 bg-white/5 px-4 py-2 text-sm text-slate-300 transition-colors hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto"
            title={t('cache_editor_backups')}
            aria-expanded={showBackups}
          >
            {backupLoading ? (
              <RefreshCw size={16} className="animate-spin" />
            ) : (
              <DatabaseBackup size={16} />
            )}
            {t('cache_editor_backups')}
          </button>
          {cacheStatus.loaded && (
            <button
              onClick={() => {
                setCacheStatus({ loaded: false, file_count: 0, total_items: 0, project_name: null });
                setCacheItems([]);
                setPagination(null);
                setCurrentPage(1);
                setSearchQuery('');
                setCurrentLine(0);
                setCacheBackups([]);
                setShowBackups(false);
                setBackupMessage(null);
                localStorage.removeItem(CACHE_EDITOR_STATE_KEY);
                setSavedState(null);
              }}
              className="col-span-2 min-h-11 w-full rounded-lg border border-white/5 bg-white/5 px-4 py-2 text-sm text-slate-400 transition-colors hover:bg-white/10 sm:w-auto"
            >
              {t('cache_editor_switch_project')}
            </button>
          )}
        </div>

        {showBackups && (
          <div className="overflow-hidden rounded-lg border border-white/10 bg-slate-950/40">
            <div className="flex items-center justify-between gap-3 px-3 py-2 border-b border-white/5">
              <div className="flex items-center gap-2 min-w-0">
                <DatabaseBackup size={16} className={elysiaActive ? 'text-pink-400' : 'text-cyan-400'} />
                <span className="text-xs font-bold text-slate-200">{t('cache_editor_backups')}</span>
                <span className="text-[10px] text-slate-500">{cacheBackups.length}</span>
              </div>
              <button
                onClick={() => loadCacheBackups()}
                disabled={backupLoading}
                className="px-3 py-1.5 min-h-9 rounded-md bg-white/5 text-slate-400 hover:bg-white/10 hover:text-white transition-colors text-[11px] font-bold flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <RefreshCw size={13} className={backupLoading ? 'animate-spin' : ''} />
                {t('cache_editor_backup_refresh')}
              </button>
            </div>
            {backupMessage && (
              <div className="px-3 py-2 text-xs text-green-300 bg-green-500/10 border-b border-green-500/20">
                {backupMessage}
              </div>
            )}
            {backupLoading ? (
              <div className="px-3 py-8 text-center text-xs text-slate-500">
                {t('cache_editor_loading')}
              </div>
            ) : cacheBackups.length === 0 ? (
              <div className="px-3 py-8 text-center text-xs text-slate-500">
                {t('cache_editor_backups_empty')}
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse text-[11px]">
                  <thead className="bg-white/[0.03]">
                    <tr>
                      <th className="px-3 py-2 font-bold text-slate-500 uppercase tracking-tight whitespace-nowrap">{t('cache_editor_backup_modified')}</th>
                      <th className="px-3 py-2 font-bold text-slate-500 uppercase tracking-tight whitespace-nowrap">{t('cache_editor_backup_progress')}</th>
                      <th className="px-3 py-2 font-bold text-slate-500 uppercase tracking-tight whitespace-nowrap">{t('cache_editor_backup_size')}</th>
                      <th className="px-3 py-2 font-bold text-slate-500 uppercase tracking-tight">{t('cache_editor_project_path')}</th>
                      <th className="px-3 py-2 font-bold text-slate-500 uppercase tracking-tight">{t('cache_editor_backup_file')}</th>
                      <th className="px-3 py-2 font-bold text-slate-500 uppercase tracking-tight text-right whitespace-nowrap">{t('options_label')}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/5">
                    {cacheBackups.map((backup) => (
                      <tr key={backup.file} className="hover:bg-white/[0.03] transition-colors">
                        <td className="px-3 py-2 text-slate-300 whitespace-nowrap font-mono">{backup.modified_time}</td>
                        <td className="px-3 py-2 text-slate-300 whitespace-nowrap">
                          <div className="flex items-center gap-2">
                            <span className="font-mono">{backup.completion_label}</span>
                            <div className="h-1.5 w-16 rounded-full bg-white/10 overflow-hidden">
                              <div
                                className={`h-full rounded-full ${elysiaActive ? 'bg-pink-400' : 'bg-cyan-400'}`}
                                style={{ width: `${Math.min(Math.max(backup.completion_rate, 0), 100)}%` }}
                              />
                            </div>
                          </div>
                        </td>
                        <td className="px-3 py-2 text-slate-400 whitespace-nowrap">{backup.size_label}</td>
                        <td className="px-3 py-2 text-slate-400 max-w-[180px] truncate" title={backup.project_name}>{backup.project_name}</td>
                        <td className="px-3 py-2 text-slate-500 max-w-[260px] truncate font-mono" title={backup.file}>{backup.file}</td>
                        <td className="px-3 py-2 text-right">
                          <button
                            onClick={() => restoreCacheBackup(backup)}
                            disabled={backupRestoring !== null}
                            className={`px-3 py-1.5 min-h-9 rounded-md text-[11px] font-bold transition-all inline-flex items-center gap-1.5 ${
                              elysiaActive
                                ? 'bg-pink-500/15 text-pink-300 hover:bg-pink-500 hover:text-white'
                                : 'bg-cyan-500/15 text-cyan-300 hover:bg-cyan-500 hover:text-slate-950'
                            } disabled:opacity-50 disabled:cursor-not-allowed`}
                          >
                            {backupRestoring === backup.file ? (
                              <RefreshCw size={13} className="animate-spin" />
                            ) : (
                              <ArchiveRestore size={13} />
                            )}
                            {t('cache_editor_backup_restore')}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* AI Proofread Row */}
        <div className="grid grid-cols-2 gap-3 sm:flex sm:items-center sm:gap-4">
          <div className="col-span-2 w-full flex-1">
            <div className="relative group">
              <span className={`absolute left-3 top-1/2 -translate-y-1/2 text-[9px] font-black uppercase tracking-tight transition-colors ${elysiaActive ? 'text-pink-400' : 'text-yellow-600'}`}>PROOFREAD</span>
              <input
                type="text"
                value={proofreadPath}
                onChange={(e) => setProofreadPath(e.target.value)}
                placeholder={t('cache_editor_project_path_placeholder')}
                className="w-full pl-24 pr-3 py-2 bg-slate-950/50 border border-white/10 rounded-lg focus:border-yellow-500 focus:ring-1 focus:ring-yellow-500 transition-all text-sm text-white"
              />
            </div>
          </div>
          <div className="grid w-full shrink-0 grid-cols-2 gap-1 rounded-lg border border-white/10 bg-slate-950/50 p-1 sm:w-auto">
            {(['proofread', 'annotation'] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                onClick={() => setProofreadSuggestionMode(mode)}
                disabled={proofreadState.running}
                className={`min-h-9 rounded-md px-3 text-[11px] font-semibold transition-colors ${
                  proofreadSuggestionMode === mode
                    ? 'bg-yellow-500 text-slate-950'
                    : 'text-slate-400 hover:bg-slate-800 hover:text-white'
                } disabled:opacity-50`}
              >
                {t(`setting_proofread_suggestion_mode_${mode}`)}
              </button>
            ))}
          </div>
          <button
            onClick={startProofread}
            disabled={proofreadState.running || !proofreadPath.trim()}
            className={`flex min-h-11 w-full min-w-[120px] items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-bold text-white shadow-lg transition-all sm:w-auto ${
                elysiaActive ? 'bg-purple-500 hover:bg-purple-600 shadow-purple-500/20' : 'bg-yellow-600 hover:bg-yellow-700 shadow-yellow-600/20'
            } disabled:opacity-50`}
          >
            {proofreadState.running ? (
              <span className="animate-spin h-4 w-4 border-2 border-white border-t-transparent rounded-full"></span>
            ) : null}
            {t('cache_editor_start_proofread')}
          </button>
          {proofreadState.running && (
            <button
              onClick={stopProofread}
              className="min-h-11 w-full rounded-lg border border-rose-400/25 bg-rose-400/10 px-4 py-2 text-sm font-bold text-rose-300 sm:w-auto"
            >
              {t('cache_editor_stop_proofread')} {proofreadState.progress}/{proofreadState.total}
            </button>
          )}
        </div>

        {error && (
          <div className="mt-2 bg-red-500/20 border border-red-500/30 text-red-100 px-4 py-2 rounded-lg flex justify-between items-center animate-in fade-in slide-in-from-top-1 text-xs">
            <span>{error}</span>
            <button onClick={() => setError(null)} className="text-red-400 hover:text-white transition-colors">✕</button>
          </div>
        )}
      </div>

      {cacheStatus.loaded && proofreadReports.length > 0 && (
        <aside
          data-proofread-layout="report-sidebar"
          className="hidden min-h-0 pt-4 min-[1800px]:col-start-1 min-[1800px]:row-start-3 min-[1800px]:block"
        >
          <div className="flex justify-end">
            <ProofreadReportPanel
              t={t}
              options={proofreadReports}
              activeFile={activeProofreadReport}
              run={proofreadRun}
              summary={proofreadSummary}
              filter={proofreadFilter}
              collapsed={reportPanelCollapsed}
              onToggle={() => setReportPanelCollapsed((value) => !value)}
              onUndo={undoProofreadAction}
              onSelectReport={(file) => {
                setActiveProofreadReport(file);
                loadProofreadReport(file).catch((err) => setError(String(err)));
              }}
              onFilter={(filter) => {
                setProofreadFilter(filter);
                setCurrentProofreadIndex(0);
                persistProofreadReviewState(undefined, filter).catch(() => undefined);
              }}
            />
          </div>
        </aside>
      )}

      {/* Cache Editor Content - Only show when loaded */}
      {cacheStatus.loaded && (
        <div
          data-proofread-layout="editor"
          className="col-start-1 row-start-3 flex min-h-0 flex-col overflow-hidden min-[1800px]:col-start-2"
        >
          {/* Top navigation bar */}
          <div className="flex flex-col gap-3 border-b border-white/5 bg-surface/30 p-4 backdrop-blur-sm sm:flex-row sm:items-center sm:justify-between">
            <div className="grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-2 sm:flex sm:w-auto sm:gap-4">
              <span className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">
                {t('cache_editor_line_info',
                  currentLine + 1 + (currentPage - 1) * pageSize,
                  pagination?.total_items || 0,
                  currentPage,
                  pagination?.total_pages || 0
                )}
              </span>
            </div>
            <div className="flex items-center gap-4">
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder={t('cache_editor_search_placeholder')}
                className="w-full rounded-lg border border-white/10 bg-slate-950/50 px-3 py-1 text-xs text-white outline-none transition-all focus:border-primary focus:ring-1 focus:ring-primary sm:w-48"
              />
              <button
                onClick={() => {
                  setCacheStatus({ loaded: false, file_count: 0, total_items: 0, project_name: null });
                  setCacheItems([]);
                  setPagination(null);
                  setCurrentPage(1);
                  setSearchQuery('');
                  setProjectPath('');
                  setCurrentLine(0);
                  setCacheBackups([]);
                  setShowBackups(false);
                  setBackupMessage(null);
                  // Clear saved state from localStorage and component state
                  localStorage.removeItem(CACHE_EDITOR_STATE_KEY);
                  setSavedState(null);
                }}
                className="whitespace-nowrap rounded-lg border border-white/5 bg-white/5 px-3 py-1 text-[10px] font-bold uppercase text-slate-400 transition-colors hover:bg-white/10"
              >
                {t('cache_editor_switch_project')}
              </button>
            </div>
          </div>

          {proofreadReports.length > 0 && (
            <div
              data-proofread-layout="flow-tools"
              className="flex-none border-b border-white/5 bg-surface/20 p-3 min-[1800px]:hidden"
            >
              <div className="grid grid-cols-2 gap-2 sm:hidden">
                <button
                  type="button"
                  onClick={() => setMobileProofreadPanel((current) => current === 'report' ? null : 'report')}
                  className="inline-flex min-h-10 items-center justify-center gap-2 rounded-md border border-slate-700 bg-slate-950/90 px-3 text-xs font-semibold text-amber-300"
                >
                  <FileCheck2 size={15} />
                  {t('cache_editor_proofread_report')} #{proofreadSummary.status_counts?.pending || 0}
                </button>
                <button
                  type="button"
                  onClick={() => setMobileProofreadPanel((current) => current === 'navigator' ? null : 'navigator')}
                  className="inline-flex min-h-10 items-center justify-center gap-2 rounded-md border border-slate-700 bg-slate-950/90 px-3 text-xs font-semibold text-amber-300"
                >
                  <ListChecks size={15} />
                  {t('cache_editor_proofread_quick_nav', visibleProofreadGroups.length > 0 ? currentProofreadIndex + 1 : 0, visibleProofreadGroups.length)}
                </button>
              </div>
              {mobileProofreadPanel && (
                <div className="mt-2 flex min-w-0 justify-center sm:hidden">
                  {mobileProofreadPanel === 'report' ? (
                    <ProofreadReportPanel
                      t={t}
                      options={proofreadReports}
                      activeFile={activeProofreadReport}
                      run={proofreadRun}
                      summary={proofreadSummary}
                      filter={proofreadFilter}
                      collapsed={false}
                      onToggle={() => setMobileProofreadPanel(null)}
                      onUndo={undoProofreadAction}
                      onSelectReport={(file) => {
                        setActiveProofreadReport(file);
                        loadProofreadReport(file).catch((err) => setError(String(err)));
                      }}
                      onFilter={(filter) => {
                        setProofreadFilter(filter);
                        setCurrentProofreadIndex(0);
                        persistProofreadReviewState(undefined, filter).catch(() => undefined);
                      }}
                    />
                  ) : (
                    <ProofreadQuickNavigator
                      t={t}
                      groups={visibleProofreadGroups}
                      index={currentProofreadIndex}
                      collapsed={false}
                      onToggle={() => setMobileProofreadPanel(null)}
                      onSelect={(index) => {
                        setMobileProofreadPanel(null);
                        navigateToProofreadGroup(index);
                      }}
                    />
                  )}
                </div>
              )}
              <div className="hidden grid-cols-2 gap-3 sm:grid">
                <div className="flex min-w-0 items-start justify-start">
                <ProofreadReportPanel
                  t={t}
                  options={proofreadReports}
                  activeFile={activeProofreadReport}
                  run={proofreadRun}
                  summary={proofreadSummary}
                  filter={proofreadFilter}
                  collapsed={reportPanelCollapsed}
                  onToggle={() => setReportPanelCollapsed((value) => !value)}
                  onUndo={undoProofreadAction}
                  onSelectReport={(file) => {
                    setActiveProofreadReport(file);
                    loadProofreadReport(file).catch((err) => setError(String(err)));
                  }}
                  onFilter={(filter) => {
                    setProofreadFilter(filter);
                    setCurrentProofreadIndex(0);
                    persistProofreadReviewState(undefined, filter).catch(() => undefined);
                  }}
                />
                </div>
                <div className="flex min-w-0 items-start justify-end">
                <ProofreadQuickNavigator
                  t={t}
                  groups={visibleProofreadGroups}
                  index={currentProofreadIndex}
                  collapsed={quickNavigatorCollapsed}
                  onToggle={() => setQuickNavigatorCollapsed((value) => !value)}
                  onSelect={navigateToProofreadGroup}
                />
                </div>
              </div>
            </div>
          )}

          {/* Main unified editor area */}
          <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
            {/* Header for the panes */}
            <div className="flex bg-surface/50 border-b border-white/5 backdrop-blur-sm sticky top-0 z-20">
              <div className="flex-1 px-4 py-2 border-r border-white/5">
                <h3 className={`text-xs font-bold uppercase tracking-widest ${elysiaActive ? 'text-pink-400' : 'text-purple-400'}`}>
                  {t('cache_editor_source_text')}
                </h3>
              </div>
              <div className="flex-1 px-4 py-2">
                <h3 className={`text-xs font-bold uppercase tracking-widest ${
                  editingItem !== null 
                    ? (elysiaActive ? 'text-pink-300' : 'text-red-400') 
                    : (elysiaActive ? 'text-pink-400' : 'text-green-400')
                }`}>
                  {t('cache_editor_translation')} {editingItem !== null ? t('cache_editor_editing') : ''}
                </h3>
              </div>
            </div>

            {/* Scrollable Rows Container */}
            <div 
              ref={sourceScrollRef}
              className="flex-1 overflow-y-auto custom-scrollbar"
            >
              {cacheItems.length === 0 ? (
                <div className="text-center text-slate-500 py-20 italic">
                  {loading ? t('cache_editor_loading') : t('cache_editor_no_source_loaded')}
                </div>
              ) : (
                cacheItems.map((item, index) => {
                  const itemId = `${item.file_path}:${item.text_index}`;
                  const proofreadGroup = proofreadGroupByItemId.get(itemId);
                  const activeSuggestion = activeSuggestionForGroup(proofreadGroup);
                  const suggestionOffset = activeSuggestionIndexForGroup(proofreadGroup);
                  return (
                  <div 
                    key={item.id}
                    data-row-index={index}
                    data-file-path={item.file_path}
                    data-text-index={item.text_index}
                    className={`group flex border-b border-white/[0.02] transition-colors ${rowProofreadClass(proofreadGroup)} ${
                      index === currentLine ? 'bg-white/[0.03]' : 'hover:bg-white/[0.01]'
                    }`}
                    onClick={() => {
                      handleRowClick(index);
                      if (proofreadGroup) {
                        setExpandedProofreadItemId((current) => current === itemId ? '' : itemId);
                        const groupIndex = visibleProofreadGroups.findIndex((group) => group.item_id === itemId);
                        if (groupIndex >= 0) setCurrentProofreadIndex(groupIndex);
                      }
                    }}
                  >
                    {/* Source Pane */}
                    <div className={`flex-1 p-3 border-r border-white/5 ${index === currentLine ? 'opacity-100' : 'opacity-60'}`}>
                      <div className="text-[10px] text-slate-600 mb-1 font-mono">
                        {index + 1 + (currentPage - 1) * pageSize}. {item.file_path.split('/').pop()}:{item.text_index}
                      </div>
                      <div className={`text-sm leading-relaxed ${index === currentLine ? 'text-white' : 'text-slate-300'}`}>
                        {item.source || <em className="text-slate-700">{t('cache_editor_no_source_text')}</em>}
                      </div>
                    </div>

                    {/* Translation Pane */}
                    <div 
                      className={`flex-1 p-3 transition-all ${
                        index === currentLine && editingItem === item.id
                          ? 'bg-white/[0.05]'
                          : ''
                      }`}
                      onDoubleClick={() => handleRowDoubleClick(item, index)}
                    >
                      <div className="flex items-center justify-between text-[10px] text-slate-600 mb-1 group-hover:text-slate-400 transition-colors">
                        <div className="flex items-center gap-2">
                            {rowProofreadMarker(proofreadGroup) && (
                              <span className={`text-sm font-black ${rowProofreadMarkerClass(proofreadGroup)}`}>
                                {rowProofreadMarker(proofreadGroup)}
                                {proofreadGroup && proofreadGroup.suggestions.length > 1 ? proofreadGroup.suggestions.length : ''}
                              </span>
                            )}
                            <span className="font-mono">{item.text_index}</span>
                            <button
                                onClick={(e) => {
                                    e.stopPropagation();
                                    handleEditStart(item);
                                    runSingleLineAnalysis(item);
                                }}
                                className={`opacity-0 group-hover:opacity-100 transition-opacity px-1.5 py-0.5 rounded text-[9px] font-bold border ${
                                    elysiaActive ? 'border-pink-500/30 text-pink-400 hover:bg-pink-500/10' : 'border-cyan-500/30 text-cyan-400 hover:bg-cyan-500/10'
                                }`}
                                title={t('cache_editor_single_check_tooltip')}
                            >
                                {t('cache_editor_single_check_btn')}
                            </button>
                        </div>
                        {item.modified && !rowProofreadMarker(proofreadGroup) && (
                          <span className="text-emerald-400 text-sm font-black" title={t('cache_editor_modified')}>*</span>
                        )}
                      </div>

                      {editingItem === item.id ? (
                        <div className="space-y-2 animate-in fade-in zoom-in-95 duration-200">
                          <textarea
                            value={editingText}
                            onChange={(e) => setEditingText(e.target.value)}
                            className={`w-full h-32 p-3 bg-slate-950/80 border rounded-lg resize-none focus:outline-none focus:ring-2 transition-all text-sm leading-relaxed ${
                                elysiaActive ? 'border-pink-500/50 focus:ring-pink-500/30' : 'border-primary/50 focus:ring-primary/30'
                            }`}
                            autoFocus
                          />
                          
                          {/* AI Analysis Result Display */}
                          {(analyzingLine || lineAnalysisResult) && (
                            <div className="mt-2 p-2 rounded bg-slate-900/50 border border-white/5 text-xs animate-in slide-in-from-top-2">
                                {analyzingLine ? (
                                    <div className="flex items-center gap-2 text-slate-400">
                                        <span className="animate-spin h-3 w-3 border-2 border-slate-400 border-t-transparent rounded-full"></span>
                                        {t('cache_editor_analyzing_text')}
                                    </div>
                                ) : lineAnalysisResult ? (
                                    <div>
                                        {lineAnalysisResult.has_issues ? (
                                            <div className="space-y-2">
                                                <div className="font-bold text-yellow-400 flex items-center justify-between">
                                                    <span>{t('cache_editor_issues_found_title')}</span>
                                                    {lineAnalysisResult.corrected_translation && (
                                                        <button 
                                                            onClick={acceptLineSuggestion}
                                                            className="px-2 py-0.5 bg-green-600/20 text-green-400 hover:bg-green-600/30 rounded text-[9px] font-bold transition-colors"
                                                        >
                                                            {t('cache_editor_apply_fix_btn')}
                                                        </button>
                                                    )}
                                                </div>
                                                {lineAnalysisResult.issues?.map((issue: any, idx: number) => (
                                                    <div key={idx} className="pl-2 border-l-2 border-yellow-500/30">
                                                        <div className="text-slate-300 font-bold">{t(`cache_editor_proofread_${issue.type}`) || issue.type} <span className="text-slate-500 font-normal">({t(`cache_editor_proofread_${issue.severity}`) || issue.severity})</span></div>
                                                        <div className="text-slate-400">{issue.description}</div>
                                                        {issue.suggestion && <div className="text-green-400/80 mt-0.5">{t('cache_editor_proofread_suggestion')}: {issue.suggestion}</div>}
                                                    </div>
                                                ))}
                                                {lineAnalysisResult.corrected_translation && (
                                                    <div className="mt-2 pt-2 border-t border-white/5">
                                                        <span className="text-slate-500 block mb-1">{t('cache_editor_proposed_fix_title')}</span>
                                                        <div className="text-green-300 font-mono bg-black/20 p-1 rounded">{lineAnalysisResult.corrected_translation}</div>
                                                    </div>
                                                )}
                                            </div>
                                        ) : (
                                            <div className="text-green-400 font-bold flex items-center gap-2">
                                                {t('cache_editor_no_issues_found_text')}
                                            </div>
                                        )}
                                    </div>
                                ) : null}
                            </div>
                          )}

                          <div className="flex gap-2 justify-end mt-2">
                            <button
                                onClick={() => runSingleLineAnalysis(item)}
                                disabled={analyzingLine}
                                className={`px-3 py-1 rounded-md text-xs font-bold transition-all border ${
                                    elysiaActive ? 'border-pink-500/30 text-pink-400 hover:bg-pink-500/10' : 'border-cyan-500/30 text-cyan-400 hover:bg-cyan-500/10'
                                } mr-auto disabled:opacity-50`}
                            >
                                {analyzingLine ? <span className="animate-spin inline-block mr-1">↻</span> : null}
                                {t('cache_editor_single_check_btn')}
                            </button>

                            <button
                              onClick={handleEditSave}
                              className={`px-3 py-1 rounded-md text-xs font-bold transition-all ${
                                elysiaActive ? 'bg-pink-500 hover:bg-pink-600' : 'bg-green-600 hover:bg-green-700'
                              } text-white`}
                            >
                              {t('cache_editor_save')}
                            </button>
                            <button
                              onClick={handleEditCancel}
                              className="px-3 py-1 bg-white/5 text-slate-400 rounded-md text-xs font-bold hover:bg-white/10 transition-colors"
                            >
                              {t('cache_editor_cancel')}
                            </button>
                          </div>
                        </div>
                      ) : (
                        <>
                          <div
                            className={`cursor-pointer text-sm leading-relaxed min-h-[2.5rem] flex items-start transition-colors ${
                              index === currentLine ? 'text-white' : 'text-slate-400 hover:text-slate-200'
                            }`}
                          >
                            {item.translation || <em className="text-slate-700 italic">{t('cache_editor_no_translation')}</em>}
                          </div>
                          {proofreadGroup && activeSuggestion && expandedProofreadItemId === itemId && (
                            <ProofreadInlineSuggestion
                              t={t}
                              suggestion={activeSuggestion}
                              index={suggestionOffset}
                              count={proofreadGroup.suggestions.length}
                              busy={proofreadActionBusy}
                              onPrevious={() => moveInlineSuggestion(proofreadGroup, -1)}
                              onNext={() => moveInlineSuggestion(proofreadGroup, 1)}
                              onAccept={() => runProofreadAction(activeSuggestion.suggestion_id, 'accept')}
                              onReject={() => runProofreadAction(activeSuggestion.suggestion_id, 'reject')}
                              onIgnore={() => runProofreadAction(activeSuggestion.suggestion_id, 'ignore')}
                              onRestore={() => runProofreadAction(activeSuggestion.suggestion_id, 'restore')}
                              onDelete={() => runProofreadAction(activeSuggestion.suggestion_id, 'delete')}
                              readOnly={Boolean(proofreadReports.find((report) => report.file === activeProofreadReport)?.is_archive)}
                            />
                          )}
                        </>
                      )}
                    </div>
                  </div>
                  );
                })
              )}
            </div>
          </div>

          {/* Pagination */}
          {pagination && pagination.total_pages > 1 && (
            <div className="flex items-center justify-center gap-4 p-2 bg-surface/30 border-t border-gray-700">
              <button
                onClick={() => setCurrentPage(currentPage - 1)}
                disabled={!pagination.has_prev}
                className="px-2 py-0.5 bg-gray-600/50 text-white rounded text-xs disabled:opacity-50 hover:bg-gray-600 transition-colors"
              >
                {t('cache_editor_previous')}
              </button>
              <span className="text-[10px] text-gray-400">
                {t('cache_editor_page_info', currentPage, pagination.total_pages)}
              </span>
              <button
                onClick={() => setCurrentPage(currentPage + 1)}
                disabled={!pagination.has_next}
                className="px-2 py-0.5 bg-gray-600/50 text-white rounded text-xs disabled:opacity-50 hover:bg-gray-600 transition-colors"
              >
                {t('cache_editor_next')}
              </button>
            </div>
          )}

        </div>
      )}

      {cacheStatus.loaded && proofreadReports.length > 0 && (
        <aside
          data-proofread-layout="navigator-sidebar"
          className="hidden min-h-0 items-center min-[1800px]:col-start-3 min-[1800px]:row-start-3 min-[1800px]:flex"
        >
          <ProofreadQuickNavigator
            t={t}
            groups={visibleProofreadGroups}
            index={currentProofreadIndex}
            collapsed={quickNavigatorCollapsed}
            onToggle={() => setQuickNavigatorCollapsed((value) => !value)}
            onSelect={navigateToProofreadGroup}
          />
        </aside>
      )}
    </div>
  );
};
