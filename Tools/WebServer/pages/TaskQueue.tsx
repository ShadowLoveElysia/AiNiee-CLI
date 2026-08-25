import React, { useState, useEffect } from 'react';
import { ListPlus, Trash2, Play, FileJson, Settings2, FolderOpen, Globe, BookOpen, Layers, Plus, Loader2, Save, X, AlertTriangle, Edit3, ChevronDown, ChevronUp, Cpu, Zap, MessageSquare, Server, GripVertical } from 'lucide-react';
import { DataService } from '../services/DataService';
import { nativeConfirm } from '../services/nativeDialog';
import { useI18n } from '../contexts/I18nContext';
import { useGlobal } from '../contexts/GlobalContext';
import { QueueTaskItem, TaskType } from '../types';

export const TaskQueue: React.FC = () => {
  const { t } = useI18n();
  const { config, profiles, rulesProfiles, activeTheme } = useGlobal();
  const [queue, setQueue] = useState<QueueTaskItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [editIndex, setEditIndex] = useState<number | null>(null);
  const [draggedIndex, setDraggedIndex] = useState<number | null>(null);
  const [dragOverIndex, setDragOverIndex] = useState<number | null>(null);

  // Task Form State
  const [taskForm, setTaskForm] = useState<Partial<QueueTaskItem>>({
    task_type: TaskType.TRANSLATE,
    input_path: '',
    output_path: '',
    profile: '',
    rules_profile: '',
    source_lang: '',
    target_lang: '',
    project_type: '',
    model: '',
    threads: undefined,
    retry: undefined,
    timeout: undefined,
    rounds: undefined,
    pre_lines: undefined,
    lines_limit: undefined,
    tokens_limit: undefined,
    think_depth: '',
    thinking_budget: undefined,
    failover: undefined,
    resume: false,
    polish_mode: undefined
  });

  const fetchQueue = async () => {
    try {
      const data = await DataService.getQueue();
      setQueue(data);
    } catch (error) {
      console.error("Failed to fetch queue", error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchQueue();
    const interval = setInterval(fetchQueue, 5000); 
    return () => clearInterval(interval);
  }, []);

  const openAddModal = () => {
    setEditIndex(null);
    setTaskForm({
        task_type: TaskType.TRANSLATE,
        input_path: '',
        output_path: '',
        profile: '',
        rules_profile: '',
        source_lang: '',
        target_lang: '',
        project_type: '',
        platform: '',
        api_url: '',
        model: '',
        threads: undefined,
        retry: undefined,
        timeout: undefined,
        rounds: undefined,
        pre_lines: undefined,
        lines_limit: undefined,
        tokens_limit: undefined,
        think_depth: '',
        thinking_budget: undefined,
        failover: undefined,
        resume: false,
        polish_mode: undefined
    });
    setShowModal(true);
  };

  const openEditModal = (index: number) => {
    setEditIndex(index);
    // Ensure all fields are explicitly set, even if undefined in original task
    const taskToEdit = queue[index];
    setTaskForm({
      task_type: taskToEdit.task_type,
      input_path: taskToEdit.input_path,
      output_path: taskToEdit.output_path || '',
      profile: taskToEdit.profile || '',
      rules_profile: taskToEdit.rules_profile || '',
      source_lang: taskToEdit.source_lang || '',
      target_lang: taskToEdit.target_lang || '',
      project_type: taskToEdit.project_type || '',
      platform: taskToEdit.platform || '',
      api_url: taskToEdit.api_url || '',
      model: taskToEdit.model || '',
      threads: taskToEdit.threads,
      retry: taskToEdit.retry,
      timeout: taskToEdit.timeout,
      rounds: taskToEdit.rounds,
      pre_lines: taskToEdit.pre_lines,
      lines_limit: taskToEdit.lines_limit,
      tokens_limit: taskToEdit.tokens_limit,
      think_depth: taskToEdit.think_depth ?? '',
      thinking_budget: taskToEdit.thinking_budget,
      failover: taskToEdit.failover,
      resume: taskToEdit.resume,
      polish_mode: taskToEdit.polish_mode
    });
    setShowModal(true);
  };

  const handleSubmit = async () => {
    if (!taskForm.input_path) {
        alert(t('msg_enter_input_path'));
        return;
    }
    const payload: Partial<QueueTaskItem> = { ...taskForm };
    if (payload.task_type === TaskType.TRANSLATE) {
      payload.polish_mode = null;
    }
    if (payload.tokens_limit !== undefined && payload.tokens_limit !== null) {
      payload.tokens_limit = Math.max(400, Math.min(16000, Number(payload.tokens_limit) || 400));
      payload.lines_limit = null;
    } else if (payload.lines_limit !== undefined && payload.lines_limit !== null) {
      payload.lines_limit = Math.max(1, Math.min(100, Number(payload.lines_limit) || 1));
      payload.tokens_limit = null;
    } else if (editIndex !== null) {
      payload.lines_limit = null;
      payload.tokens_limit = null;
    }
    if (editIndex !== null) {
      payload.output_path ||= null;
      payload.profile ||= null;
      payload.rules_profile ||= null;
      payload.source_lang ||= null;
      payload.target_lang ||= null;
      payload.project_type ||= null;
      payload.platform ||= null;
      payload.api_url ||= null;
      payload.model ||= null;
      if (payload.think_depth === '' || payload.think_depth === undefined) {
        payload.think_depth = null;
      }
      payload.threads ??= null;
      payload.retry ??= null;
      payload.timeout ??= null;
      payload.rounds ??= null;
      payload.pre_lines ??= null;
      payload.thinking_budget ??= null;
      if (payload.task_type !== TaskType.TRANSLATE && payload.polish_mode === undefined) {
        payload.polish_mode = null;
      }
    }
    try {
      if (editIndex !== null) {
          await DataService.updateQueueItem(editIndex, payload);
      } else {
          await DataService.addToQueue(payload);
      }
      setShowModal(false);
      fetchQueue();
    } catch (error) {
      alert(t('msg_operation_failed'));
    }
  };

  const handleRemoveTask = async (index: number) => {
    if (!(await nativeConfirm(t('msg_profile_delete_confirm').replace('{}', `Task #${index + 1}`)))) return;
    try {
      await DataService.removeFromQueue(index);
      fetchQueue();
    } catch (error) {
      alert(t('msg_failed_remove_task'));
    }
  };

  const handleClearQueue = async () => {
    if (!(await nativeConfirm(t('menu_clear_data') + "?"))) return;
    try {
      await DataService.clearQueue();
      fetchQueue();
    } catch (error) {
      alert(t('msg_failed_clear_queue'));
    }
  };

  // 拖拽处理函数
  const handleDragStart = (e: React.DragEvent, index: number) => {
    const task = queue[index];
    // 检查任务是否真正被锁定
    if (isTaskActuallyLocked(task)) {
      e.preventDefault();
      showLockedTaskAlert(task);
      return;
    }
    setDraggedIndex(index);
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/html', e.currentTarget.outerHTML);
  };

  const handleDragOver = (e: React.DragEvent, index: number) => {
    e.preventDefault();
    const targetTask = queue[index];
    // 不允许在真正锁定的任务上放置
    if (isTaskActuallyLocked(targetTask)) {
      e.dataTransfer.dropEffect = 'none';
      return;
    }
    e.dataTransfer.dropEffect = 'move';
    setDragOverIndex(index);
  };

  const handleDragLeave = () => {
    setDragOverIndex(null);
  };

  const handleDrop = async (e: React.DragEvent, dropIndex: number) => {
    e.preventDefault();

    if (draggedIndex === null || draggedIndex === dropIndex) {
      setDraggedIndex(null);
      setDragOverIndex(null);
      return;
    }

    const targetTask = queue[dropIndex];
    // 检查目标位置是否真正被锁定
    if (isTaskActuallyLocked(targetTask)) {
      showLockedTaskAlert(targetTask);
      setDraggedIndex(null);
      setDragOverIndex(null);
      return;
    }

    try {
      // 调用API重新排序
      await DataService.moveQueueItem(draggedIndex, dropIndex);
      fetchQueue();
    } catch (error: any) {
      alert(t('msg_operation_failed'));
    }

    setDraggedIndex(null);
    setDragOverIndex(null);
  };

  const handleDragEnd = () => {
    setDraggedIndex(null);
    setDragOverIndex(null);
  };

  const handleRunQueue = async () => {
    try {
      await DataService.runQueue();
      window.location.hash = '/monitor';
    } catch (error: any) {
      alert(error.message || t('msg_failed_start_queue'));
    }
  };

  // 检查任务是否真正被锁定
  const isTaskActuallyLocked = (task: any) => {
    // 优先使用准确的处理状态检测
    if (task.is_actually_processing !== undefined) {
      return task.is_actually_processing;
    }
    // 降级到传统的locked状态
    return task.locked || false;
  };

  // 显示锁定任务提示
  const showLockedTaskAlert = (task: any) => {
    let statusText = '';
    if (task.status === 'translating') {
      if (task.task_type === TaskType.ALL_IN_ONE) {
        statusText = t('task_status_all_in_one_cn');
      } else {
        statusText = t('task_status_translating_cn');
      }
    } else if (task.status === 'polishing') {
      statusText = t('task_status_polishing_cn');
    } else {
      statusText = task.status; // fallback
    }
    alert(t('msg_task_locked').replace('{}', statusText));
  };

  const isElysia = activeTheme === 'elysia' || activeTheme === 'herrscher_of_human';
  const limitMode = taskForm.tokens_limit !== undefined && taskForm.tokens_limit !== null
    ? 'tokens'
    : taskForm.lines_limit !== undefined && taskForm.lines_limit !== null
      ? 'lines'
      : 'profile';
  const setLimitMode = (mode: 'profile' | 'lines' | 'tokens') => {
    if (mode === 'profile') {
      setTaskForm({ ...taskForm, lines_limit: undefined, tokens_limit: undefined });
    } else if (mode === 'lines') {
      const fallback = Number(config?.lines_limit ?? 20);
      setTaskForm({
        ...taskForm,
        lines_limit: Math.max(1, Math.min(100, Number(taskForm.lines_limit ?? fallback) || 20)),
        tokens_limit: undefined
      });
    } else {
      const fallback = Number(config?.tokens_limit ?? 1500);
      setTaskForm({
        ...taskForm,
        lines_limit: undefined,
        tokens_limit: Math.max(400, Math.min(16000, Number(taskForm.tokens_limit ?? fallback) || 1500))
      });
    }
  };

  return (
    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500 pb-24">
      <header className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className={`text-3xl font-black uppercase tracking-tighter ${isElysia ? 'text-pink-500' : 'text-white'}`}>
            {t('menu_task_queue')}
          </h1>
          <p className="text-slate-500 text-sm">{t('ui_new_task_desc')}</p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={openAddModal}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg font-bold transition-all ${
                isElysia ? 'bg-pink-500 text-white hover:bg-pink-400 shadow-lg shadow-pink-500/20' : 'bg-primary text-slate-900 hover:bg-cyan-300 shadow-lg shadow-primary/20'
            }`}
          >
            <Plus size={18} />
            {t('menu_queue_add')}
          </button>
          
          <button
            onClick={async () => {
                await DataService.editQueueFile();
            }}
            className="flex items-center gap-2 px-4 py-2 bg-slate-800 text-slate-200 rounded-lg font-bold hover:bg-slate-700 transition-all border border-slate-700"
          >
            <FileJson size={18} />
            {t('menu_queue_edit_json')}
          </button>
        </div>
      </header>

      {/* TUI Real-time Guidance */}
      <div className="bg-blue-900/30 border border-blue-500/30 rounded-lg p-4">
        <div className="flex items-start gap-3">
          <div className="flex-shrink-0 w-6 h-6 bg-blue-500 rounded-full flex items-center justify-center">
            <Cpu size={14} className="text-white" />
          </div>
          <div className="flex-1">
            <h3 className="text-blue-400 font-semibold text-sm mb-1">
              {t('title_realtime_queue_editing')}
            </h3>
            <p className="text-slate-300 text-sm mb-2">
              {t('msg_realtime_queue_tip')}
            </p>
            <div className="flex items-center gap-2 text-xs text-blue-300">
              <kbd className="px-2 py-1 bg-slate-800 rounded border border-slate-600">CLI</kbd>
              <span>{t('msg_press_e_in_cli')}</span>
            </div>
          </div>
        </div>
      </div>

      {/* Structured Queue List */}
      {queue.length > 1 && (
        <div className="flex items-center gap-2 text-xs text-slate-500">
          <GripVertical size={12} />
          <span>Drag to reorder tasks • 拖拽可调整任务顺序</span>
        </div>
      )}
      <div className="space-y-4">
        {loading ? (
          <div className="p-20 text-center text-slate-500 bg-slate-900/50 rounded-xl border border-slate-800">
             <Loader2 size={32} className="animate-spin mx-auto mb-4 opacity-20" />
             <p>{t('msg_loading_queue')}</p>
          </div>
        ) : queue.length === 0 ? (
          <div className="p-20 text-center text-slate-500 flex flex-col items-center gap-4 bg-slate-900/50 rounded-xl border border-slate-800">
            <Layers size={48} className="opacity-20" />
            <p>{t('msg_queue_empty')}</p>
          </div>
        ) : (
          queue.map((task, idx) => (
            <div
              key={idx}
              draggable={!isTaskActuallyLocked(task)}
              onDragStart={(e) => handleDragStart(e, idx)}
              onDragOver={(e) => handleDragOver(e, idx)}
              onDragLeave={handleDragLeave}
              onDrop={(e) => handleDrop(e, idx)}
              onDragEnd={handleDragEnd}
              className={`group relative rounded-xl p-5 transition-all backdrop-blur-sm ${
                isTaskActuallyLocked(task) ? 'cursor-not-allowed' : 'cursor-move'
              } ${
                draggedIndex === idx ? 'opacity-50 scale-95' : ''
              } ${
                dragOverIndex === idx && draggedIndex !== idx && !isTaskActuallyLocked(task) ? 'border-primary border-2 bg-primary/10' :
                isTaskActuallyLocked(task) ? 'bg-slate-900/20 border border-amber-500/30 shadow-amber-500/10 shadow-md' :
                'bg-slate-900/40 border border-slate-800 hover:border-slate-700'
              }`}
            >
                <div className="flex flex-col md:flex-row md:items-center justify-between gap-6">
                    <div className="flex items-start gap-4 flex-1 min-w-0">
                        <div className={`mt-1 flex-shrink-0 ${
                          isTaskActuallyLocked(task)
                            ? 'text-amber-400 cursor-not-allowed'
                            : 'text-slate-500 hover:text-slate-400 cursor-grab active:cursor-grabbing'
                        }`}>
                            {isTaskActuallyLocked(task) ? (
                              <span className="text-amber-400 text-sm">🔒</span>
                            ) : (
                              <GripVertical size={16} />
                            )}
                        </div>
                        <div className={`mt-1 flex-shrink-0 w-8 h-8 rounded-lg flex items-center justify-center font-mono text-xs font-bold ${
                            task.status === 'completed' ? 'bg-green-500/20 text-green-400' : 'bg-slate-800 text-slate-500'
                        }`}>
                            {(idx + 1).toString().padStart(2, '0')}
                        </div>
                        <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 mb-1 flex-wrap">
                                <span className={`text-[10px] px-1.5 py-0.5 rounded font-black uppercase ${
                                    task.task_type === TaskType.ALL_IN_ONE ? 'bg-orange-500/20 text-orange-400' :
                                    task.task_type === TaskType.TRANSLATE ? 'bg-blue-500/20 text-blue-400' : 'bg-purple-500/20 text-purple-400'
                                }`}>
                                    {task.task_type === TaskType.ALL_IN_ONE ? t('task_type_all_in_one') : task.task_type === TaskType.TRANSLATE ? t('task_type_translate') : t('task_type_polish')}
                                </span>
                                <h3 className="text-white font-bold truncate text-sm">{task.input_path}</h3>
                                <span className={`ml-auto md:ml-0 text-[10px] font-black uppercase px-2 py-0.5 rounded ${
                                    task.status === 'completed' ? 'bg-green-500/20 text-green-400' :
                                    task.status === 'translated' ? 'bg-blue-500/10 text-blue-400' :
                                    ['translating', 'polishing'].includes(task.status) ? 'bg-blue-500/20 text-blue-400 animate-pulse' :
                                    task.status === 'error' ? 'bg-red-500/20 text-red-400' : 'bg-slate-800 text-slate-500'
                                }`}>
                                    {task.status}
                                </span>
                            </div>
                            
                            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mt-2 text-[10px] text-slate-500 font-medium">
                                <div className="flex items-center gap-1.5"><Globe size={12}/> {task.source_lang || 'Auto'} → {task.target_lang || 'Auto'}</div>
                                <div className="flex items-center gap-1.5"><Layers size={12}/> {task.project_type || 'Auto'}</div>
                                <div className="flex items-center gap-1.5"><Settings2 size={12}/> {task.profile || 'Default'} / {task.rules_profile || 'Default'}</div>
                                {task.model && <div className="flex items-center gap-1.5 text-cyan-400/80"><Cpu size={12}/> {task.model}</div>}
                                {task.threads !== undefined && <div className="flex items-center gap-1.5 text-amber-400/80"><Zap size={12}/> {task.threads === 0 ? 'Auto' : task.threads} Threads</div>}
                                {task.tokens_limit !== undefined && task.tokens_limit !== null && <div className="flex items-center gap-1.5 text-purple-400/80"><MessageSquare size={12}/> {task.tokens_limit} Tokens</div>}
                                {task.lines_limit !== undefined && task.lines_limit !== null && <div className="flex items-center gap-1.5 text-blue-400/80"><MessageSquare size={12}/> {task.lines_limit} Lines</div>}
                            </div>
                        </div>
                    </div>

                    <div className="flex items-center gap-2 border-t md:border-t-0 border-slate-800 pt-3 md:pt-0">
                        <button
                            onClick={() => isTaskActuallyLocked(task) ? showLockedTaskAlert(task) : openEditModal(idx)}
                            className={`p-2 rounded-lg transition-all ${
                              isTaskActuallyLocked(task)
                                ? 'text-slate-600 cursor-not-allowed'
                                : 'text-slate-400 hover:text-primary hover:bg-primary/10'
                            }`}
                            title={isTaskActuallyLocked(task) ? t('msg_task_locked').replace('{}', '') : t('btn_edit')}
                        >
                            <Edit3 size={18} />
                        </button>
                        <button
                            onClick={() => isTaskActuallyLocked(task) ? showLockedTaskAlert(task) : handleRemoveTask(idx)}
                            className={`p-2 rounded-lg transition-all ${
                              isTaskActuallyLocked(task)
                                ? 'text-slate-600 cursor-not-allowed'
                                : 'text-slate-400 hover:text-red-400 hover:bg-red-400/10'
                            }`}
                            title={isTaskActuallyLocked(task) ? t('msg_task_locked').replace('{}', '') : t('btn_remove')}
                        >
                            <Trash2 size={18} />
                        </button>
                    </div>
                </div>
            </div>
          ))
        )}
      </div>

      {/* Footer Actions */}
      {queue.length > 0 && (
        <div className="fixed bottom-8 left-1/2 -translate-x-1/2 flex items-center gap-4 bg-slate-900/90 backdrop-blur-xl border border-slate-700/50 p-2 rounded-2xl shadow-2xl z-50 animate-in fade-in zoom-in duration-300">
           <button
            onClick={handleClearQueue}
            className="px-6 py-3 text-red-400 font-bold hover:bg-red-400/10 rounded-xl transition-all"
           >
             {t('menu_queue_clear')}
           </button>
           <button
            onClick={handleRunQueue}
            className={`px-10 py-3 rounded-xl font-black uppercase tracking-widest flex items-center gap-3 transition-all transform hover:scale-105 active:scale-95 ${
                isElysia ? 'bg-pink-500 text-white shadow-[0_0_20px_rgba(236,72,153,0.4)]' : 'bg-primary text-slate-900 shadow-[0_0_20px_rgba(34,211,238,0.4)]'
            }`}
           >
             <Play size={20} fill="currentColor" />
             {t('menu_queue_start')}
           </button>
        </div>
      )}

      {/* Task Config Modal */}
      {showModal && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-black/80 backdrop-blur-md animate-in fade-in duration-300">
            <div className="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-3xl overflow-hidden shadow-2xl flex flex-col max-h-[90vh]">
                <div className="p-6 border-b border-slate-800 flex items-center justify-between bg-slate-800/20">
                    <h2 className="text-xl font-black text-white uppercase flex items-center gap-3 tracking-tighter">
                        {editIndex !== null ? <Edit3 className="text-primary"/> : <Plus className="text-primary"/>}
                        {editIndex !== null ? t('modal_modify_queue_task') : t('menu_queue_add')}
                    </h2>
                    <button onClick={() => setShowModal(false)} className="text-slate-500 hover:text-white transition-colors"><X size={24}/></button>
                </div>
                
                <div className="flex-1 overflow-y-auto p-8 custom-scrollbar">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
                        {/* Section: Basic */}
                        <div className="space-y-6">
                            <h4 className="text-[10px] font-black text-slate-500 uppercase tracking-[0.2em] border-b border-slate-800 pb-2">{t('section_core_assignment')}</h4>
                            
                            <div className="space-y-2">
                                <label className="text-xs font-bold text-slate-400 uppercase tracking-wider">{t('ui_recent_type')}</label>
                                <select 
                                    value={taskForm.task_type}
                                    onChange={e => {
                                      const taskType = e.target.value as TaskType.TRANSLATE | TaskType.POLISH | TaskType.ALL_IN_ONE;
                                      setTaskForm({
                                        ...taskForm,
                                        task_type: taskType,
                                        polish_mode: taskType === TaskType.TRANSLATE ? null : taskForm.polish_mode
                                      });
                                    }}
                                    className="w-full bg-slate-950 border border-slate-800 rounded-lg px-4 py-2.5 text-white focus:ring-2 ring-primary/20 outline-none transition-all"
                                >
                                    <option value={TaskType.TRANSLATE}>{t('menu_start_translation')}</option>
                                    <option value={TaskType.POLISH}>{t('menu_start_polishing')}</option>
                                    <option value={TaskType.ALL_IN_ONE}>{t('menu_start_all_in_one')}</option>
                                </select>
                            </div>

                            <div className="space-y-2">
                                <label className="text-xs font-bold text-slate-400 uppercase tracking-wider">{t('setting_input_path')}</label>
                                <input 
                                    placeholder="/path/to/novel"
                                    value={taskForm.input_path}
                                    onChange={e => setTaskForm({...taskForm, input_path: e.target.value})}
                                    className="w-full bg-slate-950 border border-slate-800 rounded-lg px-4 py-2.5 text-white focus:ring-2 ring-primary/20 outline-none placeholder:text-slate-700"
                                />
                            </div>

                            <div className="space-y-2">
                                <label className="text-xs font-bold text-slate-400 uppercase tracking-wider">Profiles</label>
                                <div className="grid grid-cols-2 gap-3">
                                    <select
                                        value={taskForm.profile || ''}
                                        onChange={e => setTaskForm({...taskForm, profile: e.target.value})}
                                        className="bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-white outline-none"
                                    >
                                        <option value="">{t('label_config_profile')}</option>
                                        {profiles.map(p => <option key={p} value={p}>{p}</option>)}
                                    </select>
                                    <select
                                        value={taskForm.rules_profile || ''}
                                        onChange={e => setTaskForm({...taskForm, rules_profile: e.target.value})}
                                        className="bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-white outline-none"
                                    >
                                        <option value="">{t('label_rules_profile')}</option>
                                        {rulesProfiles.map(p => <option key={p} value={p}>{p}</option>)}
                                    </select>
                                </div>
                            </div>
                        </div>

                        {/* Section: Language & Project */}
                        <div className="space-y-6">
                            <h4 className="text-[10px] font-black text-slate-500 uppercase tracking-[0.2em] border-b border-slate-800 pb-2">{t('section_language_logic')}</h4>
                            
                            <div className="grid grid-cols-2 gap-4">
                                <div className="space-y-2">
                                    <label className="text-xs font-bold text-slate-400 uppercase tracking-wider">{t('label_source')}</label>
                                    <input
                                        placeholder={t('placeholder_current')}
                                        value={taskForm.source_lang || ''}
                                        onChange={e => setTaskForm({...taskForm, source_lang: e.target.value})}
                                        className="w-full bg-slate-950 border border-slate-800 rounded-lg px-4 py-2 text-sm text-white outline-none"
                                    />
                                </div>
                                <div className="space-y-2">
                                    <label className="text-xs font-bold text-slate-400 uppercase tracking-wider">{t('label_target')}</label>
                                    <input
                                        placeholder={t('placeholder_current')}
                                        value={taskForm.target_lang || ''}
                                        onChange={e => setTaskForm({...taskForm, target_lang: e.target.value})}
                                        className="w-full bg-slate-950 border border-slate-800 rounded-lg px-4 py-2 text-sm text-white outline-none"
                                    />
                                </div>
                            </div>

                            <div className="space-y-2">
                                <label className="text-xs font-bold text-slate-400 uppercase tracking-wider">{t('setting_project_type')}</label>
                                <input
                                    placeholder={t('placeholder_project_type')}
                                    value={taskForm.project_type || ''}
                                    onChange={e => setTaskForm({...taskForm, project_type: e.target.value})}
                                    className="w-full bg-slate-950 border border-slate-800 rounded-lg px-4 py-2 text-sm text-white outline-none"
                                />
                            </div>

                            <div className="space-y-2">
                                <label className="text-xs font-bold text-slate-400 uppercase tracking-wider">{t('setting_output_path')}</label>
                                <input
                                    placeholder={t('placeholder_output_default')}
                                    value={taskForm.output_path || ''}
                                    onChange={e => setTaskForm({...taskForm, output_path: e.target.value})}
                                    className="w-full bg-slate-950 border border-slate-800 rounded-lg px-4 py-2 text-sm text-white outline-none"
                                />
                            </div>
                        </div>

                        {/* Section: Fine-grained Overrides */}
                        <div className="md:col-span-2 pt-4">
                            <h4 className="text-[10px] font-black text-primary uppercase tracking-[0.2em] border-b border-primary/20 pb-2 mb-6">{t('section_advanced_overrides')}</h4>
                            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                                {/* API Overrides */}
                                <div className="space-y-2">
                                    <label className="text-[10px] font-bold text-slate-500 uppercase flex items-center gap-2"><Server size={12}/> {t('label_platform_override')}</label>
                                    <select 
                                        value={taskForm.platform || ''}
                                        onChange={e => setTaskForm({...taskForm, platform: e.target.value})}
                                        className="w-full bg-slate-950 border border-slate-800 rounded-lg px-4 py-2 text-xs text-white outline-none"
                                    >
                                        <option value="">{t('tip_follow_profile')}</option>
                                        {Object.keys(config?.platforms || {}).map(p => <option key={p} value={p}>{config?.platforms[p]?.name || p}</option>)}
                                    </select>
                                </div>
                                <div className="space-y-2">
                                    <label className="text-[10px] font-bold text-slate-500 uppercase flex items-center gap-2"><Globe size={12}/> {t('label_url_override')}</label>
                                    <input 
                                        placeholder={t('tip_follow_profile')}
                                        value={taskForm.api_url || ''}
                                        onChange={e => setTaskForm({...taskForm, api_url: e.target.value})}
                                        className="w-full bg-slate-950 border border-slate-800 rounded-lg px-4 py-2 text-xs text-white outline-none"
                                    />
                                </div>
                                <div className="space-y-2">
                                    <label className="text-[10px] font-bold text-slate-500 uppercase flex items-center gap-2"><BookOpen size={12}/> {t('label_key_override')}</label>
                                    <p className="rounded-lg border border-slate-800 bg-slate-950 px-4 py-2 text-xs text-slate-400">
                                        {t('queue_api_key_storage_notice')}
                                    </p>
                                </div>
                                <div className="space-y-2">
                                    <label className="text-[10px] font-bold text-slate-500 uppercase flex items-center gap-2"><Cpu size={12}/> {t('label_model_override')}</label>
                                    <select 
                                        value={taskForm.model || ''}
                                        onChange={e => setTaskForm({...taskForm, model: e.target.value})}
                                        className="w-full bg-slate-950 border border-slate-800 rounded-lg px-4 py-2 text-xs text-white outline-none"
                                    >
                                        <option value="">{t('tip_follow_profile')}</option>
                                        {taskForm.platform && config?.platforms[taskForm.platform]?.model_datas?.map(m => (
                                            <option key={m} value={m}>{m}</option>
                                        ))}
                                    </select>
                                </div>
                                {/* Performance Overrides */}
                                <div className="space-y-2">
                                    <label className="text-[10px] font-bold text-slate-500 uppercase flex items-center gap-2"><Zap size={12}/> {t('label_threads_override')}</label>
                                    <input
                                        type="number"
                                        placeholder={t('tip_follow_profile')}
                                        value={taskForm.threads ?? ''}
                                        onChange={e => setTaskForm({...taskForm, threads: e.target.value ? parseInt(e.target.value) : undefined})}
                                        className="w-full bg-slate-950 border border-slate-800 rounded-lg px-4 py-2 text-xs text-white outline-none"
                                    />
                                </div>
                                <div className="space-y-2">
                                    <label className="text-[10px] font-bold text-slate-500 uppercase flex items-center gap-2">{t('label_context_lines')}</label>
                                    <input
                                        type="number"
                                        placeholder={t('tip_follow_profile')}
                                        value={taskForm.pre_lines ?? ''}
                                        onChange={e => setTaskForm({...taskForm, pre_lines: e.target.value ? parseInt(e.target.value) : undefined})}
                                        className="w-full bg-slate-950 border border-slate-800 rounded-lg px-4 py-2 text-xs text-white outline-none"
                                    />
                                </div>
                                
                                <div className="space-y-2">
                                    <label className="text-[10px] font-bold text-slate-500 uppercase">{t('setting_limit_mode')}</label>
                                    <div className="grid grid-cols-3 gap-1 bg-slate-950 border border-slate-800 rounded-lg p-1">
                                        <button
                                            type="button"
                                            onClick={() => setLimitMode('profile')}
                                            className={`px-2 py-1.5 rounded text-[10px] font-bold transition-all ${limitMode === 'profile' ? 'bg-primary text-slate-900' : 'text-slate-500 hover:text-slate-200'}`}
                                        >
                                            {t('tip_follow_profile')}
                                        </button>
                                        <button
                                            type="button"
                                            onClick={() => setLimitMode('lines')}
                                            className={`px-2 py-1.5 rounded text-[10px] font-bold transition-all ${limitMode === 'lines' ? 'bg-primary text-slate-900' : 'text-slate-500 hover:text-slate-200'}`}
                                        >
                                            Lines
                                        </button>
                                        <button
                                            type="button"
                                            onClick={() => setLimitMode('tokens')}
                                            className={`px-2 py-1.5 rounded text-[10px] font-bold transition-all ${limitMode === 'tokens' ? 'bg-primary text-slate-900' : 'text-slate-500 hover:text-slate-200'}`}
                                        >
                                            Tokens
                                        </button>
                                    </div>
                                    {limitMode === 'lines' && (
                                        <input
                                            type="number"
                                            min={1}
                                            max={100}
                                            value={taskForm.lines_limit ?? ''}
                                            onChange={e => setTaskForm({
                                                ...taskForm,
                                                lines_limit: e.target.value ? Math.max(1, Math.min(100, parseInt(e.target.value) || 1)) : undefined,
                                                tokens_limit: undefined
                                            })}
                                            className="w-full bg-slate-950 border border-slate-800 rounded-lg px-4 py-2 text-xs text-white outline-none"
                                        />
                                    )}
                                    {limitMode === 'tokens' && (
                                        <input
                                            type="number"
                                            min={400}
                                            max={16000}
                                            step={100}
                                            value={taskForm.tokens_limit ?? ''}
                                            onChange={e => setTaskForm({
                                                ...taskForm,
                                                lines_limit: undefined,
                                                tokens_limit: e.target.value ? Math.max(400, Math.min(16000, parseInt(e.target.value) || 400)) : undefined
                                            })}
                                            className="w-full bg-slate-950 border border-slate-800 rounded-lg px-4 py-2 text-xs text-white outline-none"
                                        />
                                    )}
                                </div>
                                <div className="space-y-2">
                                    <label className="text-[10px] font-bold text-slate-500 uppercase">{t('label_think_budget')}</label>
                                    <input
                                        type="number"
                                        placeholder={t('tip_follow_profile')}
                                        value={taskForm.thinking_budget ?? ''}
                                        onChange={e => setTaskForm({...taskForm, thinking_budget: e.target.value ? parseInt(e.target.value) : undefined})}
                                        className="w-full bg-slate-950 border border-slate-800 rounded-lg px-4 py-2 text-xs text-white outline-none"
                                    />
                                    <p className="text-[9px] text-slate-500 italic px-1">{t('hint_think_budget')}</p>
                                </div>
                                <div className="space-y-2">
                                    <label className="text-[10px] font-bold text-slate-500 uppercase">{t('label_think_depth')}</label>
                                    <input
                                        type="text"
                                        list="queue-think-depth-options"
                                        placeholder={t('tip_follow_profile')}
                                        value={taskForm.think_depth ?? ''}
                                        onChange={e => setTaskForm({...taskForm, think_depth: e.target.value})}
                                        className="w-full bg-slate-950 border border-slate-800 rounded-lg px-4 py-2 text-xs text-white outline-none"
                                    />
                                    <datalist id="queue-think-depth-options">
                                        <option value="minimal" />
                                        <option value="low" />
                                        <option value="medium" />
                                        <option value="high" />
                                        <option value="xhigh" />
                                        <option value="max" />
                                    </datalist>
                                </div>
                                <div className="space-y-2">
                                    <label className="text-[10px] font-bold text-slate-500 uppercase">{t('setting_polishing_mode_selection')}</label>
                                    <select
                                        value={taskForm.polish_mode || ''}
                                        onChange={e => setTaskForm({...taskForm, polish_mode: (e.target.value || undefined) as QueueTaskItem['polish_mode']})}
                                        disabled={taskForm.task_type === TaskType.TRANSLATE}
                                        className="w-full bg-slate-950 border border-slate-800 rounded-lg px-4 py-2 text-xs text-white outline-none disabled:opacity-40"
                                    >
                                        <option value="">{t('tip_follow_profile')}</option>
                                        <option value="translated_text_polish">{t('choice_translated_text_polish')}</option>
                                        <option value="source_text_polish">{t('choice_source_text_polish')}</option>
                                    </select>
                                </div>
                                <div className="flex flex-wrap gap-4">
                                    <label className="flex items-center gap-2 text-[10px] font-bold text-slate-500 uppercase">
                                        <input
                                            type="checkbox"
                                            checked={!!taskForm.resume}
                                            onChange={e => setTaskForm({...taskForm, resume: e.target.checked})}
                                        />
                                        Resume
                                    </label>
                                    <label className="flex items-center gap-2 text-[10px] font-bold text-slate-500 uppercase">
                                        <input
                                            type="checkbox"
                                            checked={taskForm.failover === true}
                                            onChange={e => setTaskForm({...taskForm, failover: e.target.checked})}
                                        />
                                        Failover override
                                    </label>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>

                <div className="p-6 border-t border-slate-800 flex justify-end gap-3 bg-slate-800/30">
                    <button
                        onClick={() => setShowModal(false)}
                        className="px-6 py-2 rounded-lg font-bold text-slate-400 hover:text-white transition-colors"
                    >
                        {t('btn_cancel')}
                    </button>
                    <button
                        onClick={handleSubmit}
                        className={`px-8 py-2 rounded-lg font-black uppercase tracking-widest transition-all ${
                            isElysia ? 'bg-pink-500 text-white hover:bg-pink-400' : 'bg-primary text-slate-900 hover:bg-cyan-300'
                        }`}
                    >
                        {editIndex !== null ? t('btn_save_changes') : t('btn_add_to_queue')}
                    </button>
                </div>
            </div>
        </div>
      )}
    </div>
  );
};
