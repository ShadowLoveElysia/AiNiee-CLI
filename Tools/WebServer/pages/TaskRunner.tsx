import React, { useEffect, useRef, useState } from 'react';
import { Terminal } from '../components/Terminal';
import { StatsPanel } from '../components/StatsPanel';
import { Play, Square, Upload, FileText, ChevronRight, ChevronDown, Terminal as TerminalIcon, Loader2, History, AlertCircle, Sparkles, ListPlus, X } from 'lucide-react';
import { DataService } from '../services/DataService';
import { nativeConfirm } from '../services/nativeDialog';
import { TaskStats, LogEntry, TaskType, TaskPayload } from '../types';
import { useGlobal } from '../contexts/GlobalContext';
import { useI18n } from '../contexts/I18nContext';

const trimPathEnd = (value: string) => value.replace(/[\\/]+$/, '');

const resolveMangaProjectPath = (outputPath?: string, inputPath?: string) => {
  const configuredOutput = trimPathEnd(String(outputPath || '').trim());
  if (configuredOutput) return `${configuredOutput}/mangaProject`;

  const input = trimPathEnd(String(inputPath || '').trim());
  if (!input) return 'mangaProject';

  const normalized = input.replace(/\\/g, '/');
  const slashIndex = normalized.lastIndexOf('/');
  const parent = slashIndex >= 0 ? normalized.slice(0, slashIndex) : '';
  const leaf = slashIndex >= 0 ? normalized.slice(slashIndex + 1) : normalized;
  const looksLikeFile = /\.(png|jpe?g|webp|bmp|gif|pdf|zip|cbz|rar|7z)$/i.test(leaf);
  const baseName = looksLikeFile ? leaf.replace(/\.[^/.]+$/, '') : leaf;
  const derivedOutput = `${parent ? `${parent}/` : ''}${baseName}_AiNiee_Output`;
  return `${derivedOutput}/mangaProject`;
};

