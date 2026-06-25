import React, { useState, useEffect } from 'react';
import { Save, RefreshCw, Plus, Trash2, ToggleLeft, ToggleRight, AlertTriangle, Globe, Server, FileJson, CheckCircle, Loader2, Wifi, WifiOff, Edit2, X, Check, ChevronDown, Sparkles, DownloadCloud } from 'lucide-react';
import { AppConfig, PlatformConfig } from '../types';
import { MangaModelEngineOption, MangaModelManagerManifest, MangaJob } from '../types/manga';
import { DataService } from '../services/DataService';
import { nativeConfirm } from '../services/nativeDialog';
import { PROJECT_TYPES } from '../constants';
import { useI18n } from '../contexts/I18nContext';
import { useGlobal } from '../contexts/GlobalContext';

export const Settings: React.FC = () => {
  const { t, language, setLanguage, availableLanguages } = useI18n();
  const { config, setConfig, activeTheme, triggerRipple, unlockThemeWithNotification, unlockedThemes } = useGlobal(); // Use Global Context
  const elysiaActive = activeTheme === 'elysia';

  const getCharacterName = (id: string) => {
    switch(id) {
        case 'elysia': return '无瑕·爱莉希雅';
        case 'eden': return '黄金·伊甸';
        case 'mobius': return '无限·梅比乌斯';
        case 'pardofelis': return '空梦·帕朵菲莉丝';
        case 'griseo': return '繁星·格蕾修';
        case 'kevin': return '救世·凯文';
        case 'kalpas': return '鏖灭·千劫';
        case 'aponia': return '戒律·阿波尼亚';
        case 'villv': return '螺旋·维尔薇';
        case 'su': return '天慧·苏';
        case 'sakura': return '刹那·樱';
        case 'kosma': return '旭光·科斯魔';
        case 'hua': return '浮生·华';
        case 'herrscher_of_human': return '真我·人之律者';
        default: return '默认主题';
    }
  };

  const getThemeColorClass = () => {
    switch(activeTheme) {
        case 'elysia': return 'text-pink-500';
        case 'herrscher_of_human': return 'text-[#ff8fa3]';
        case 'eden': return 'text-amber-500';
        case 'mobius': return 'text-green-500';
        case 'pardofelis': return 'text-[#7f5539]';
        case 'griseo': return 'text-blue-400';
        case 'kevin': return 'text-sky-500';
        case 'kalpas': return 'text-red-500';
        case 'aponia': return 'text-purple-400';
        case 'villv': return 'text-orange-600';
        case 'su': return 'text-emerald-500';
        case 'sakura': return 'text-pink-400';
        case 'kosma': return 'text-indigo-400';
        case 'hua': return 'text-orange-500';
        default: return 'text-primary';
    }
  };

  const getTabActiveClass = (isSelected: boolean) => {
    if (!isSelected) return 'border-b-2 border-transparent text-slate-400 hover:text-slate-200';
    
    switch(activeTheme) {
        case 'elysia': return 'bg-pink-500/10 text-pink-500 border border-pink-200/50 shadow-[0_0_15px_rgba(255,102,153,0.15)]';
        case 'herrscher_of_human': return 'bg-pink-400/20 text-[#ff4d6d] border border-pink-300 shadow-[0_0_20px_rgba(255,143,163,0.3)]';
        case 'eden': return 'bg-amber-500/10 text-amber-500 border border-amber-500/50';
        case 'mobius': return 'bg-green-500/10 text-green-500 border border-green-500/50';
        case 'pardofelis': return 'bg-orange-200/50 text-[#7f5539] border border-orange-300';
        case 'griseo': return 'bg-blue-100 text-blue-500 border border-blue-200';
        case 'kevin': return 'bg-sky-100 text-sky-600 border border-sky-200';
        case 'kalpas': return 'bg-red-500/10 text-red-500 border border-red-500/50';
        case 'aponia': return 'bg-purple-500/10 text-purple-400 border border-purple-500/30';
        case 'villv': return 'bg-orange-500/10 text-orange-600 border border-orange-500/30';
        case 'su': return 'bg-emerald-500/10 text-emerald-500 border border-emerald-500/30';
        case 'sakura': return 'bg-pink-400/10 text-pink-500 border border-pink-400/30';
        case 'kosma': return 'bg-indigo-500/10 text-indigo-400 border border-indigo-500/30';
        case 'hua': return 'bg-orange-500/10 text-orange-500 border border-orange-500/30';
        default: return 'border-b-2 border-primary text-primary';
    }
  };
  
  const [activeTab, setActiveTab] = useState<'general' | 'api' | 'project' | 'features' | 'system' | 'profiles'>('general');
  const [newPoolItem, setNewPoolItem] = useState('');
  const [saving, setSaving] = useState(false);
  
  // Temp Files State
  const [tempFiles, setTempFiles] = useState<{name: string, path: string, size: number}[]>([]);
  const [selectedTempFiles, setSelectedTempFiles] = useState<string[]>([]);
  const [isDeletingFiles, setIsDeletingFiles] = useState(false);
  const [isTempFilesExpanded, setIsTempFilesExpanded] = useState(false);

  // Profile Management State
  const [profiles, setProfiles] = useState<string[]>([]);
  const [loadingProfiles, setLoadingProfiles] = useState(false);
  const [switchingProfile, setSwitchingProfile] = useState<string | null>(null);
  
  // CRUD State
  const [isCreating, setIsCreating] = useState(false);
  const [isCreatingPlatform, setIsCreatingPlatform] = useState(false);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [tempName, setTempName] = useState('');
  const [actionError, setActionError] = useState<string | null>(null);
  const [mangaModelManager, setMangaModelManager] = useState<MangaModelManagerManifest | null>(null);
  const [mangaModelLoading, setMangaModelLoading] = useState(false);
  const [mangaModelJob, setMangaModelJob] = useState<MangaJob | null>(null);
  const [mangaModelError, setMangaModelError] = useState('');

  // System Online Status Check
  const [isOnline, setIsOnline] = useState(true);
  
  // Aponia Trigger: Click the warning icon 3 times
  const [warningClicks, setWarningClicks] = useState(0);
  const handleWarningClick = (e: React.MouseEvent) => {
    const newCount = warningClicks + 1;
    setWarningClicks(newCount);
    if (newCount >= 3) {
      unlockThemeWithNotification('aponia');
      setWarningClicks(0);
    }
  };

  // Hua Trigger: Save 5 times
  const [saveCount, setSaveCount] = useState(0);
  const handleHuaSaveCount = () => {
    const next = saveCount + 1;
    setSaveCount(next);
    if (next >= 5) {
      unlockThemeWithNotification('hua');
      setSaveCount(0);
    }
  };

  // --- Elysia Egg Logic ---
  const isElysiaProfile = config?.active_profile?.toLowerCase() === 'elysia';
  const elysiaUnlocked = localStorage.getItem('elysia_unlocked') === 'true' || isElysiaProfile;

  useEffect(() => {
    loadProfiles();
    handleLoadTempFiles();
    loadMangaModelManager();
  }, []);

  const handleToggleElysia = (e: React.MouseEvent) => {
    triggerRipple(e.clientX, e.clientY);
  };

  // Generic Change Handler for Global Config
  const handleChange = (field: keyof AppConfig, value: any) => {
    if (!config) return;
    if (field === 'tokens_limit') {
        value = Math.max(400, Math.min(16000, parseInt(value) || 400));
    } else if (field === 'lines_limit') {
        value = Math.max(1, Math.min(100, parseInt(value) || 1));
    }
    
    // --- Easter Eggs Triggers ---
    
    // Mobius: Enter "Infinity" or "∞"
    if (typeof value === 'string' && (value.toLowerCase() === 'infinity' || value === '∞')) {
        unlockThemeWithNotification('mobius');
        return;
    }

    // Kevin: Threads 0 or 99
    if (field === 'user_thread_counts') {
        const val = parseInt(value);
        if (val === 0 || val >= 99) {
            unlockThemeWithNotification('kevin');
        }
    }

    // Eden: Failover Threshold 1920 or 999999
    if (field === 'api_failover_threshold') {
        const val = parseInt(value);
        if (val === 1920 || val >= 999999) {
            unlockThemeWithNotification('eden');
        }
    }

    if (field === 'translation_consistency_enhancement') {
        setConfig({
            ...config,
            [field]: value,
            user_thread_counts: value ? 1 : config.user_thread_counts,
        });
        return;
    }

    setConfig({ ...config, [field]: value });
  };
  
  // Handle Response Check Switch (Nested Object)
  const handleCheckChange = (key: string, val: boolean) => {
      if (!config) return;
      // 防护：确保 response_check_switch 是对象而非字符串
      const currentSwitch = typeof config.response_check_switch === 'object' && config.response_check_switch !== null
          ? config.response_check_switch
          : { newline_character_count_check: false, return_to_original_text_check: false, residual_original_text_check: false, reply_format_check: false };
      setConfig({
          ...config,
          response_check_switch: {
              ...currentSwitch,
              [key]: val
          }
      });
  };

  const handleSaveConfig = async () => {
    if (!config) return;
    setSaving(true);
    try {
        await DataService.saveConfig(config);
        alert(t('msg_saved'));
    } catch (e) {
        console.error("Failed to save", e);
        alert("Save failed.");
    } finally {
        setSaving(false);
    }
  };

  const handlePlatformChange = (tag: string) => {
      if (!config || !config.platforms[tag]) return;
      const p = config.platforms[tag];
      setConfig({
          ...config,
          target_platform: tag,
          base_url: p.api_url || config.base_url,
          model: p.model || config.model,
          api_settings: { translate: tag, polish: tag }
      });
  };

  const handleCreatePlatform = async () => {
      if (!tempName.trim()) { setActionError("Name cannot be empty"); return; }
      try {
          await DataService.createPlatform(tempName);
          const newConfig = await DataService.getConfig();
          setConfig(newConfig);
          setIsCreatingPlatform(false);
          setTempName('');
      } catch (e: any) { setActionError(e.message); }
  };

  const handlePoolAdd = () => {
    if (!config) return;
    if (newPoolItem && !config.backup_apis.includes(newPoolItem)) {
      handleChange('backup_apis', [...config.backup_apis, newPoolItem]);
      setNewPoolItem('');
    }
  };

  const handlePoolRemove = (item: string) => {
    if (!config) return;
    handleChange('backup_apis', config.backup_apis.filter(i => i !== item));
  };

  const handleLoadTempFiles = async () => {
      const list = await DataService.listTempFiles();
      setTempFiles(list);
  };

  const handleToggleTempFile = (name: string) => {
      setSelectedTempFiles(prev => 
          prev.includes(name) ? prev.filter(f => f !== name) : [...prev, name]
      );
  };

  const handleDeleteTempFiles = async () => {
      if (selectedTempFiles.length === 0) return;
      if (!(await nativeConfirm(`Delete ${selectedTempFiles.length} files?`))) return;
      setIsDeletingFiles(true);
      try {
          await DataService.deleteTempFiles(selectedTempFiles);
          await handleLoadTempFiles();
          setSelectedTempFiles([]);
      } catch (e) {
          alert("Failed to delete files");
      } finally {
          setIsDeletingFiles(false);
      }
  };

  const handleSwitchProfile = async (profileName: string) => {
      if (switchingProfile || renamingId || isCreating) return;
      if (config?.active_profile === profileName) return;
      setSwitchingProfile(profileName);
      try {
          const newConfig = await DataService.switchProfile(profileName);
          setConfig(newConfig);
      } catch (e) {
          console.error("Failed to switch profile", e);
      } finally {
          setSwitchingProfile(null);
      }
  };

  const loadProfiles = async () => {
      setLoadingProfiles(true);
      try {
          const list = await DataService.getProfiles();
          setProfiles(list);
      } catch (e) {
          console.error("Failed to load profiles", e);
      } finally {
          setLoadingProfiles(false);
      }
  };

  const startCreate = () => { setTempName(''); setActionError(null); setIsCreating(true); };
  const cancelCreate = () => { setIsCreating(false); setTempName(''); setActionError(null); };
  const confirmCreate = async () => {
      if (!tempName.trim()) { setActionError("Name cannot be empty"); return; }
      try {
          await DataService.createProfile(tempName, config?.active_profile);
          await loadProfiles();
          setIsCreating(false);
          setTempName('');
      } catch (e: any) { setActionError(e.message); }
  };

  const startRename = (name: string, e: React.MouseEvent) => {
      e.stopPropagation(); setRenamingId(name); setTempName(name); setActionError(null);
  };
  const cancelRename = () => { setRenamingId(null); setTempName(''); setActionError(null); };
  const confirmRename = async (oldName: string) => {
      if (!tempName.trim() || tempName === oldName) { cancelRename(); return; }
      try {
          await DataService.renameProfile(oldName, tempName);
          if (config?.active_profile === oldName) { setConfig({ ...config, active_profile: tempName }); }
          await loadProfiles();
          setRenamingId(null);
      } catch (e: any) { setActionError(e.message); }
  };

  const handleDelete = async (name: string, e: React.MouseEvent) => {
      e.stopPropagation();
      if (!(await nativeConfirm(t('msg_profile_delete_confirm').replace('{}', name)))) return;
      try { await DataService.deleteProfile(name); await loadProfiles(); } catch (e: any) { alert(e.message); }
  };

  const loadMangaModelManager = async () => {
      setMangaModelLoading(true);
      try {
          setMangaModelManager(await DataService.getMangaModelManager());
          setMangaModelError('');
      } catch (e: any) {
          setMangaModelError(e.message || 'Failed to load manga model manager');
      } finally {
          setMangaModelLoading(false);
      }
  };

  const waitForGlobalMangaJob = async (initialJob: MangaJob) => {
      setMangaModelJob(initialJob);
      if (!initialJob.job_id || ['completed', 'failed', 'cancelled'].includes(initialJob.status)) {
          return initialJob;
      }
      let latest = initialJob;
      for (let attempt = 0; attempt < 7200; attempt += 1) {
          await new Promise((resolve) => window.setTimeout(resolve, 500));
          latest = await DataService.getGlobalMangaJob(initialJob.job_id);
          setMangaModelJob(latest);
          if (['completed', 'failed', 'cancelled'].includes(latest.status)) break;
      }
      return latest;
  };

  const handleDownloadAllMangaModels = async () => {
      setMangaModelError('');
      try {
          const job = await DataService.startMangaAllModelsDownload();
          const settled = await waitForGlobalMangaJob(job);
          await loadMangaModelManager();
          if (settled.status !== 'completed') {
              setMangaModelError(settled.error_message || settled.message || t('manga_notice_model_prepare_warning', t('manga_model_download_all')));
          }
      } catch (e: any) {
          setMangaModelError(e.message || t('manga_notice_model_prepare_warning', t('manga_model_download_all')));
      }
  };

  if (!config) {
      return <div className="p-12 text-center text-slate-500 animate-pulse">Loading Configuration...</div>;
  }

    const Toggle = ({ field, label, desc, disabled = false }: { field: keyof AppConfig, label: string, desc?: string, disabled?: boolean }) => {
      return (
        <div className={`flex items-center justify-between p-3 border rounded-lg ${disabled ? 'border-slate-800 bg-slate-900/10 opacity-60' : 'border-slate-700 bg-slate-900/30'}`}>
          <div className="mr-4">
            <h4 className="text-slate-200 font-medium text-sm">{label}</h4>
            {desc && <p className="text-xs text-slate-500">{desc}</p>}
          </div>
          <button
            onClick={() => !disabled && handleChange(field, !config[field])}
            disabled={disabled}
            className={`transition-all duration-300 ${disabled ? 'cursor-not-allowed text-slate-700' : config[field] ? getThemeColorClass() : 'text-slate-600'}`}
          >
            {config[field] ? <ToggleRight size={32} /> : <ToggleLeft size={32} />}
          </button>
        </div>
      );
    };

  const isConsistencyEnabled = !!config.translation_consistency_enhancement;
  const showLineSplitOptimization = (config.chunk_soft_limit_extra_lines ?? 10) > 0;
  const lineSplitOptimizationMode = config.line_split_optimization_mode ?? 'off';
  const deepSeekRecommendedModels = ['deepseek-v4-flash', 'deepseek-v4-pro', 'deepseek-chat', 'deepseek-reasoner'];
  const activePlatformKey = config.target_platform || '';
  const activePlatformConfig = config.platforms?.[activePlatformKey] || {};
  const isAnthropicThinking = activePlatformConfig.api_format === 'Anthropic';
  const isDeepSeekThinking = activePlatformKey.toLowerCase() === 'deepseek';
  const isOpenAIThinking = activePlatformConfig.api_format === 'OpenAI';
  const mangaDeviceOptions = [
    { value: 'auto', label: t('manga_settings_device_auto') },
    { value: 'cpu', label: t('manga_settings_device_cpu') },
    { value: 'cuda', label: t('manga_settings_device_cuda') },
    { value: 'mps', label: t('manga_settings_device_mps') },
  ];
  const mangaEngineStageConfigKeys = {
    detect: 'manga_detect_engine',
    segment: 'manga_segment_engine',
    ocr: 'manga_ocr_engine',
    inpaint: 'manga_inpaint_engine',
  } as const;
  const getMangaEngineStageLabel = (stage: string) => {
    if (stage === 'detect') return t('manga_engine_detect');
    if (stage === 'segment') return t('manga_engine_segment');
    if (stage === 'ocr') return t('manga_engine_ocr');
    if (stage === 'inpaint') return t('manga_engine_inpaint');
    return stage;
  };
  const getMangaModelOptionStatus = (option: MangaModelEngineOption) => {
    if (option.selectable) return t('manga_ready');
    if (option.disabled_reason === 'unsupported_runtime') return t('manga_model_unsupported_runtime');
    return t('manga_model_not_downloaded');
  };
  const resolveMangaModelOptionValue = (value: string, options: MangaModelEngineOption[]) => {
    const rawValue = String(value || '').trim();
    if (!rawValue) return '';
    const direct = options.find((option) => option.model_id === rawValue);
    if (direct) return direct.model_id;
    const alias = options.find((option) => (option.aliases || []).includes(rawValue));
    return alias?.model_id || rawValue;
  };
  const DeviceSelect = ({ field, label }: { field: keyof AppConfig, label: string }) => (
    <div className="space-y-2">
      <label className="text-xs font-semibold text-slate-400 uppercase">{label}</label>
      <select
        value={(config[field] as string) || 'auto'}
        onChange={(e) => handleChange(field, e.target.value)}
        className="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-slate-200 focus:border-primary text-sm"
      >
        {mangaDeviceOptions.map(option => (
          <option key={option.value} value={option.value}>{option.label}</option>
        ))}
      </select>
    </div>
  );
  const MangaEngineSelect = ({ stage, field }: { stage: keyof typeof mangaEngineStageConfigKeys, field: keyof AppConfig }) => {
    const options = (mangaModelManager?.engine_options?.[stage] || []) as MangaModelEngineOption[];
    const fallback = stage === 'ocr'
      ? mangaModelManager?.default_ocr_model_id || ''
      : options[0]?.model_id || '';
    return (
      <div className="space-y-2">
        <label className="text-xs font-semibold text-slate-400 uppercase">{getMangaEngineStageLabel(stage)}</label>
        <select
          value={resolveMangaModelOptionValue((config[field] as string) || fallback, options)}
          onChange={(e) => handleChange(field, e.target.value)}
          className="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-slate-200 focus:border-primary text-sm"
          disabled={mangaModelLoading}
        >
          {options.length === 0 && <option value="">{t('manga_model_no_stage_options')}</option>}
          {options.map(option => {
            const disabled = !option.selectable;
            const label = [
              option.display_name || option.model_id,
              option.hardware_tier,
              option.quality_tier,
              disabled ? getMangaModelOptionStatus(option) : '',
            ].filter(Boolean).join(' · ');
            return (
              <option key={option.model_id} value={option.model_id} disabled={disabled}>
                {label}
              </option>
            );
          })}
        </select>
      </div>
    );
  };
  const deviceState = [
    config.manga_runtime_device || 'auto',
    config.manga_detect_device || 'auto',
    config.manga_ocr_device || 'auto',
    config.manga_inpaint_device || 'auto',
  ];
  const forcedCuda = deviceState.some(value => String(value).toLowerCase() === 'cuda');
  return (
    <div className="max-w-5xl mx-auto space-y-6 pb-12">
      <div className="flex justify-between items-center sticky top-0 bg-background/95 backdrop-blur z-20 py-4 border-b border-slate-800">
        <h1 className="text-2xl font-bold text-white">{t('ui_settings_title')}</h1>
        <button 
            onClick={() => { handleSaveConfig(); handleHuaSaveCount(); }}
            disabled={saving}
            className="flex items-center gap-2 bg-primary hover:bg-cyan-400 text-slate-900 px-4 py-2 rounded-lg font-bold transition-colors shadow-lg shadow-primary/20 disabled:opacity-50"
        >
          {saving ? <RefreshCw size={18} className="animate-spin" /> : <Save size={18} />}
          {t('ui_settings_save')}
        </button>
      </div>

      <div className="flex border-b border-slate-800 space-x-1 overflow-x-auto p-1">
        {[
            { id: 'general', label: t('ui_tab_general') },
            { id: 'api', label: t('ui_tab_api') },
            { id: 'project', label: t('ui_tab_project') },
            { id: 'features', label: t('ui_tab_features') },
            { id: 'system', label: t('ui_tab_system') },
            { id: 'profiles', label: t('menu_profiles') }
        ].map((tab) => {
          const isSelected = activeTab === tab.id;

          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id as any)}
              className={`px-4 py-2.5 text-sm font-bold capitalize transition-all duration-300 rounded-xl whitespace-nowrap ${getTabActiveClass(isSelected)}`}
            >
              {tab.label}
              {elysiaActive && isSelected && <Sparkles size={10} className="inline-block ml-1 text-pink-400 animate-pulse" />}
            </button>
          );
        })}
      </div>

      <div className="bg-surface/50 border border-slate-800 rounded-xl p-4 md:p-6 backdrop-blur-sm min-h-[500px]">
        {/* --- GENERAL TAB --- */}
        {activeTab === 'general' && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 md:gap-6">
            {(localStorage.getItem('elysia_unlocked') === 'true' && !isElysiaProfile) && (
              <div className={`col-span-1 md:col-span-2 p-4 rounded-lg flex items-center justify-between animate-in fade-in slide-in-from-top-4 duration-500 ${elysiaActive ? 'bg-pink-500/10 border border-pink-500/20' : 'bg-slate-900/50 border border-slate-700'}`}>
                  <div className="flex items-center gap-3">
                      <div className={`p-2 rounded-full ${elysiaActive ? 'bg-pink-500/20 text-pink-500' : 'bg-slate-800 text-slate-500'}`}>
                          <Sparkles size={20} />
                      </div>
                      <div>
                          <h4 className={`font-bold text-sm ${elysiaActive ? 'text-pink-600' : 'text-slate-300'}`}>真我·主题 (Elysia Theme)</h4>
                          <p className={`text-[10px] ${elysiaActive ? 'text-pink-400' : 'text-slate-500'}`}>在这里，与奇迹相遇。这就是我给你的小惊喜哦♪</p>
                      </div>
                  </div>
                  <button
                    onClick={handleToggleElysia}
                    className={`transition-all duration-300 ${elysiaActive ? 'text-pink-500 scale-110' : 'text-slate-600'}`}
                  >
                    {elysiaActive ? <ToggleRight size={32} /> : <ToggleLeft size={32} />}
                  </button>
              </div>
            )}

            <div className="space-y-2 col-span-1 md:col-span-2">
              <label className="text-xs font-semibold text-slate-400 uppercase">{t('setting_input_path')}</label>
              <input type="text" value={config.label_input_path} onChange={(e) => handleChange('label_input_path', e.target.value)}
                className="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-slate-200 focus:border-primary text-sm" />
            </div>
            <div className="space-y-2 col-span-1 md:col-span-1">
                <label className="text-xs text-slate-500 font-bold uppercase">{t('setting_output_path')}</label>
                <input 
                    type="text"
                    value={config.label_output_path}
                    onChange={(e) => handleChange('label_output_path', e.target.value)}
                    disabled={config.auto_set_output_path}
                    className={`w-full bg-slate-800/50 border border-slate-700 rounded p-2.5 text-sm text-slate-200 focus:border-primary outline-none transition-all ${config.auto_set_output_path ? 'opacity-50 grayscale cursor-not-allowed' : ''}`}
                    placeholder="Auto (beside input folder)"
                />
            </div>

            <div className="col-span-1 md:col-span-2">
                <Toggle 
                  field="auto_set_output_path" 
                  label={t('setting_auto_set_output_path')} 
                  desc="Ignore fixed output path and create output folder beside input file/folder" 
                />
            </div>

            <div className="space-y-2">
              <label className="text-xs font-semibold text-slate-400 uppercase">{t('prompt_source_lang')}</label>
              <input type="text" value={config.source_language} onChange={(e) => handleChange('source_language', e.target.value)}
                className="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-slate-200 focus:border-primary text-sm" />
            </div>
            <div className="space-y-2">
              <label className="text-xs font-semibold text-slate-400 uppercase">{t('prompt_target_lang')}</label>
              <input type="text" value={config.target_language} onChange={(e) => handleChange('target_language', e.target.value)}
                className="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-slate-200 focus:border-primary text-sm" />
            </div>
            <div className="space-y-2">
              <label className="text-xs font-semibold text-slate-400 uppercase">{t('setting_thread_count')}</label>
              <input
                type="number"
                value={isConsistencyEnabled ? 1 : config.user_thread_counts}
                onChange={(e) => handleChange('user_thread_counts', parseInt(e.target.value))}
                disabled={isConsistencyEnabled}
                className={`w-full border rounded-lg p-2.5 text-sm ${isConsistencyEnabled ? 'bg-slate-900/30 border-slate-800 text-slate-500 cursor-not-allowed' : 'bg-slate-900 border-slate-700 text-slate-200 focus:border-primary'}`}
              />
              {isConsistencyEnabled && (
                <p className="text-[10px] text-yellow-400">{t('msg_thread_locked_by_consistency')}</p>
              )}
            </div>
            <div className="space-y-2">
              <label className="text-xs font-semibold text-slate-400 uppercase">{t('setting_request_timeout')}</label>
              <input type="number" value={config.request_timeout} onChange={(e) => handleChange('request_timeout', parseInt(e.target.value))}
                className="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-slate-200 focus:border-primary text-sm" />
            </div>
          </div>
        )}

        {/* --- API TAB --- */}
        {activeTab === 'api' && (
          <div className="space-y-8">
            <div className="p-4 bg-slate-900/50 border border-primary/20 rounded-xl relative overflow-hidden">
                <div className="absolute top-0 left-0 w-1 h-full bg-primary" />
                <h3 className="text-primary font-bold mb-4 flex items-center gap-2">
                    <Server size={18} /> Platform Config
                </h3>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 md:gap-6">
                    <div className="space-y-2">
                        <div className="flex justify-between items-center">
                            <label className="text-xs font-semibold text-slate-400 uppercase">{t('label_platform')}</label>
                            <button 
                                onClick={() => setIsCreatingPlatform(true)}
                                className="text-[10px] font-bold text-primary flex items-center gap-1 hover:text-cyan-300 transition-colors"
                            >
                                <Plus size={12} /> {t('menu_api_add_custom') || 'Add New'}
                            </button>
                        </div>
                        
                        {isCreatingPlatform ? (
                            <div className="flex gap-2 animate-in fade-in slide-in-from-top-1 duration-200">
                                <input 
                                    autoFocus
                                    className="flex-1 bg-slate-950 border border-slate-600 rounded px-3 py-1 text-white text-xs"
                                    placeholder="Platform name..."
                                    value={tempName}
                                    onChange={(e) => setTempName(e.target.value)}
                                    onKeyDown={(e) => e.key === 'Enter' && handleCreatePlatform()}
                                />
                                <button onClick={() => setIsCreatingPlatform(false)} className="p-1 rounded bg-slate-800 text-slate-400"><X size={14}/></button>
                                <button onClick={handleCreatePlatform} className="p-1 rounded bg-primary text-slate-900"><Check size={14}/></button>
                            </div>
                        ) : (
                            <select 
                                value={config.target_platform} 
                                onChange={(e) => handlePlatformChange(e.target.value)}
                                className="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-slate-200 focus:border-primary text-sm"
                            >
                                {Object.values(config.platforms).map((p: PlatformConfig) => (
                                    <option key={p.tag} value={p.tag}>
                                        {p.name}
                                    </option>
                                ))}
                            </select>
                        )}
                        {actionError && isCreatingPlatform && <p className="text-red-400 text-[10px]">{actionError}</p>}
                    </div>
                    <div className="space-y-2">
                        <label className="text-xs font-semibold text-slate-400 uppercase">{t('label_model')}</label>
                        <input type="text" value={config.model} onChange={(e) => handleChange('model', e.target.value)}
                        className="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-slate-200 focus:border-primary text-sm" />
                        {config.target_platform?.toLowerCase() === 'deepseek' && (
                            <div className="mt-2 p-3 rounded-lg border border-cyan-500/20 bg-cyan-500/5">
                                <p className="text-[10px] font-bold uppercase tracking-wide text-cyan-300">{t('ui_deepseek_recommended_models')}</p>
                                <div className="mt-2 flex flex-wrap gap-2">
                                    {deepSeekRecommendedModels.map(modelName => (
                                        <button
                                            key={modelName}
                                            type="button"
                                            onClick={() => handleChange('model', modelName)}
                                            className={`px-2.5 py-1 rounded-full text-[10px] font-bold border transition-colors ${config.model === modelName ? 'border-cyan-300 bg-cyan-300/20 text-cyan-200' : 'border-slate-700 bg-slate-900 text-slate-300 hover:border-cyan-500/40 hover:text-cyan-200'}`}
                                        >
                                            {modelName}
                                        </button>
                                    ))}
                                </div>
                                <p className="mt-2 text-[10px] text-slate-400">{t('ui_deepseek_recommended_models_desc')}</p>
                            </div>
                        )}
                    </div>
                    <div className="col-span-1 md:col-span-2 space-y-2">
                        <label className="text-xs font-semibold text-slate-400 uppercase">{t('label_key')}</label>
                        <input type="password" 
                            value={config.platforms[config.target_platform]?.api_key || ''} 
                            onChange={(e) => {
                                const newKey = e.target.value;
                                setConfig({
                                    ...config,
                                    platforms: {
                                        ...config.platforms,
                                        [config.target_platform]: { ...(config.platforms[config.target_platform] || {}), api_key: newKey }
                                    }
                                });
                            }}
                            className="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-slate-200 focus:border-primary text-sm" 
                        />
                    </div>
                    <div className="col-span-1 md:col-span-2 space-y-2">
                        <label className="text-xs font-semibold text-slate-400 uppercase">{t('label_url')}</label>
                        <input type="text" value={config.base_url} onChange={(e) => handleChange('base_url', e.target.value)}
                        className="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-slate-200 focus:border-primary text-sm" />
                    </div>

                    <div className="col-span-1 md:col-span-2">
                        <div className="flex items-center justify-between p-3 border border-slate-700 rounded-lg bg-slate-900/30">
                            <div className="mr-4">
                                <h4 className="text-slate-200 font-medium text-sm">{t('自动补全 OpenAI 规范的 Chat 终点')}</h4>
                                <p className="text-xs text-slate-500">{t('如果开启，当 URL 不以 /chat/completions 结尾时，程序将自动尝试补全它')}</p>
                            </div>
                            <button
                                onClick={() => {
                                    const newVal = !config.platforms[config.target_platform]?.auto_complete;
                                    setConfig({
                                        ...config,
                                        platforms: {
                                            ...config.platforms,
                                            [config.target_platform]: { ...(config.platforms[config.target_platform] || {}), auto_complete: newVal }
                                        }
                                    });
                                }}
                                className={`transition-all duration-300 ${config.platforms[config.target_platform]?.auto_complete ? getThemeColorClass() : 'text-slate-600'}`}
                            >
                                {config.platforms[config.target_platform]?.auto_complete ? <ToggleRight size={32} /> : <ToggleLeft size={32} />}
                            </button>
                        </div>
                    </div>
                </div>

                {/* Thinking Features - Always show */}
                {true && (
                    <div className="space-y-4 border-t border-slate-700 pt-4">
                        <h4 className="text-slate-300 font-semibold flex items-center gap-2">
                            <Sparkles size={16} className="text-purple-400" />
                            {t('menu_api_think_switch')}
                        </h4>
                        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                            <div className="space-y-2">
                                <label className="text-xs font-semibold text-slate-400 uppercase">
                                    {t('menu_api_think_switch')}
                                </label>
                                <button
                                    onClick={() => {
                                        const newThinkSwitch = !config.think_switch;
                                        setConfig({
                                            ...config,
                                            think_switch: newThinkSwitch,
                                            platforms: {
                                                ...config.platforms,
                                                [config.target_platform]: {
                                                    ...(config.platforms[config.target_platform] || {}),
                                                    think_switch: newThinkSwitch
                                                }
                                            }
                                        });
                                    }}
                                    className={`flex items-center gap-2 px-3 py-2 rounded-lg transition-all text-sm font-medium ${
                                        config.think_switch
                                            ? 'bg-green-500/20 text-green-400 border border-green-500/30'
                                            : 'bg-slate-800 text-slate-400 border border-slate-700'
                                    }`}
                                >
                                    {config.think_switch ? <ToggleRight size={16} /> : <ToggleLeft size={16} />}
                                    {config.think_switch ? 'ON' : 'OFF'}
                                </button>
                            </div>

                            {config.think_switch && (
                                <>
                                    <div className="space-y-2">
                                        <label className="text-xs font-semibold text-slate-400 uppercase">
                                            {t('menu_api_think_depth')}
                                        </label>
                                        {isAnthropicThinking || isDeepSeekThinking || isOpenAIThinking ? (
                                            <select
                                                value={config.think_depth || 'low'}
                                                onChange={(e) => {
                                                    const newDepth = e.target.value;
                                                    setConfig({
                                                        ...config,
                                                        think_depth: newDepth,
                                                        platforms: {
                                                            ...config.platforms,
                                                            [config.target_platform]: {
                                                                ...(config.platforms[config.target_platform] || {}),
                                                                think_depth: newDepth
                                                            }
                                                        }
                                                    });
                                                }}
                                                className="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-slate-200 focus:border-primary text-sm"
                                            >
                                                {!isAnthropicThinking && <option value="minimal">Minimal</option>}
                                                <option value="low">Low</option>
                                                <option value="medium">Medium</option>
                                                <option value="high">High</option>
                                                {!isAnthropicThinking && <option value="xhigh">XHigh</option>}
                                                {isDeepSeekThinking && <option value="max">Max</option>}
                                            </select>
                                        ) : (
                                            <input
                                                type="number"
                                                value={config.think_depth || 0}
                                                onChange={(e) => {
                                                    const newDepth = parseInt(e.target.value);
                                                    setConfig({
                                                        ...config,
                                                        think_depth: newDepth,
                                                        platforms: {
                                                            ...config.platforms,
                                                            [config.target_platform]: {
                                                                ...(config.platforms[config.target_platform] || {}),
                                                                think_depth: newDepth
                                                            }
                                                        }
                                                    });
                                                }}
                                                className="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-slate-200 focus:border-primary text-sm"
                                                min="0"
                                                max="10000"
                                            />
                                        )}
                                    </div>

                                    <div className="space-y-2">
                                        <label className="text-xs font-semibold text-slate-400 uppercase">
                                            {t('menu_api_think_budget')}
                                        </label>
                                        <input
                                            type="number"
                                            value={config.thinking_budget ?? ''}
                                            onChange={(e) => {
                                                const val = e.target.value;
                                                const newBudget = val === '' ? undefined : parseInt(val);
                                                setConfig({
                                                    ...config,
                                                    thinking_budget: newBudget,
                                                    platforms: {
                                                        ...config.platforms,
                                                        [config.target_platform]: {
                                                            ...(config.platforms[config.target_platform] || {}),
                                                            thinking_budget: newBudget
                                                        }
                                                    }
                                                });
                                            }}
                                            className="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-slate-200 focus:border-primary text-sm"
                                            min="-1"
                                            placeholder="4096"
                                        />
                                        <span className="text-[10px] text-slate-500 block italic">{t('hint_think_budget')}</span>
                                    </div>
                                </>
                            )}
                        </div>

                        {/* Thinking Mode Warning */}
                        {config.think_switch && (
                            <div className="bg-red-900/30 border border-red-500/30 rounded-lg p-3 mt-4">
                                <div className="flex items-start gap-2">
                                    <AlertTriangle size={16} className="text-red-400 mt-0.5" />
                                    <p className="text-red-300 text-sm">
                                        {['sakura', 'localllm', 'murasaki'].includes(config.target_platform?.toLowerCase() || '')
                                            ? t('warning_thinking_online_only')
                                            : t('warning_thinking_compatibility')
                                        }
                                    </p>
                                </div>
                            </div>
                        )}
                    </div>
                )}
            </div>

            <div className="pt-4 border-t border-slate-800">
               <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 mb-4">
                 <h3 className="text-white font-semibold flex items-center gap-2">
                   <RefreshCw size={16} className="text-accent" /> {t('menu_api_pool_settings')}
                 </h3>
                 <Toggle field="enable_api_failover" label={t('setting_enable_api_failover')} />
               </div>
               
               {config.enable_api_failover && (
                 <div className="space-y-4 bg-slate-900/30 p-4 rounded-lg border border-slate-800">
                   <div className="space-y-2">
                      <label className="text-xs font-semibold text-slate-400 uppercase">{t('setting_api_failover_threshold')}</label>
                      <input type="number" value={config.api_failover_threshold} onChange={(e) => handleChange('api_failover_threshold', parseInt(e.target.value))}
                        className="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-slate-200 focus:border-primary text-sm" />
                   </div>
                   
                   <div className="bg-slate-900 rounded-lg p-3 md:p-4 border border-slate-800">
                     <div className="flex gap-2 mb-4">
                        <select 
                          value={newPoolItem} 
                          onChange={(e) => setNewPoolItem(e.target.value)}
                          className="flex-1 bg-slate-800 border border-slate-700 rounded text-sm p-2 text-white"
                        >
                          <option value="">{t('prompt_add_to_pool')}...</option>
                          {Object.keys(config.platforms)
                             .filter(p => !config.backup_apis.includes(p) && p !== config.target_platform)
                             .map(p => (
                            <option key={p} value={p}>{p}</option>
                          ))}
                        </select>
                        <button onClick={handlePoolAdd} className="bg-primary hover:bg-cyan-400 text-slate-900 px-3 rounded text-sm font-bold">
                          <Plus size={16} />
                        </button>
                     </div>
                     <ul className="space-y-2">
                       {config.backup_apis.map((api, idx) => (
                         <li key={idx} className="flex justify-between items-center text-sm bg-slate-900 p-2 rounded border border-slate-700">
                           <span className="text-slate-300">{api}</span>
                           <button onClick={() => handlePoolRemove(api)} className="text-red-400 hover:text-red-300"><Trash2 size={14} /></button>
                         </li>
                       ))}
                       {config.backup_apis.length === 0 && <p className="text-slate-500 text-xs italic p-2">{t('msg_api_pool_empty')}</p>}
                     </ul>
                   </div>
                 </div>
               )}
            </div>
          </div>
        )}

        {/* --- PROJECT TAB --- */}
        {activeTab === 'project' && (
          <div className="space-y-6">
            <div className="space-y-2">
               <label className="text-xs font-semibold text-slate-400 uppercase">{t('setting_project_type')}</label>
               <select value={config.translation_project} onChange={(e) => handleChange('translation_project', e.target.value)}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-slate-200 focus:border-primary text-sm">
                  {PROJECT_TYPES.map(p => <option key={p} value={p}>{p}</option>)}
               </select>
            </div>

            <div className="p-4 border border-slate-700 rounded-lg bg-slate-900/30">
              <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div>
                  <h4 className="text-slate-200 font-medium text-sm">{t('manga_settings_visual_runtime')}</h4>
                  <p className="mt-1 text-xs text-slate-500">{t('manga_settings_visual_models_hint')}</p>
                </div>
                <button
                  type="button"
                  onClick={() => { void handleDownloadAllMangaModels(); }}
                  disabled={Boolean(mangaModelJob?.status === 'running') || Boolean(mangaModelManager?.available)}
                  className="inline-flex items-center justify-center gap-2 rounded-lg border border-cyan-500/30 bg-cyan-500/10 px-3 py-2 text-xs font-bold text-cyan-200 transition-colors hover:border-cyan-300 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {mangaModelJob?.status === 'running' ? <Loader2 size={14} className="animate-spin" /> : <DownloadCloud size={14} />}
                  {mangaModelManager?.available ? t('manga_model_all_ready_short') : t('manga_model_download_all')}
                </button>
              </div>
              {mangaModelJob?.status === 'running' && (
                <div className="mb-4 rounded-lg border border-cyan-500/20 bg-cyan-500/5 p-3 text-xs text-cyan-200">
                  <div className="flex items-center justify-between gap-2">
                    <span>{mangaModelJob.message || t('manga_preparing')}</span>
                    <span>{Math.max(0, Math.min(100, Number(mangaModelJob.progress || 0)))}%</span>
                  </div>
                  <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-950">
                    <div className="h-full bg-cyan-300" style={{ width: `${Math.max(0, Math.min(100, Number(mangaModelJob.progress || 0)))}%` }} />
                  </div>
                </div>
              )}
              {mangaModelError && (
                <div className="mb-4 rounded-lg border border-rose-500/20 bg-rose-500/5 p-3 text-xs text-rose-200">
                  {mangaModelError}
                </div>
              )}
              <div className="mb-5 grid grid-cols-1 md:grid-cols-2 gap-4">
                <MangaEngineSelect stage="detect" field="manga_detect_engine" />
                <MangaEngineSelect stage="segment" field="manga_segment_engine" />
                <MangaEngineSelect stage="ocr" field="manga_ocr_engine" />
                <MangaEngineSelect stage="inpaint" field="manga_inpaint_engine" />
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <DeviceSelect field="manga_runtime_device" label={t('manga_settings_runtime_device')} />
                <DeviceSelect field="manga_detect_device" label={t('manga_settings_detect_device')} />
                <DeviceSelect field="manga_ocr_device" label={t('manga_settings_ocr_device')} />
                <DeviceSelect field="manga_inpaint_device" label={t('manga_settings_inpaint_device')} />
              </div>
              {forcedCuda && (
                <div className="mt-4 rounded-lg border border-amber-500/20 bg-amber-500/5 p-3 text-xs text-amber-300">
                  {t('manga_settings_cuda_runtime_hint')}
                </div>
              )}
            </div>

             <div className="p-4 border border-slate-700 rounded-lg bg-slate-900/30">
               <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 mb-4">
                 <div>
                   <h4 className="text-slate-200 font-medium text-sm">{t('setting_limit_mode')}</h4>
                   <p className="text-[10px] text-slate-500">{t('menu_trans_mode_select')}</p>
                 </div>
                 <div className="flex bg-slate-900 rounded-lg p-1 border border-slate-700 w-full sm:w-auto">
                   <button onClick={() => handleChange('tokens_limit_switch', false)}
                      className={`flex-1 sm:flex-none px-4 py-1 text-xs rounded-md transition-all ${!config.tokens_limit_switch ? 'bg-primary text-slate-900 font-bold shadow' : 'text-slate-400'}`}>
                     Line
                   </button>
                   <button onClick={() => handleChange('tokens_limit_switch', true)}
                      className={`flex-1 sm:flex-none px-4 py-1 text-xs rounded-md transition-all ${config.tokens_limit_switch ? 'bg-primary text-slate-900 font-bold shadow' : 'text-slate-400'}`}>
                     Token
                   </button>
                 </div>
               </div>
               
               {!config.tokens_limit_switch ? (
                 <div className="space-y-2">
                   <label className="text-xs font-semibold text-slate-400 uppercase">{t('prompt_limit_val')} (Lines)</label>
                   <input type="number" min={1} max={100} value={config.lines_limit} onChange={(e) => handleChange('lines_limit', Math.max(1, Math.min(100, parseInt(e.target.value) || 1)))}
                     className="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-slate-200 focus:border-primary text-sm" />
                 </div>
               ) : (
                 <div className="space-y-2">
                   <label className="text-xs font-semibold text-slate-400 uppercase">{t('prompt_limit_val')} (Tokens)</label>
                   <div className="flex items-center gap-2 text-yellow-500 mb-1">
                     <AlertTriangle size={14} />
                     <span className="text-[10px] font-bold uppercase">{t('warn_token_mode_severe')}</span>
                   </div>
                   <input type="number" min={400} max={16000} step={100} value={config.tokens_limit ?? 1500} onChange={(e) => handleChange('tokens_limit', Math.max(400, Math.min(16000, parseInt(e.target.value) || 400)))}
                     className="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-slate-200 focus:border-primary text-sm" />
                 </div>
               )}

               <div className="space-y-2 mt-4 pt-4 border-t border-slate-700">
                  <label className="text-xs font-semibold text-slate-400 uppercase">{t('setting_pre_lines') || 'Context Lines (Pre-lines)'}</label>
                  <p className="text-[10px] text-slate-500 mb-1">{t('desc_pre_lines') || 'Number of previous translated lines to include as context for AI.'}</p>
                  <input type="number" value={config.pre_line_counts ?? 3} onChange={(e) => handleChange('pre_line_counts', parseInt(e.target.value) || 0)}
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-slate-200 focus:border-primary text-sm" />
               </div>

               <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4 pt-4 border-t border-slate-700">
                 <div className="space-y-2">
                   <label className="text-xs font-semibold text-slate-400 uppercase">{t('setting_chunk_soft_limit_extra_lines')}</label>
                   <p className="text-[10px] text-slate-500 mb-1">{t('setting_chunk_soft_limit_extra_lines_desc')}</p>
                   <input
                     type="number"
                     min={0}
                     max={50}
                     value={config.chunk_soft_limit_extra_lines ?? 10}
                     onChange={(e) => handleChange('chunk_soft_limit_extra_lines', parseInt(e.target.value) || 0)}
                     className="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-slate-200 focus:border-primary text-sm"
                   />
                 </div>
                 {showLineSplitOptimization && (
                   <div className="space-y-2">
                     <label className="text-xs font-semibold text-slate-400 uppercase">{t('setting_line_split_optimization_mode')}</label>
                     <p className="text-[10px] text-slate-500 mb-1">
                       {lineSplitOptimizationMode === 'tail'
                         ? t('setting_line_split_optimization_mode_tail_desc')
                         : t('setting_line_split_optimization_mode_desc')}
                     </p>
                     <div className="grid grid-cols-3 gap-1 bg-slate-900 rounded-lg p-1 border border-slate-700">
                       {(['off', 'dynamic', 'tail'] as const).map((mode) => (
                         <button
                           key={mode}
                           type="button"
                           onClick={() => handleChange('line_split_optimization_mode', mode)}
                           className={`px-2 py-2 text-xs rounded-md transition-all ${lineSplitOptimizationMode === mode ? 'bg-primary text-slate-900 font-bold shadow' : 'text-slate-400 hover:text-slate-200'}`}
                         >
                           {t(`choice_${mode}`)}
                         </button>
                       ))}
                     </div>
                   </div>
                 )}
                 <div className="space-y-2">
                   <label className="text-xs font-semibold text-slate-400 uppercase">{t('setting_retry_split_min_lines')}</label>
                   <p className="text-[10px] text-slate-500 mb-1">{t('setting_retry_split_min_lines_desc')}</p>
                   <input
                     type="number"
                     min={1}
                     max={100}
                     value={config.retry_split_min_lines ?? 15}
                     onChange={(e) => handleChange('retry_split_min_lines', parseInt(e.target.value) || 15)}
                     className="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-slate-200 focus:border-primary text-sm"
                   />
                 </div>
               </div>

               {isConsistencyEnabled && !config.tokens_limit_switch && (
                 <div className="mt-4 rounded-lg border border-amber-500/20 bg-amber-500/5 p-3">
                   <p className="text-[11px] text-amber-300">{t('ui_consistency_lines_recommend')}</p>
                 </div>
               )}
             </div>
          </div>
        )}

        {/* --- FEATURES TAB --- */}
        {activeTab === 'features' && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 md:gap-4">
            <Toggle field="pre_translation_switch" label={t('feature_pre_translation_switch')} />
            <Toggle field="post_translation_switch" label={t('feature_post_translation_switch')} />
            <Toggle field="prompt_dictionary_switch" label={t('feature_prompt_dictionary_switch')} />
            <Toggle field="exclusion_list_switch" label={t('feature_exclusion_list_switch')} />
            <Toggle field="characterization_switch" label={t('feature_characterization_switch')} />
            <Toggle
              field="character_recall_switch"
              label={t('feature_character_recall_switch')}
              desc={t('feature_character_recall_switch_desc')}
            />
            {config.character_recall_switch && (
              <>
                <div className="p-3 rounded-lg border border-slate-700 bg-slate-900/40 space-y-2">
                  <label className="text-xs font-semibold text-slate-400 uppercase">{t('setting_character_recall_context_lines')}</label>
                  <p className="text-[10px] text-slate-500">{t('setting_character_recall_context_lines_desc')}</p>
                  <input type="number" min={0} max={200} value={config.character_recall_context_lines ?? 50} onChange={(e) => handleChange('character_recall_context_lines', parseInt(e.target.value) || 0)}
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-slate-200 focus:border-primary text-sm" />
                </div>
                <div className="p-3 rounded-lg border border-slate-700 bg-slate-900/40 space-y-2">
                  <label className="text-xs font-semibold text-slate-400 uppercase">{t('setting_character_recall_lookahead_lines')}</label>
                  <p className="text-[10px] text-slate-500">{t('setting_character_recall_lookahead_lines_desc')}</p>
                  <input type="number" min={0} max={50} value={config.character_recall_lookahead_lines ?? 10} onChange={(e) => handleChange('character_recall_lookahead_lines', parseInt(e.target.value) || 0)}
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-slate-200 focus:border-primary text-sm" />
                </div>
              </>
            )}
            <Toggle field="world_building_switch" label={t('feature_world_building_switch')} />
            <Toggle field="writing_style_switch" label={t('feature_writing_style_switch')} />
            <Toggle field="translation_example_switch" label={t('feature_translation_example_switch')} />
            <Toggle field="few_shot_and_example_switch" label={t('feature_few_shot_and_example_switch')} />
            <Toggle field="auto_process_text_code_segment" label={t('feature_auto_process_text_code_segment')} />
            <Toggle
              field="translation_consistency_enhancement"
              label={t('setting_translation_consistency_enhancement')}
              desc={t('setting_translation_consistency_enhancement_desc')}
            />

            {/* AI Proofread Settings */}
            <div className="col-span-2 border-t border-slate-700 pt-4 mt-2">
              <h3 className="text-sm font-semibold text-slate-300 mb-3">{t('feature_proofread_section')}</h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <Toggle field="enable_auto_proofread" label={t('feature_enable_auto_proofread')} />
              </div>
            </div>
          </div>
        )}

        {/* --- SYSTEM TAB --- */}
        {activeTab === 'system' && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 md:gap-4">
             <Toggle field="show_detailed_logs" label={t('setting_detailed_logs')} />
             <Toggle field="enable_cache_backup" label={t('setting_cache_backup')} />
             <Toggle field="enable_auto_restore_ebook" label={t('setting_auto_restore_ebook')} />
             <Toggle field="enable_xlsx_conversion" label={t('setting_enable_xlsx_conversion')} />
             <Toggle field="enable_dry_run" label={t('setting_dry_run')} />
             <Toggle field="enable_smart_round_limit" label={t('setting_enable_smart_round_limit')} />
             <Toggle field="enable_rate_limit" label={t('setting_enable_rate_limit')} desc={t('setting_enable_rate_limit_desc')} />
             <Toggle field="response_conversion_toggle" label={t('setting_response_conversion_toggle')} />
             <Toggle field="enable_context_enhancement" label={t('setting_enable_context_enhancement')} desc={t('setting_enable_context_enhancement_desc')} />

             <div className="space-y-2">
                <label className="text-xs font-semibold text-slate-400 uppercase">{t('setting_cache_save_interval')}</label>
                <input
                  type="number"
                  min={1}
                  max={3600}
                  value={config.cache_save_interval ?? 8}
                  onChange={(e) => handleChange('cache_save_interval', Math.min(Math.max(parseInt(e.target.value) || 8, 1), 3600))}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-slate-200 focus:border-primary text-sm"
                />
                <p className="text-xs text-slate-500">{t('setting_cache_save_interval_desc')}</p>
             </div>

             {config.enable_rate_limit && (
               <div className="col-span-1 md:col-span-2 grid grid-cols-1 md:grid-cols-2 gap-4 p-4 bg-slate-900/30 border border-slate-700 rounded-lg">
                 <div className="space-y-2">
                    <label className="text-xs font-semibold text-slate-400 uppercase">{t('setting_custom_rpm_limit')}</label>
                    <input type="number" value={config.custom_rpm_limit ?? 0} onChange={(e) => handleChange('custom_rpm_limit', parseInt(e.target.value) || 0)}
                      placeholder="0 = use platform default"
                      className="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-slate-200 focus:border-primary text-sm" />
                    <p className="text-xs text-slate-500">{t('setting_custom_rpm_limit_desc')}</p>
                 </div>
                 <div className="space-y-2">
                    <label className="text-xs font-semibold text-slate-400 uppercase">{t('setting_custom_tpm_limit')}</label>
                    <input type="number" value={config.custom_tpm_limit ?? 0} onChange={(e) => handleChange('custom_tpm_limit', parseInt(e.target.value) || 0)}
                      placeholder="0 = use platform default"
                      className="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-slate-200 focus:border-primary text-sm" />
                    <p className="text-xs text-slate-500">{t('setting_custom_tpm_limit_desc')}</p>
                 </div>
               </div>
             )}

             <div className="space-y-2">
                <label className="text-xs font-semibold text-slate-400 uppercase">{t('setting_temp_file_limit')}</label>
                <input type="number" value={config.temp_file_limit ?? 10} onChange={(e) => handleChange('temp_file_limit', parseInt(e.target.value) || 0)}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-slate-200 focus:border-primary text-sm" />
             </div>

             <div className="space-y-2">
                <label className="text-xs font-semibold text-slate-400 uppercase">Cache Editor Page Size</label>
                <input type="number" value={config.cache_editor_page_size ?? 15} onChange={(e) => handleChange('cache_editor_page_size', parseInt(e.target.value) || 15)}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-slate-200 focus:border-primary text-sm" />
             </div>

             <div className="col-span-1 md:col-span-2 p-4 bg-slate-900/50 border border-slate-700 rounded-lg space-y-4">
                <div className="flex justify-between items-center">
                    <div className="flex items-center gap-2">
                        <FileJson size={18} className="text-primary" />
                        <span className="text-sm font-semibold text-slate-200">{t('ui_task_temp_uploaded')} ({tempFiles.length})</span>
                    </div>
                    <div className="flex items-center gap-2">
                        {selectedTempFiles.length > 0 && (
                            <button 
                                onClick={handleDeleteTempFiles}
                                disabled={isDeletingFiles}
                                className="flex items-center gap-1 text-[10px] bg-red-500/10 text-red-400 border border-red-500/20 px-2 py-1 rounded hover:bg-red-500/20 transition-all"
                            >
                                <Trash2 size={12} /> {t('menu_profile_delete')} ({selectedTempFiles.length})
                            </button>
                        )}
                        <button 
                            onClick={() => setIsTempFilesExpanded(!isTempFilesExpanded)}
                            className={`p-1 text-slate-400 hover:text-white transition-transform ${isTempFilesExpanded ? 'rotate-180' : ''}`}
                        >
                            <ChevronDown size={18} />
                        </button>
                    </div>
                </div>

                {isTempFilesExpanded && (
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 animate-in fade-in slide-in-from-top-2 duration-200">
                        {tempFiles.length === 0 ? (
                            <p className="col-span-full text-center text-xs text-slate-500 py-4 italic">No files found</p>
                        ) : (
                            tempFiles.map(f => (
                                <label key={f.name} className="flex items-center gap-3 p-2 bg-slate-950/50 rounded border border-slate-800 hover:border-slate-700 cursor-pointer group">
                                    <input 
                                        type="checkbox" 
                                        checked={selectedTempFiles.includes(f.name)}
                                        onChange={() => handleToggleTempFile(f.name)}
                                        className="w-4 h-4 rounded border-slate-700 text-primary focus:ring-primary bg-slate-900"
                                    />
                                    <div className="flex-1 min-w-0">
                                        <p className="text-xs text-slate-300 truncate" title={f.name}>{f.name}</p>
                                        <p className="text-[10px] text-slate-500">{(f.size/1024).toFixed(1)} KB</p>
                                    </div>
                                </label>
                            ))
                        )}
                    </div>
                )}
             </div>

             <div className="col-span-1 md:col-span-2 mt-4">
                 <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between border-b border-slate-700 pb-2 mb-3 gap-2">
                    <h4 className="text-slate-300 font-bold text-sm md:text-base">{t('menu_response_checks')}</h4>
                    <div className={`flex items-center gap-2 px-3 py-1 rounded-full text-[10px] font-bold border transition-colors duration-500 ${
                        isOnline 
                            ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20 shadow-[0_0_10px_rgba(16,185,129,0.2)]' 
                            : 'bg-red-500/10 text-red-500 border-red-500/20 shadow-[0_0_10px_rgba(239,68,68,0.2)] animate-pulse'
                    }`}>
                        {isOnline ? <Wifi size={12} /> : <WifiOff size={12} />}
                        {isOnline ? t('ui_system_online') : "SYSTEM OFFLINE"}
                    </div>
                 </div>
                 
                 <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 md:gap-4">
                     {typeof config.response_check_switch === 'object' && config.response_check_switch !== null && Object.entries(config.response_check_switch).map(([key, val]) => (
                         <div key={key} className="flex items-center justify-between p-3 bg-slate-900/50 rounded border border-slate-800/50">
                             <div className="flex items-center gap-2">
                                <AlertTriangle size={14} className="text-yellow-500 cursor-help" onClick={handleWarningClick} />
                                <span className="text-xs text-slate-300">{t(`check_${key}`) || key}</span>
                             </div>
                             <button onClick={() => handleCheckChange(key, !(val as boolean))} className={`transition-all duration-300 ${val ? getThemeColorClass() + ' scale-110' : 'text-slate-600'}`}>
                                 {val ? <ToggleRight size={24} /> : <ToggleLeft size={24} />}
                             </button>
                         </div>
                     ))}
                 </div>
             </div>

             <div className="col-span-1 md:col-span-2 mt-4 pt-4 border-t border-slate-700">
                <h4 className="mb-3 text-sm md:text-base font-bold text-slate-300">{t('label_category_project_general')}</h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3 md:gap-4">
                    <div className="p-4 bg-slate-900/50 border border-slate-700 rounded-lg flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
                        <div className="flex items-center gap-2">
                            <Globe size={18} className="text-primary" />
                            <span className="text-sm font-semibold text-slate-200">{t('ui_language')}</span>
                        </div>
                        <div className="flex flex-wrap gap-2 w-full sm:w-auto">
                            {availableLanguages.map(lang => (
                                <button
                                    key={lang.code}
                                    onClick={() => {
                                        setLanguage(lang.code);
                                        handleChange('interface_language', lang.code);
                                    }}
                                    className={`flex-1 sm:flex-none px-3 py-1.5 rounded text-xs font-bold transition-all ${
                                        language === lang.code
                                            ? 'bg-primary text-slate-900 shadow-md shadow-cyan-500/20'
                                            : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                    }`}
                                >
                                    {lang.label}
                                </button>
                            ))}
                        </div>
                    </div>
                    <Toggle
                      field="enable_github_promotion"
                      label={t('setting_github_promotion')}
                      desc={t('setting_github_promotion_desc')}
                    />
                </div>
             </div>
          </div>
        )}

        {/* --- PROFILES TAB --- */}
        {activeTab === 'profiles' && (
          <div className="space-y-6">
            <div className="flex justify-between items-center mb-4">
                <h2 className="text-lg md:text-xl font-semibold text-slate-200">{t('menu_profiles')}</h2>
                <div className="flex gap-2">
                    <button 
                      onClick={startCreate} 
                      disabled={isCreating}
                      className="flex items-center gap-2 px-3 py-2 bg-primary/10 border border-primary/20 text-primary rounded-lg hover:bg-primary/20 transition-colors disabled:opacity-50"
                    >
                        <Plus size={16} /> <span className="text-xs font-bold">{t('menu_profile_create')}</span>
                    </button>
                    <button 
                      onClick={loadProfiles} 
                      className="p-2 bg-slate-800 rounded-lg hover:bg-slate-700 text-slate-400 hover:text-white transition-colors"
                      title="Reload Profiles"
                    >
                        <RefreshCw size={18} className={loadingProfiles ? "animate-spin" : ""} />
                    </button>
                </div>
            </div>
            
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                {/* Create Profile Card */}
                {isCreating && (
                    <div className="p-5 rounded-xl border border-primary/50 bg-slate-900/80 shadow-lg shadow-primary/10 flex flex-col gap-3 animate-in fade-in zoom-in-95 duration-200">
                        <h3 className="text-sm font-bold text-slate-300">New Profile Name</h3>
                        <input 
                            autoFocus
                            type="text" 
                            className="bg-slate-950 border border-slate-700 rounded p-2 text-white text-sm focus:border-primary outline-none"
                            placeholder="Enter name..."
                            value={tempName}
                            onChange={(e) => setTempName(e.target.value)}
                            onKeyDown={(e) => e.key === 'Enter' && confirmCreate()}
                        />
                         {actionError && <p className="text-red-400 text-xs">{actionError}</p>}
                        <div className="flex justify-end gap-2 mt-2">
                            <button onClick={cancelCreate} className="p-1.5 rounded bg-slate-800 text-slate-400 hover:text-white"><X size={16}/></button>
                            <button onClick={confirmCreate} className="p-1.5 rounded bg-primary text-slate-900 hover:bg-cyan-400"><Check size={16}/></button>
                        </div>
                    </div>
                )}

                {profiles.map((profileName) => {
                    const isSelected = config?.active_profile === profileName;
                    const isRenaming = renamingId === profileName;
                    
                    return (
                        <div 
                            key={profileName} 
                            onClick={() => !isRenaming && handleSwitchProfile(profileName)}
                            className={`group relative p-4 md:p-5 rounded-xl border cursor-pointer transition-all duration-300 ${
                                isSelected
                                    ? 'bg-primary/10 border-primary ring-1 ring-primary shadow-[0_0_20px_rgba(6,182,212,0.3)] scale-[1.02]' 
                                    : 'bg-slate-900/50 border-slate-700 hover:border-slate-500 hover:bg-slate-800'
                            }`}
                        >
                            <div className="flex items-start justify-between">
                                <div className="flex items-center gap-3 w-full">
                                    <div className={`p-2 rounded-lg shrink-0 ${isSelected ? 'bg-primary/20 text-primary' : 'bg-slate-800 text-slate-400 group-hover:bg-slate-700 group-hover:text-slate-200'}`}>
                                        <FileJson size={20} md:size={24} />
                                    </div>
                                    <div className="flex-1 min-w-0">
                                        {isRenaming ? (
                                            <div className="flex items-center gap-2" onClick={e => e.stopPropagation()}>
                                                <input 
                                                    autoFocus
                                                    className="w-full bg-slate-950 border border-slate-600 rounded px-1 py-0.5 text-white text-sm"
                                                    value={tempName}
                                                    onChange={(e) => setTempName(e.target.value)}
                                                    onKeyDown={(e) => e.key === 'Enter' && confirmRename(profileName)}
                                                />
                                                <button onClick={cancelRename} className="text-red-400 hover:text-red-300"><X size={14}/></button>
                                                <button onClick={() => confirmRename(profileName)} className="text-green-400 hover:text-green-300"><Check size={14}/></button>
                                            </div>
                                        ) : (
                                            <>
                                                <h3 className={`text-sm md:text-base font-semibold truncate ${isSelected ? 'text-primary' : 'text-slate-200 group-hover:text-white'}`}>{profileName}</h3>
                                                <p className="text-[10px] text-slate-500 uppercase tracking-tighter">JSON Config</p>
                                            </>
                                        )}
                                        {actionError && isRenaming && <p className="text-red-400 text-xs mt-1">{actionError}</p>}
                                    </div>
                                </div>
                                
                                <div className="flex flex-col items-end gap-2 ml-2">
                                    {/* Theme Selector for Unlocked Themes */}
                                    {isSelected && unlockedThemes.length > 1 && (
                                        <div className="relative group/theme" onClick={e => e.stopPropagation()}>
                                            <select 
                                                value={activeTheme}
                                                onChange={(e) => triggerRipple(window.innerWidth / 2, window.innerHeight / 2, e.target.value as any)}
                                                className={`text-[10px] font-bold py-1 pl-2 pr-6 rounded border appearance-none cursor-pointer transition-all ${
                                                    elysiaActive 
                                                        ? 'bg-pink-500/10 border-pink-500/30 text-pink-400 hover:bg-pink-500/20' 
                                                        : 'bg-slate-800 border-slate-700 text-slate-400 hover:text-white'
                                                }`}
                                            >
                                                {unlockedThemes.map(tId => (
                                                    <option key={tId} value={tId}>{getCharacterName(tId)}</option>
                                                ))}
                                            </select>
                                            <ChevronDown size={10} className="absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none text-slate-500" />
                                        </div>
                                    )}
                                    
                                    {profileName.toLowerCase() === 'elysia' && !unlockedThemes.includes('elysia') && (
                                        <button 
                                            onClick={(e) => {
                                                e.stopPropagation();
                                                unlockThemeWithNotification('elysia');
                                            }}
                                            className="p-1.5 rounded-lg bg-slate-800 text-slate-500 hover:text-pink-400 transition-all"
                                            title="Magic Toggle"
                                        >
                                            <Sparkles size={16} />
                                        </button>
                                    )}
                                    {switchingProfile === profileName ? (
                                        <Loader2 size={18} className="text-primary animate-spin" />
                                    ) : isSelected && (
                                        <CheckCircle size={18} className="text-primary" />
                                    )}
                                </div>
                            </div>
                            
                            {/* Actions Toolbar */}
                            {!isRenaming && (
                                <div className="mt-4 flex justify-between items-end border-t border-white/5 pt-3">
                                    <div className="flex gap-2 opacity-100 sm:opacity-0 group-hover:opacity-100 transition-opacity">
                                        <button 
                                            onClick={(e) => startRename(profileName, e)}
                                            className="p-1.5 rounded hover:bg-slate-700 text-slate-400 hover:text-cyan-400 transition-colors" 
                                            title="Rename"
                                        >
                                            <Edit2 size={14} />
                                        </button>
                                        <button 
                                            onClick={(e) => handleDelete(profileName, e)}
                                            className="p-1.5 rounded hover:bg-slate-700 text-slate-400 hover:text-red-400 transition-colors"
                                            title="Delete"
                                            disabled={isSelected} // Cannot delete active
                                        >
                                            <Trash2 size={14} />
                                        </button>
                                    </div>
                                    <span className={`text-[10px] font-bold uppercase tracking-widest transition-colors ${isSelected ? 'text-primary' : 'text-slate-500 group-hover:text-primary'}`}>
                                        {switchingProfile === profileName ? 'Loading' : isSelected ? 'Active' : 'Load'}
                                    </span>
                                </div>
                            )}
                        </div>
                    );
                })}
                
                {profiles.length === 0 && !loadingProfiles && !isCreating && (
                    <div className="col-span-full py-12 text-center border-2 border-dashed border-slate-800 rounded-xl">
                        <p className="text-slate-500 text-sm">No profiles found in Resource/profiles/</p>
                    </div>
                )}
            </div>
            
            <div className="p-3 md:p-4 bg-yellow-500/10 border border-yellow-500/20 rounded-lg flex gap-3">
                <AlertTriangle size={20} className="text-yellow-500 shrink-0 mt-0.5" />
                <div>
                    <h4 className="text-xs md:text-sm font-bold text-yellow-500 uppercase tracking-tight">Warning</h4>
                    <p className="text-[10px] md:text-xs text-slate-400 mt-1">Switching profiles will overwrite your current unsaved settings. Please save any critical changes first.</p>
                </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