export const TaskRunner: React.FC = () => {
  const { t } = useI18n();
  const { config, taskState, setTaskState } = useGlobal(); // Use persistent global state
  
  const intervalRef = useRef<any>(null);
  const taskGenerationRef = useRef(0);
  const pollInFlightRef = useRef(false);
  const startInFlightRef = useRef(false);
  const cursorRef = useRef({ logs: 0, chart: 0, comparison: 0 });
  const [comparisonChannelStatus, setComparisonChannelStatus] = useState<'idle' | 'waiting' | 'active' | 'stale'>('idle');
  const [comparisonLagSec, setComparisonLagSec] = useState<number | null>(null);
  
  // Upload State
  const [tempFiles, setTempFiles] = useState<{name: string, path: string, size: number}[]>([]);
  const [showTempFileList, setShowTempFileList] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [showUploadArea, setShowUploadArea] = useState(false);
  const [previewTaskOverride, setPreviewTaskOverride] = useState<TaskType | null>(null);
  const [isStarting, setIsStarting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const mangaMode = !!taskState.isMangaMode;
  const mangaProjectPath = resolveMangaProjectPath(config?.label_output_path, taskState.customInputPath);

  // --- Helper Functions (Hoisted) ---

  const addLog = (msg: string, type: LogEntry['type'] = 'info') => {
    setTaskState(prev => ({
        ...prev,
        logs: [...prev.logs, {
            id: Date.now().toString(),
            timestamp: new Date().toLocaleTimeString(),
            message: msg,
            type
        }]
    }));
  };

  const setTaskType = (type: TaskType) => {
      setPreviewTaskOverride(null);
      setTaskState(prev => ({ ...prev, taskType: type }));
  };

  const setCustomInputPath = (path: string) => {
      setTaskState(prev => ({ ...prev, customInputPath: path }));
  };

  const loadTempFiles = async () => {
      try {
          const files = await DataService.listTempFiles();
          setTempFiles(files);
      } catch (e) {
          console.error("Failed to load temp files", e);
      }
  };

  const mapLogs = (logs: any[], prefix: string): LogEntry[] => {
      return (logs || []).map((l: any, idx: number) => {
          if (!l) return null;
          return {
              id: l.id || `${prefix}-${idx}-${l.timestamp || Date.now()}`,
              timestamp: typeof l.timestamp === 'number'
                  ? new Date(l.timestamp * 1000).toLocaleTimeString()
                  : (l.timestamp || new Date().toLocaleTimeString()),
              message: String(l.message || ''),
              type: l.type || 'info'
          };
      }).filter(Boolean) as LogEntry[];
  };

  const resolveComparisonStatus = (taskStatus: string, comparisonSeq: number, comparisonUpdatedAt?: number) => {
      if (taskStatus !== 'running') return { status: 'idle' as const, lagSec: null as number | null };
      if (!comparisonSeq || !comparisonUpdatedAt) return { status: 'waiting' as const, lagSec: null as number | null };
      const lagSec = Math.max(0, Math.floor(Date.now() / 1000 - comparisonUpdatedAt));
      return lagSec <= 20
          ? { status: 'active' as const, lagSec }
          : { status: 'stale' as const, lagSec };
  };

  // --- Event Handlers ---

  const processUpload = async (file: File) => {
      if (startInFlightRef.current || taskState.isRunning) return;
      const uploadGeneration = taskGenerationRef.current;
      setIsUploading(true);
      try {
          // Attempt 1: Default Policy
          let result = await DataService.uploadFile(file, 'default');
          
          // Case 1: Limit Reached (10 files) -> Ask user
          if (result.status === 'limit_reached') {
              const confirmMsg = t('msg_upload_limit_reached')
                  .replace('{}', result.limit)
                  .replace('{}', result.oldest); // Reuse token
              
              if (await nativeConfirm(confirmMsg)) {
                  // User chose to Delete (Overwrite)
                  result = await DataService.uploadFile(file, 'overwrite');
              } else {
                  // User chose Cancel (Keep old, use buffer)
                  result = await DataService.uploadFile(file, 'buffer');
              }
          }
          
          // Case 2: Forced Delete (12th file attempt)
          if (result.status === 'forced_delete') {
              const warnMsg = t('msg_upload_forced_delete')
                  .replace('{}', result.limit + 1)
                  .replace('{}', result.deleted);
              
              alert(warnMsg);
              // Backend already deleted oldest and saved new file in this request? 
              // Wait, my backend logic for 'forced_delete' returned early without saving!
              // I need to fix backend to save after deleting.
              // Assuming backend fixed:
              // actually backend logic: if count >= limit+1: delete, return status. 
              // It didn't save. Frontend needs to retry upload?
              // Let's re-read my backend code.
              
              // Backend Code: 
              // elif count >= limit + 1:
              //    try: os.remove(files[0][0]) ...
              //    return { "status": "forced_delete", ... }
              // It RETURNS before saving!
              
              // So Frontend must re-upload now that space is made.
              // Policy 'buffer' or 'default' should work now as count dropped.
              result = await DataService.uploadFile(file, 'default');
          }

          if (result.path) {
              if (
                  uploadGeneration !== taskGenerationRef.current ||
                  startInFlightRef.current ||
                  taskState.isRunning
              ) return;
              addLog(`[SYSTEM] File uploaded: ${result.path}`, "system");
              await loadTempFiles();
              if (
                  uploadGeneration !== taskGenerationRef.current ||
                  startInFlightRef.current ||
                  taskState.isRunning
              ) return;
              setCustomInputPath(result.path);
              setShowUploadArea(false);
          }
      } catch (error: any) {
          addLog(`[ERROR] Upload failed: ${error.message}`, "error");
      } finally {
          setIsUploading(false);
          if (fileInputRef.current) fileInputRef.current.value = '';
      }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
      if (e.target.files && e.target.files[0]) {
          await processUpload(e.target.files[0]);
      }
  };

  const handleDragOver = (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
  };

  const handleDrop = async (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      
      if (e.dataTransfer.files && e.dataTransfer.files[0]) {
          await processUpload(e.dataTransfer.files[0]);
      }
  };

  const startPolling = (generation = taskGenerationRef.current) => {
      stopPolling();
      intervalRef.current = setInterval(async () => {
          if (pollInFlightRef.current || generation !== taskGenerationRef.current) return;
          pollInFlightRef.current = true;
          try {
              const requestedCursor = { ...cursorRef.current };
              const data = await DataService.getTaskStatus(
                  requestedCursor.logs,
                  requestedCursor.chart,
                  requestedCursor.comparison
              );
              if (generation !== taskGenerationRef.current) return;
              const nextCursor = data.cursors || requestedCursor;
              cursorRef.current = {
                  logs: nextCursor.logs ?? requestedCursor.logs,
                  chart: nextCursor.chart ?? requestedCursor.chart,
                  comparison: nextCursor.comparison ?? requestedCursor.comparison
              };
              const comparisonSeq = nextCursor.comparison ?? requestedCursor.comparison ?? 0;
              const comparisonStatus = resolveComparisonStatus(
                  data.stats?.status || 'idle',
                  comparisonSeq,
                  data.comparison_updated_at
              );
              setComparisonChannelStatus(comparisonStatus.status);
              setComparisonLagSec(comparisonStatus.lagSec);
              if (['completed', 'error', 'idle'].includes(data.stats?.status)) {
                  setPreviewTaskOverride(null);
              }
              
              setTaskState(prev => {
                  try {
                      const stats = data.stats || prev.stats;
                      const mappedLogs = mapLogs(data.logs || [], 'be');
                      const logsReset = (nextCursor.logs ?? requestedCursor.logs) < requestedCursor.logs;
                      const chartReset = (nextCursor.chart ?? requestedCursor.chart) < requestedCursor.chart;
                      const mergedLogs = logsReset
                          ? mappedLogs
                          : (mappedLogs.length > 0 ? [...prev.logs, ...mappedLogs] : prev.logs);
                      const mergedChart = chartReset
                          ? (data.chart_data || [])
                          : ((data.chart_data && data.chart_data.length > 0) ? [...prev.chartData, ...data.chart_data] : prev.chartData);

                      // Update State
                      const newState = {
                          ...prev,
                          stats: stats,
                          logs: mergedLogs.slice(-500),
                          chartData: mergedChart.slice(-120),
                          comparison: data.comparison ? { ...data.comparison } : prev.comparison
                      };
                      
                      // Check stop condition
                      if (stats.status === 'completed' || stats.status === 'error' || stats.status === 'idle') {
                          if (prev.isRunning) {
                              // Stop running locally if backend is done
                              stopPolling();
                              return { ...newState, isRunning: false };
                          }
                      }

                      return newState;
                  } catch (innerError) {
                      console.error("State update error", innerError);
                      return prev;
                  }
              });

          } catch (e) {
              console.error("Polling error", e);
          } finally {
              pollInFlightRef.current = false;
          }
      }, 1000);
  };

  const stopPolling = () => {
      if (intervalRef.current) {
          clearInterval(intervalRef.current);
          intervalRef.current = null;
      }
  };

  const handleStart = async (isAllInOne = false) => {
      if (startInFlightRef.current) return;
      if (!config || !config.target_platform) {
          addLog("[ERROR] Configuration not fully loaded or platform not selected.", "error");
          return;
      }
      if (!taskState.customInputPath) {
          addLog("[ERROR] Input path is required.", "error");
          return;
      }

      // Defensive access
      const targetPlatform = config.target_platform;
      const platformConfig = config.platforms ? config.platforms[targetPlatform] : undefined;
      const apiKey = platformConfig?.api_key || (config as any).api_key || undefined;

      // Prepare payload with explicit values (transmitting parameters correctly)
      const taskType = isAllInOne ? TaskType.ALL_IN_ONE : (taskState.taskType || TaskType.TRANSLATE);
      const taskGeneration = taskGenerationRef.current + 1;
      taskGenerationRef.current = taskGeneration;
      stopPolling();
      startInFlightRef.current = true;
      setIsStarting(true);
      setShowUploadArea(false);
      setPreviewTaskOverride(taskType);
      const payload: TaskPayload = {
          task: taskType,
          input_path: taskState.customInputPath,
          output_path: config.label_output_path || undefined, 
          source_lang: config.source_language || undefined,
          target_lang: config.target_language || undefined,
          project_type: config.translation_project || undefined,
          resume: !!taskState.isResuming,
          profile: config.active_profile || undefined,
          rules_profile: config.active_rules_profile || undefined,
          
          threads: Number(config.user_thread_counts || 0),
          retry: Number(config.retry_count || 0),
          timeout: Number(config.request_timeout || 60),
          rounds: Number(config.round_limit || 3),
          pre_lines: Number(config.pre_line_counts ?? 3),
          
          platform: targetPlatform,
          model: config.model || platformConfig?.model || '',
          api_key: apiKey,
          api_url: config.base_url || platformConfig?.api_url || undefined,
          
          failover: !!config.enable_api_failover,
          think_depth: config.think_depth ?? platformConfig?.think_depth ?? undefined,
          thinking_budget: config.thinking_budget ?? platformConfig?.thinking_budget ?? undefined,
          polish_mode: taskType === TaskType.POLISH || taskType === TaskType.ALL_IN_ONE
              ? (config.polishing_mode_selection as TaskPayload['polish_mode']) || undefined
              : undefined,
          
          lines: !config.tokens_limit_switch ? Math.max(1, Math.min(100, Number(config.lines_limit || 20))) : undefined,
          tokens: config.tokens_limit_switch ? Math.max(400, Math.min(16000, Number(config.tokens_limit || 1500))) : undefined,
          
          manga: taskType === TaskType.TRANSLATE && mangaMode
      };

      // Reset Chart but keep input path
      cursorRef.current = { logs: 0, chart: 0, comparison: 0 };
      setComparisonChannelStatus('waiting');
      setComparisonLagSec(null);
      setTaskState(prev => ({ 
          ...prev, 
          isRunning: true, 
          chartData: [],
          logs: [],
          stats: { 
              ...(prev.stats || {}), 
              status: 'running',
              completedProgress: 0 // Reset local progress display
          }
      }));
      
      addLog(`[SYSTEM] Starting ${isAllInOne ? 'ALL-IN-ONE' : String(payload.task).toUpperCase()} task...`, "system");

      try {
          await DataService.startTask(payload);
          if (taskGeneration !== taskGenerationRef.current) return;
          startInFlightRef.current = false;
          setIsStarting(false);
          startPolling(taskGeneration);
      } catch (e: any) {
          if (taskGeneration !== taskGenerationRef.current) return;
          startInFlightRef.current = false;
          setIsStarting(false);
          setPreviewTaskOverride(null);
          setTaskState(prev => ({ ...prev, isRunning: false, stats: { ...prev.stats, status: 'error' } }));
          addLog(`[ERROR] Failed to start: ${e.message}`, "error");
      }
  };

  const handleStop = async () => {
      addLog("[SYSTEM] Sending STOP signal...", "warning");
      try {
          await DataService.stopTask();
          taskGenerationRef.current += 1;
          stopPolling();
          startInFlightRef.current = false;
          setIsStarting(false);
          setPreviewTaskOverride(null);
          setTaskState(prev => ({
              ...prev,
              isRunning: false,
              stats: { ...prev.stats, status: 'idle' }
          }));
      } catch (e: any) {
          addLog(`[ERROR] Failed to stop: ${e.message}`, "error");
      }
  };

  const generateCLIPreview = () => {
      if (!config) return "Loading config...";
      const previewTask = previewTaskOverride || taskState.taskType || TaskType.TRANSLATE;
      const platformConfig = config.target_platform ? config.platforms?.[config.target_platform] : undefined;
      const polishMode = config.polishing_mode_selection;
      const thinkDepth = config.think_depth ?? platformConfig?.think_depth;
      const thinkingBudget = config.thinking_budget ?? platformConfig?.thinking_budget;
      const parts = ['uv run ainiee_cli.py', previewTask, `"${taskState.customInputPath || ''}"`];
      
      // Flags
      if (taskState.isResuming) parts.push('-y --resume');
      else parts.push('-y');

      // Add Profile (Critical)
      if (config.active_profile) parts.push(`--profile "${config.active_profile}"`);
      if (config.active_rules_profile) parts.push(`--rules-profile "${config.active_rules_profile}"`);
      if (config.label_output_path) parts.push(`--output "${config.label_output_path}"`);
      if (mangaMode && previewTask === TaskType.TRANSLATE) parts.push('--manga');

      if (config.source_language) parts.push(`-s "${config.source_language}"`);
      if (config.target_language) parts.push(`-t "${config.target_language}"`);
      if (config.translation_project) parts.push(`--type "${config.translation_project}"`);
      if (config.user_thread_counts !== undefined) parts.push(`--threads ${Number(config.user_thread_counts || 0)}`);
      if (config.retry_count !== undefined) parts.push(`--retry ${Number(config.retry_count || 0)}`);
      if (config.request_timeout !== undefined) parts.push(`--timeout ${Number(config.request_timeout || 60)}`);
      if (config.round_limit !== undefined) parts.push(`--rounds ${Number(config.round_limit || 3)}`);
      if (config.pre_line_counts !== undefined) parts.push(`--pre-lines ${Number(config.pre_line_counts ?? 3)}`);
      if (config.target_platform) parts.push(`--platform "${config.target_platform}"`);
      const model = config.model || platformConfig?.model;
      if (model) parts.push(`--model "${model}"`);
      const apiUrl = config.base_url || platformConfig?.api_url;
      if (apiUrl) parts.push(`--api-url "${apiUrl}"`);
      parts.push(`--failover ${config.enable_api_failover ? 'on' : 'off'}`);
      if (thinkDepth !== undefined && thinkDepth !== null && thinkDepth !== '') {
          parts.push(`--think-depth ${String(thinkDepth)}`);
      }
      if (thinkingBudget !== undefined && thinkingBudget !== null) {
          parts.push(`--thinking-budget ${Number(thinkingBudget)}`);
      }
      if ((previewTask === TaskType.POLISH || previewTask === TaskType.ALL_IN_ONE) && polishMode) {
          parts.push(`--polish-mode ${polishMode}`);
      }
      if (config.tokens_limit_switch) {
          parts.push(`--tokens ${Math.max(400, Math.min(16000, Number(config.tokens_limit || 1500)))}`);
      } else {
          parts.push(`--lines ${Math.max(1, Math.min(100, Number(config.lines_limit || 20)))}`);
      }
      parts.push('--web-mode');
      return parts.join(' ');
  }

  // --- Effects ---

  // Initial Sync with logs if empty and config loaded
  useEffect(() => {
    if (config && taskState.logs.length === 0) {
        addLog(`[SYSTEM] Ready. Loaded profile: ${config.active_profile || 'default'}`, 'system');
    }
    loadTempFiles();
  }, [config]);

  // Initial sync on mount to recover state after refresh
  useEffect(() => {
    const recoveryGeneration = taskGenerationRef.current;
    const recoverState = async () => {
        try {
            const data = await DataService.getTaskStatus(0, 0, 0);
            if (recoveryGeneration !== taskGenerationRef.current) return;
            cursorRef.current = {
                logs: data.cursors?.logs ?? (data.logs?.length || 0),
                chart: data.cursors?.chart ?? (data.chart_data?.length || 0),
                comparison: data.cursors?.comparison ?? (data.comparison ? 1 : 0)
            };
            const recoveredStatus = resolveComparisonStatus(
                data.stats?.status || 'idle',
                cursorRef.current.comparison,
                data.comparison_updated_at
            );
            setComparisonChannelStatus(recoveredStatus.status);
            setComparisonLagSec(recoveredStatus.lagSec);
            if (data.stats.status !== 'running') {
                setPreviewTaskOverride(null);
            }
            setTaskState(prev => ({
                ...prev,
                isRunning: data.stats.status === 'running',
                stats: data.stats,
                chartData: data.chart_data || [],
                comparison: data.comparison || prev.comparison,
                logs: mapLogs(data.logs || [], 'sync')
            }));
            
            if (data.stats.status === 'running') {
                startPolling(recoveryGeneration);
            }
        } catch (e) {
            console.error("Failed to recover state", e);
        }
    };
    recoverState();
    return () => {
        taskGenerationRef.current += 1;
        startInFlightRef.current = false;
        stopPolling();
    };
  }, []);

  // --- Render ---

  const [activeTab, setActiveTab] = useState<'console' | 'comparison'>('console');
  const comparisonStatusMeta = (() => {
      if (comparisonChannelStatus === 'active') {
          return {
              label: `ACTIVE${comparisonLagSec !== null ? ` · ${comparisonLagSec}s` : ''}`,
              className: 'text-emerald-300 border-emerald-500/30 bg-emerald-500/10'
          };
      }
      if (comparisonChannelStatus === 'waiting') {
          return {
              label: 'WAITING',
              className: 'text-amber-300 border-amber-500/30 bg-amber-500/10'
          };
      }
      if (comparisonChannelStatus === 'stale') {
          return {
              label: `DELAYED${comparisonLagSec !== null ? ` · ${comparisonLagSec}s` : ''}`,
              className: 'text-rose-300 border-rose-500/30 bg-rose-500/10'
          };
      }
      return {
          label: 'IDLE',
          className: 'text-slate-300 border-slate-600/40 bg-slate-700/20'
      };
  })();

  return (
    <div className="space-y-3 h-[calc(100vh-140px)] flex flex-col">
      {/* Header Controls */}
      <div className="bg-surface border border-slate-800 p-4 rounded-xl space-y-4">
        <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
            <div className="flex items-center gap-4 w-full md:w-auto flex-1">
                <div className="w-12 h-12 bg-slate-800 rounded-lg flex items-center justify-center border border-slate-700 shrink-0">
                    <FileText size={24} className="text-slate-400" />
                </div>
                <div className="flex-1 min-w-[300px] space-y-2">
                    <div className="flex gap-2">
                        <button 
                            disabled={taskState.isRunning}
                            onClick={() => setTaskType(TaskType.TRANSLATE)} 
                            className={`text-xs px-3 py-1 rounded transition-colors ${taskState.taskType === TaskType.TRANSLATE ? 'bg-primary text-slate-900 font-bold' : 'bg-slate-800 text-slate-400 hover:text-slate-200'}`}
                        >
                            {t('ui_task_translate')}
                        </button>
                        <button 
                            disabled={taskState.isRunning || mangaMode}
                            onClick={() => setTaskType(TaskType.POLISH)} 
                            className={`text-xs px-3 py-1 rounded transition-colors ${taskState.taskType === TaskType.POLISH ? 'bg-purple-500 text-slate-900 font-bold' : 'bg-slate-800 text-slate-400 hover:text-slate-200'}`}
                        >
                            {t('ui_task_polish')}
                        </button>
                        <button 
                            disabled={taskState.isRunning || mangaMode}
                            onClick={() => setTaskType(TaskType.EXPORT)} 
                            className={`text-xs px-3 py-1 rounded transition-colors ${taskState.taskType === TaskType.EXPORT ? 'bg-emerald-500 text-slate-900 font-bold' : 'bg-slate-800 text-slate-400 hover:text-slate-200'}`}
                        >
                            {t('ui_task_export')}
                        </button>
                    </div>
                    <div className="relative">
                         <input 
                            type="text" 
                            disabled={taskState.isRunning}
                            placeholder={t('prompt_input_path')} 
                            value={taskState.customInputPath}
                            onChange={(e) => setCustomInputPath(e.target.value)}
                            className="bg-transparent border-b border-slate-700 p-1 text-white font-semibold focus:ring-0 focus:border-primary placeholder:text-slate-600 w-full outline-none transition-colors pr-8"
                        />
                         <div className="absolute right-0 top-0">
                            <button 
                                disabled={taskState.isRunning}
                                onClick={() => setShowTempFileList(!showTempFileList)}
                                className={`text-slate-500 hover:text-white p-1 transition-transform ${showTempFileList ? 'rotate-180 text-primary' : ''}`}
                            >
                                <ChevronDown size={14} />
                            </button>
                             {showTempFileList && (
                                 <div className="absolute right-0 top-full mt-1 w-80 bg-slate-900 border border-slate-700 rounded shadow-xl z-20 max-h-60 overflow-y-auto animate-in fade-in slide-in-from-top-2 duration-200">
                                     <div className="px-3 py-2 text-xs font-bold text-slate-500 border-b border-slate-800 bg-slate-950 flex justify-between items-center">
                                         <span>{t('ui_task_temp_uploaded')}</span>
                                         <button onClick={() => setShowTempFileList(false)} className="hover:text-white transition-colors"><X size={12} /></button>
                                     </div>
                                     {tempFiles.length === 0 ? (
                                         <div className="p-3 text-xs text-slate-500 text-center italic">{t('msg_no_files_found')}</div>
                                     ) : (
                                         tempFiles.map((f, i) => (
                                             <div 
                                                key={i} 
                                                className="px-3 py-2 hover:bg-slate-800 cursor-pointer text-xs text-slate-300 truncate border-b border-slate-800/50 last:border-0"
                                                onClick={() => {
                                                    setCustomInputPath(f.path);
                                                    setShowTempFileList(false);
                                                }}
                                                title={f.path}
                                             >
                                                 {f.name} <span className="text-slate-600 ml-1">({(f.size/1024).toFixed(1)} KB)</span>
                                             </div>
                                         ))
                                     )}
                                 </div>
                             )}
                         </div>
                    </div>
                    {/* Resume Switch */}
                    <div className="flex items-center gap-2">
                        <label className="flex items-center gap-2 cursor-pointer group">
                            <input 
                                type="checkbox" 
                                disabled={taskState.isRunning}
                                checked={taskState.isResuming}
                                onChange={(e) => setTaskState(prev => ({ ...prev, isResuming: e.target.checked }))}
                                className="w-4 h-4 rounded border-slate-700 text-primary focus:ring-primary bg-slate-900"
                            />
                            <span className="text-xs text-slate-400 group-hover:text-slate-200 transition-colors">{t('ui_resume')} / {t('option_resume')}</span>
                        </label>
                        <label className="flex items-center gap-2 cursor-pointer group">
                            <input
                                type="checkbox"
                                disabled={taskState.isRunning}
                                checked={mangaMode}
                                onChange={(e) => {
                                    setPreviewTaskOverride(null);
                                    setTaskState(prev => ({
                                        ...prev,
                                        isMangaMode: e.target.checked,
                                        taskType: e.target.checked ? TaskType.TRANSLATE : prev.taskType
                                    }));
                                }}
                                className="w-4 h-4 rounded border-slate-700 text-cyan-400 focus:ring-cyan-400 bg-slate-900"
                            />
                            <span className="text-xs text-slate-400 group-hover:text-slate-200 transition-colors">Manga Mode (`--manga`)</span>
                        </label>
                    </div>
                </div>
            </div>
            
            <div className="flex gap-3 w-full md:w-auto justify-end">
                 <button 
                    disabled={taskState.isRunning}
                    onClick={() => setShowUploadArea(!showUploadArea)} 
                    className={`flex items-center gap-2 px-6 py-2 rounded-lg font-bold transition-all border ${showUploadArea ? 'bg-blue-500 text-slate-900 border-blue-400' : 'bg-blue-500/10 text-blue-400 border-blue-500/50 hover:bg-blue-500/20'}`}
                >
                    <Upload size={18} /> {t('ui_task_upload')}
                </button>

                 {!taskState.isRunning ? (
                    <div className="flex gap-2">
                        <button 
                            onClick={() => handleStart(false)}
                            className="flex items-center gap-2 px-6 py-2 rounded-lg font-bold transition-all bg-accent/10 text-accent border border-accent/50 hover:bg-accent/20 hover:shadow-lg hover:shadow-accent/10"
                        >
                            <Play size={18} /> {t('ui_task_start')} {taskState.taskType.toUpperCase()}
                        </button>
                        
                        <button 
                            onClick={() => handleStart(true)}
                            disabled={mangaMode || taskState.taskType !== TaskType.TRANSLATE}
                            className="flex items-center gap-2 px-4 py-2 rounded-lg font-bold transition-all bg-green-500/10 text-green-400 border border-green-500/50 hover:bg-green-500/20 hover:shadow-lg"
                            title={t('menu_start_all_in_one')}
                        >
                            <Sparkles size={18} /> {t('menu_start_all_in_one')?.split('【')[0]}
                        </button>

                        <button 
                            onClick={() => window.location.hash = '/queue'}
                            className="flex items-center gap-2 px-4 py-2 rounded-lg font-bold transition-all bg-blue-500/10 text-blue-400 border border-blue-500/50 hover:bg-blue-500/20"
                            title={t('menu_task_queue')}
                        >
                            <ListPlus size={18} /> {t('ui_process_queue') || 'Queue'}
                        </button>
                        {mangaMode && (
                            <button
                                onClick={() => { window.location.hash = `/manga-editor?project_path=${encodeURIComponent(mangaProjectPath)}`; }}
                                className="flex items-center gap-2 px-4 py-2 rounded-lg font-bold transition-all bg-cyan-500/10 text-cyan-300 border border-cyan-400/40 hover:bg-cyan-500/20"
                                title="Open Manga Editor"
                            >
                                <FileText size={18} /> Manga Editor
                            </button>
                        )}
                    </div>
                ) : (
                    <button 
                        onClick={handleStop}
                        disabled={isStarting}
                        className="flex items-center gap-2 px-6 py-2 rounded-lg font-bold transition-all bg-red-500/10 text-red-500 border border-red-500/50 hover:bg-red-500/20 hover:shadow-lg hover:shadow-red-500/10"
                    >
                        {isStarting
                            ? <Loader2 size={18} className="animate-spin" />
                            : <Square size={18} fill="currentColor" />}
                        {isStarting ? 'STARTING...' : t('ui_task_stop')}
                    </button>
                )}
            </div>
        </div>

        {/* Upload Drag & Drop Area */}
        {showUploadArea && !taskState.isRunning && (
            <div 
                className={`border-2 border-dashed rounded-xl p-8 transition-all text-center cursor-pointer ${isUploading ? 'border-primary bg-primary/5' : 'border-slate-700 hover:border-slate-500 hover:bg-slate-800/30'}`}
                onDragOver={handleDragOver}
                onDrop={handleDrop}
                onClick={() => fileInputRef.current?.click()}
            >
                <input 
                    type="file" 
                    ref={fileInputRef} 
                    className="hidden" 
                    onChange={handleFileUpload} 
                />
                <div className="flex flex-col items-center gap-3">
                    {isUploading ? (
                        <>
                             <Loader2 size={32} className="text-primary animate-spin" />
                             <p className="text-primary font-bold">{t('msg_uploading') || 'Uploading...'}</p>
                        </>
                    ) : (
                        <>
                            <div className="p-3 bg-slate-800 rounded-full text-slate-400">
                                <Upload size={24} />
                            </div>
                            <div>
                                <p className="text-slate-200 font-medium">{t('msg_drag_drop_title') || 'Drag & Drop files here, or click to browse'}</p>
                                <p className="text-slate-500 text-sm mt-1">{t('msg_drag_drop_subtitle') || 'Files will be uploaded to project temp folder'}</p>
                            </div>
                        </>
                    )}
                </div>
            </div>
        )}
      </div>

      <div className="bg-slate-950 border border-slate-800 rounded-lg p-3 font-mono text-xs text-slate-400 flex items-start gap-3 overflow-x-auto whitespace-nowrap">
        <TerminalIcon size={14} className="mt-0.5 text-primary shrink-0" />
        <span className="text-slate-500 select-none">$</span>
        <span className="text-emerald-400">{generateCLIPreview()}</span>
      </div>

      {/* Tabs */}
      {config?.show_detailed_logs && (
        <div className="flex gap-2 border-b border-slate-800 px-2 mt-2">
          <button 
            onClick={() => setActiveTab('console')}
            className={`px-4 py-2 text-xs font-bold transition-all border-b-2 flex items-center gap-2 ${activeTab === 'console' ? 'border-primary text-primary' : 'border-transparent text-slate-500 hover:text-slate-300'}`}
          >
            <TerminalIcon size={14} />
            {t('ui_tab_console') || 'CONSOLE'}
          </button>
          <button 
            onClick={() => setActiveTab('comparison')}
            className={`px-4 py-2 text-xs font-bold transition-all border-b-2 flex items-center gap-2 ${activeTab === 'comparison' ? 'border-primary text-primary' : 'border-transparent text-slate-500 hover:text-slate-300'}`}
          >
            <FileText size={14} />
            {t('ui_tab_comparison') || 'COMPARISON'}
          </button>
        </div>
      )}

      {/* Content */}
      <div className="flex-1 min-h-0 flex flex-col relative space-y-4 pt-2">
        {(!config?.show_detailed_logs || activeTab === 'console') ? (
            <div className="flex-1 flex flex-col space-y-4 min-h-0">
                <StatsPanel data={taskState.chartData} stats={taskState.stats} />
                <Terminal logs={taskState.logs} height="flex-1" />
            </div>
        ) : (
            <div className="flex-1 flex flex-col space-y-4 min-h-0">
                <StatsPanel data={taskState.chartData} stats={taskState.stats} variant="compact" />
                <div className="px-3 py-2 rounded-lg border border-slate-800 bg-slate-900/40 flex items-center justify-between">
                    <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Comparison Channel</span>
                    <span className={`px-2 py-1 rounded border text-[10px] font-mono uppercase tracking-wide ${comparisonStatusMeta.className}`}>
                        {comparisonStatusMeta.label}
                    </span>
                </div>
                <div className="flex-1 grid grid-cols-1 lg:grid-cols-2 gap-4 min-h-0 overflow-y-auto">
                    {/* Source Pane */}
                    <div className="flex flex-col bg-slate-900/40 border border-magenta/20 rounded-xl overflow-hidden backdrop-blur-sm shadow-inner shadow-magenta/5 min-h-[300px]">
                        <div className="px-4 py-2 bg-magenta/10 border-b border-magenta/20 flex justify-between items-center">
                            <span className="text-[10px] font-bold text-magenta uppercase tracking-widest">{t('label_original_source')}</span>
                            <span className="text-[10px] text-slate-500 font-mono">{(taskState.comparison?.source?.split('\n').length || 0)} {t('label_lines')}</span>
                        </div>
                        <div className="flex-1 p-4 overflow-y-auto font-mono text-sm text-slate-300 leading-relaxed scrollbar-thin scrollbar-thumb-magenta/20 whitespace-pre-wrap">
                            {taskState.comparison?.source || <span className="text-slate-600 italic">{t('msg_waiting_text')}</span>}
                        </div>
                    </div>

                    {/* Translation Pane */}
                    <div className="flex flex-col bg-slate-900/40 border border-primary/20 rounded-xl overflow-hidden backdrop-blur-sm shadow-inner shadow-primary/5 min-h-[300px]">
                        <div className="px-4 py-2 bg-primary/10 border-b border-primary/20 flex justify-between items-center">
                            <span className="text-[10px] font-bold text-primary uppercase tracking-widest">{t('label_translation_output')}</span>
                            <span className="text-[10px] text-slate-500 font-mono">{(taskState.comparison?.translation?.split('\n').length || 0)} {t('label_lines')}</span>
                        </div>
                        <div className="flex-1 p-4 overflow-y-auto font-mono text-sm text-primary-light leading-relaxed scrollbar-thin scrollbar-thumb-primary/20 whitespace-pre-wrap">
                            {taskState.comparison?.translation || <span className="text-slate-600 italic animate-pulse">{t('msg_processing_batch')}</span>}
                        </div>
                    </div>
                </div>
            </div>
        )}

        {taskState.isRunning && (
            <div className="absolute top-4 right-4 flex items-center gap-2 bg-slate-900/80 backdrop-blur px-3 py-1 rounded-full border border-primary/20 text-primary text-xs font-mono animate-pulse">
                <Loader2 size={12} className="animate-spin" />
                {t('msg_processing')}
            </div>
        )}
      </div>
    </div>
  );
};
