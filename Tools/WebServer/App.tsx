import React, { useState, useEffect, useRef } from 'react';
import { LayoutDashboard, PlayCircle, Settings as SettingsIcon, Archive, Terminal as TerminalIcon, BookOpen, Puzzle, Menu, X as CloseIcon, Sparkles, ListPlus, Database, Images } from 'lucide-react';
import { Dashboard } from './pages/Dashboard';
import { Settings } from './pages/Settings';
import { Rules } from './pages/Rules';
import { Plugins } from './pages/Plugins';
import { TaskQueue } from './pages/TaskQueue';
import { TaskRunner } from './pages/TaskRunner';
import { Prompts } from './pages/Prompts';
import { Monitor } from './pages/Monitor';
import { CacheEditor } from './pages/CacheEditor';
import { MangaEditor } from './pages/mangaEditor';
import { DataService } from './services/DataService';
import { I18nProvider, useI18n } from './contexts/I18nContext';
import { GlobalProvider, useGlobal } from './contexts/GlobalContext';
import { ThemeManager } from './components/Themes/ThemeManager';
import { ElysiaGuide } from './components/ElysiaGuide';
import { UnlockModal } from './components/UnlockModal';

// Custom hook to track hash based location for navigation
const useLocation = () => {
  const getPathname = () => (window.location.hash.substring(1).split('?')[0] || '/');
  const [pathname, setPathname] = useState(getPathname());

  useEffect(() => {
    const handleHashChange = () => {
      setPathname(getPathname());
    };
    window.addEventListener('hashchange', handleHashChange);
    return () => window.removeEventListener('hashchange', handleHashChange);
  }, []);

  return { pathname };
};

const SidebarItem = ({ to, icon: Icon, labelKey, currentPath, onClick, activeTheme }: any) => {
  const { t } = useI18n();
  const isActive = currentPath === to || (to === '/' && currentPath === '');
  const elysiaActive = activeTheme === 'elysia';

  const handleClick = (e: React.MouseEvent) => {
    e.preventDefault();
    window.location.hash = to;
    if (onClick) onClick();
  };

  const getActiveStyles = () => {
    if (!isActive) {
        if (activeTheme === 'elysia' || activeTheme === 'herrscher_of_human') return 'text-pink-300/70 hover:bg-pink-50/30 hover:text-pink-400';
        if (activeTheme === 'eden') return 'text-amber-300/70 hover:bg-amber-900/30 hover:text-amber-400';
        if (activeTheme === 'mobius') return 'text-green-300/70 hover:bg-green-900/30 hover:text-green-400';
        if (activeTheme === 'pardofelis') return 'text-[#7f5539]/70 hover:bg-[#7f5539]/10 hover:text-[#7f5539]';
        if (activeTheme === 'kalpas') return 'text-red-400/70 hover:bg-red-950/30 hover:text-red-400';
        return 'text-slate-400 hover:bg-slate-800/50 hover:text-slate-200';
    }
    
    switch(activeTheme) {
        case 'elysia': return 'bg-pink-100/50 text-pink-500 border border-pink-200';
        case 'herrscher_of_human': return 'bg-pink-400/20 text-[#ff4d6d] border border-pink-300 shadow-[0_0_15px_rgba(255,143,163,0.3)]';
        case 'eden': return 'bg-amber-500/20 text-amber-500 border border-amber-500/30';
        case 'mobius': return 'bg-green-500/20 text-green-500 border border-green-500/30';
        case 'pardofelis': return 'bg-orange-500/20 text-orange-600 border border-orange-500/30';
        case 'griseo': return 'bg-blue-100 text-blue-500 border border-blue-200';
        case 'kevin': return 'bg-sky-100 text-sky-600 border border-sky-200';
        case 'kalpas': return 'bg-red-500/20 text-red-500 border border-red-500/30';
        case 'aponia': return 'bg-purple-500/10 text-purple-400 border border-purple-500/20';
        case 'villv': return 'bg-orange-500/10 text-orange-600 border border-orange-500/20';
        case 'su': return 'bg-emerald-500/10 text-emerald-500 border border-emerald-500/20';
        case 'sakura': return 'bg-pink-400/10 text-pink-500 border border-pink-400/20';
        case 'kosma': return 'bg-indigo-500/10 text-indigo-400 border border-indigo-500/20';
        case 'hua': return 'bg-orange-500/10 text-orange-500 border border-orange-500/20';
        default: return 'bg-primary/10 text-primary border border-primary/20';
    }
  };

  return (
    <div
      onClick={handleClick}
      className={`cursor-pointer flex items-center gap-3 px-4 py-3 rounded-lg transition-all duration-300 group relative overflow-hidden ${getActiveStyles()}`}
    >
      <div className="relative">
        <Icon size={20} className={`transition-all duration-300 ${isActive ? 'text-current' : 'text-slate-500 group-hover:text-slate-300'}`} />
        {elysiaActive && isActive && <Sparkles size={10} className="absolute -top-1 -right-1 text-pink-400 animate-pulse" />}
      </div>
      <span className="font-medium text-sm">{t(labelKey) || labelKey}</span>
      {isActive && (
          <div className={`absolute right-0 top-0 h-full w-1 ${
              activeTheme === 'elysia' ? 'bg-pink-400 shadow-[0_0_10px_#ff8fa3]' : 
              activeTheme === 'herrscher_of_human' ? 'bg-[#ff4d6d] shadow-[0_0_15px_#ff8fa3]' :
              activeTheme === 'eden' ? 'bg-amber-500' :
              activeTheme === 'mobius' ? 'bg-green-500' :
              activeTheme === 'pardofelis' ? 'bg-orange-500' :
              activeTheme === 'griseo' ? 'bg-blue-400' :
              activeTheme === 'kevin' ? 'bg-sky-400' :
              activeTheme === 'kalpas' ? 'bg-red-500' :
              activeTheme === 'aponia' ? 'bg-purple-400' :
              activeTheme === 'villv' ? 'bg-orange-600' :
              activeTheme === 'su' ? 'bg-emerald-500' :
              activeTheme === 'sakura' ? 'bg-pink-400' :
              activeTheme === 'kosma' ? 'bg-indigo-400' :
              activeTheme === 'hua' ? 'bg-orange-500' :
              'bg-primary'
          }`} />
      )}
    </div>
  );
};
const MainLayout: React.FC = () => {
  const { pathname } = useLocation();
  const { t, language, setLanguage, availableLanguages } = useI18n();
  const { version, config, activeTheme, rippleData, triggerRipple, unlockThemeWithNotification, notification, setNotification } = useGlobal();
  const [systemMode, setSystemMode] = useState<'full' | 'monitor' | 'loading'>('loading');
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const elysiaActive = activeTheme === 'elysia';

  // --- Elysia State ---
  const [elysiaUnlocked, setElysiaUnlocked] = useState(() => {
    return localStorage.getItem('unlocked_themes')?.includes('elysia') || false;
  });

  // Easter Egg States
  const [versionClicks, setVersionClicks] = useState(0);
  const [isVersionMetamorphosed, setIsVersionMetamorphosed] = useState(false);
  const [activeShake, setActiveShake] = useState(false);
  const [isExploding, setIsExploding] = useState(false);

  // Reset easter egg states when switching back to default theme
  useEffect(() => {
    if (activeTheme === 'default') {
      setIsVersionMetamorphosed(false);
      setVersionClicks(0);
      setLogoClicks(0);
    }
  }, [activeTheme]);

  const handleVersionClick = (e: React.MouseEvent) => {
    // Only allow metamorphosis/switching in default theme
    if (activeTheme !== 'default') {
        if (isVersionMetamorphosed) {
            // If already morphed, allow switching back to Elysia only if in Elysia theme (but logic says only default)
            // Let's stick to user request: only default theme allows "normal switching"
            return;
        }
        return;
    }

    if (isVersionMetamorphosed) {
        triggerRipple(e.clientX, e.clientY, 'elysia');
        return;
    }
    const newClicks = versionClicks + 1;
    setVersionClicks(newClicks);
    if (newClicks >= 7) {
      setIsExploding(true);
      setTimeout(() => {
          setIsVersionMetamorphosed(true);
          setIsExploding(false);
      }, 600);
    }
  };

  const [logoClicks, setLogoClicks] = useState(0);
  const [lastLogoClickTime, setLastLogoClickTime] = useState(0);
  const [isShaking, setIsShaking] = useState(false);
  const [showTip, setShowTip] = useState(false);
  const shakeTimeout = useRef<any>(null);

  const handleLogoClick = (e: React.MouseEvent) => {
    const now = Date.now();
    setIsShaking(true);
    if (shakeTimeout.current) clearTimeout(shakeTimeout.current);
    shakeTimeout.current = setTimeout(() => setIsShaking(false), 300);

    // Easter egg triggers ONLY in default theme
    if (activeTheme !== 'default') return;

    const newClicks = logoClicks + 1;
    setLogoClicks(newClicks);
    
    // Mobius Trigger: 10 clicks in 3 seconds
    if (now - lastLogoClickTime < 3000) {
        if (newClicks >= 10) {
            unlockThemeWithNotification('mobius');
            setLogoClicks(0);
        }
    } else {
        setLogoClicks(1);
    }
    setLastLogoClickTime(now);

    // Original Elysia Trigger
    if (newClicks === 7 && !elysiaUnlocked) {
      setShowTip(true);
      setElysiaUnlocked(true);
      triggerRipple(e.clientX, e.clientY, 'elysia');
      setTimeout(() => setShowTip(false), 5000);
    }
  };

  // --- Character Unlocks ---
  
  // Su Trigger: Idle on Dashboard for 60s
  useEffect(() => {
    let idleTimer: any;
    const resetTimer = () => {
        if (idleTimer) clearTimeout(idleTimer);
        if ((pathname === '/' || pathname === '') && activeTheme !== 'su') {
            idleTimer = setTimeout(() => {
                unlockThemeWithNotification('su');
            }, 60000);
        }
    };

    if (pathname === '/' || pathname === '') {
        window.addEventListener('mousemove', resetTimer);
        window.addEventListener('keydown', resetTimer);
        resetTimer();
    }

    return () => {
        window.removeEventListener('mousemove', resetTimer);
        window.removeEventListener('keydown', resetTimer);
        if (idleTimer) clearTimeout(idleTimer);
    };
  }, [pathname, activeTheme]);

  // Kosma Trigger: Night time usage (0-4 AM)
  useEffect(() => {
    const checkTime = () => {
        const hour = new Date().getHours();
        if (hour >= 0 && hour < 4 && activeTheme !== 'kosma') {
            // Trigger once per session if in this time range
            unlockThemeWithNotification('kosma');
        }
    };
    checkTime();
  }, [activeTheme]);

  // Sakura Trigger: Select Sakura platform
  useEffect(() => {
    if (config?.target_platform?.toLowerCase() === 'sakura' && activeTheme !== 'sakura') {
        unlockThemeWithNotification('sakura');
    }
  }, [config?.target_platform, activeTheme]);

  // Vill-V Trigger: Rapid platform switching
  const [platformSwitches, setPlatformSwitches] = useState(0);
  const lastPlatform = useRef<string | null>(null);
  useEffect(() => {
    if (config?.target_platform && lastPlatform.current && config.target_platform !== lastPlatform.current) {
        const newCount = platformSwitches + 1;
        setPlatformSwitches(newCount);
        if (newCount >= 5 && activeTheme !== 'villv') {
            unlockThemeWithNotification('villv');
            setPlatformSwitches(0);
        }
    }
    lastPlatform.current = config?.target_platform || null;
  }, [config?.target_platform, activeTheme, platformSwitches]);

  // Sync elysia_unlocked to localStorage for Settings.tsx visibility
  useEffect(() => {
    if (elysiaUnlocked) {
      localStorage.setItem('elysia_unlocked', 'true');
    } else {
      localStorage.removeItem('elysia_unlocked');
    }
  }, [elysiaUnlocked]);
  useEffect(() => {
    let mounted = true;
    DataService.getSystemMode()
      .then(res => {
        if (mounted) setSystemMode(res.mode || 'full');
      })
      .catch(() => {
        if (mounted) setSystemMode('full');
      });
    return () => { mounted = false; };
  }, []);

  // Close sidebar on path change (mobile)
  useEffect(() => {
    setIsSidebarOpen(false);
  }, [pathname]);

  // Griseo Trigger: Idle on /logs for 30s
  useEffect(() => {
    let idleTimer: any;
    const resetTimer = () => {
        if (idleTimer) clearTimeout(idleTimer);
        if (pathname === '/logs' && activeTheme !== 'griseo') {
            idleTimer = setTimeout(() => {
                unlockThemeWithNotification('griseo');
            }, 30000);
        }
    };

    if (pathname === '/logs') {
        window.addEventListener('mousemove', resetTimer);
        window.addEventListener('keydown', resetTimer);
        resetTimer();
    }

    return () => {
        window.removeEventListener('mousemove', resetTimer);
        window.removeEventListener('keydown', resetTimer);
        if (idleTimer) clearTimeout(idleTimer);
    };
  }, [pathname, activeTheme]);

  // Sync language with config ONLY ON INITIAL LOAD or when config changes significantly
  const isInitialLangSync = useRef(true);
  useEffect(() => {
    if (config?.interface_language && isInitialLangSync.current) {
        if (config.interface_language === 'zh_CN' && language !== 'zh_CN') {
            setLanguage('zh_CN');
        } else if (config.interface_language === 'en' && language !== 'en') {
            setLanguage('en');
        }
        isInitialLangSync.current = false;
    }
  }, [config?.interface_language, language, setLanguage]);

  const handleLanguageChange = (code: string) => {
    setLanguage(code);
    if (config) {
        // Force sync back to config so it persists
        DataService.saveConfig({ ...config, interface_language: code })
          .catch(err => console.error("Failed to persist language change", err));
    }
  };

  if (systemMode === 'loading') {
      return (
        <div className="h-screen bg-background flex items-center justify-center">
          <ThemeManager />
          <div className="flex flex-col items-center gap-4">
            <div className={`w-12 h-12 border-4 rounded-full animate-spin ${elysiaActive ? 'border-pink-500/20 border-t-pink-500 shadow-[0_0_15px_#ff8fa3]' : 'border-primary/20 border-t-primary'}`}></div>
            <span className={`font-mono text-xs uppercase tracking-widest ${elysiaActive ? 'text-pink-500 font-bold' : 'text-slate-500'}`}>
              {elysiaActive ? 'Connecting to Elysium...' : 'Initializing System...'}
            </span>
          </div>
        </div>
      );
    }
  
    const isMonitorMode = systemMode === 'monitor';
    const isMonitorPath = pathname === '/monitor';
    const isWorkbenchPath = pathname === '/monitor' || pathname === '/manga-editor';
  
    let content;
    if (isMonitorMode && !isMonitorPath) {
      content = (
        <div className="flex flex-col items-center justify-center h-full p-4 space-y-6 text-center animate-in fade-in duration-500">
          <div className="w-24 h-24 rounded-full bg-red-500/10 flex items-center justify-center text-red-500">
             <LayoutDashboard size={48} />
          </div>
          <h1 className="text-2xl md:text-4xl font-black text-white uppercase tracking-tighter">监控模式下此页面不可用</h1>
          <p className="text-slate-500 max-w-md">当前系统正处于独立监控模式，所有的交互功能已被禁用。请切换至监控面板查看实时任务进度。</p>
          <button 
            onClick={() => window.location.hash = '/monitor'}
            className="px-8 py-3 bg-primary text-slate-900 font-bold rounded-lg hover:bg-cyan-300 transition-colors shadow-lg shadow-primary/20"
          >
            立即跳转至监控面板
          </button>
        </div>
      );
    } else if (pathname === '/' || pathname === '') {
      content = <Dashboard />;
    } else if (pathname === '/task') {
      content = <TaskRunner />;
    } else if (pathname === '/cache-editor') {
      content = <CacheEditor />;
    } else if (pathname === '/prompts') {
      content = <Prompts />;
    } else if (pathname === '/rules') {
      content = <Rules />;
    } else if (pathname === '/plugins') {
      content = <Plugins />;
    } else if (pathname === '/queue') {
      content = <TaskQueue />;
    } else if (pathname === '/settings') {
      content = <Settings />;
    } else if (pathname === '/monitor') {
      content = <Monitor />;
    } else if (pathname === '/manga-editor') {
      content = <MangaEditor />;
    } else if (pathname === '/export') {
      content = <div className="text-slate-500 text-center mt-20">{t('menu_export_only')} (Placeholder)</div>;
    } else if (pathname === '/logs') {
      content = <div className="text-slate-500 text-center mt-20">System Logs (Placeholder)</div>;
    } else {
      content = <Dashboard />;
    }
  
    if (isWorkbenchPath) {
      return (
        <div className="h-screen bg-background overflow-hidden selection:bg-primary/30 selection:text-white">
          <ThemeManager />
          {content}
        </div>
      );
    }
  
    // --- Persistent Settings Logic ---
    const convOn = config?.response_conversion_toggle || false;
    const convPreset = config?.opencc_preset || 'None';
    const bilingualOrder = config?.bilingual_text_order || 'translation_first';
  
    const pluginEnables = config?.plugin_enables || {};
    const isPluginBilingual = pluginEnables["BilingualPlugin"] || false;
    const projType = config?.translation_project || "AutoType";
    const isTypeBilingual = ["Txt", "Epub", "Srt"].includes(projType);
    const bilingualActive = isPluginBilingual || isTypeBilingual;
  
    const targetPlatform = config?.target_platform || "Unknown";
    const modelName = config?.model || "Unknown";
    const userThreads = config?.user_thread_counts || 0;
    const contextLines = config?.pre_line_counts || 3;
    const thinkOn = config?.think_switch || false;
      const isLocal = ["sakura", "localllm", "murasaki"].includes(targetPlatform.toLowerCase());
      
      const getThemeColor = () => {
        switch(activeTheme) {
            case 'elysia': return 'text-pink-500';
            case 'herrscher_of_human': return 'text-[#ff4d6d]';
            case 'eden': return 'text-amber-500';
            case 'mobius': return 'text-green-500';
            case 'pardofelis': return 'text-[#7f5539]';
            case 'griseo': return 'text-blue-500';
            case 'kevin': return 'text-sky-500';
            case 'kalpas': return 'text-red-500';
            case 'aponia': return 'text-purple-500';
            case 'villv': return 'text-orange-600';
            case 'su': return 'text-emerald-600';
            case 'sakura': return 'text-pink-600';
            case 'kosma': return 'text-indigo-400';
            case 'hua': return 'text-orange-600';
            default: return 'text-slate-500';
        }
      };
        const labelColor = getThemeColor();
        
        const getValueColor = () => {
          if (activeTheme === 'default') return 'text-slate-200';
          if (['herrscher_of_human', 'elysia', 'pardofelis', 'griseo'].includes(activeTheme)) {
              return 'text-slate-900'; // High contrast for light themes
          }
          return 'text-white'; // High contrast for dark themes
        };
        const valueColor = getValueColor();
      
        return (
                <div className="flex flex-col md:flex-row h-screen bg-background overflow-hidden selection:bg-primary/30 selection:text-white">            <ThemeManager />
            <ElysiaGuide />
      
            {showTip && (          <div className="elysia-tip flex items-center gap-2">
            <Sparkles size={18} />
            <span>听说明亮的粉色能提高翻译效率？</span>
          </div>
        )}
  
        {/* Mobile Top Bar */}
        <div className="md:hidden flex items-center justify-between p-4 bg-slate-900 border-b border-slate-800 z-50">
          <div className="flex items-center gap-2 cursor-pointer" onClick={(e) => handleLogoClick(e)}>
              <div className={`w-6 h-6 rounded bg-gradient-to-tr from-primary to-secondary flex items-center justify-center ${isShaking ? 'logo-shake' : ''}`}>
                  <span className="text-white font-bold font-mono text-xs">Ai</span>
              </div>
              <span className="font-bold text-white">{elysiaActive ? 'AiNiee Elysia' : 'AiNiee UI'}</span>
          </div>
          <button onClick={() => setIsSidebarOpen(!isSidebarOpen)} className="text-slate-400 p-1">
              {isSidebarOpen ? <CloseIcon size={24} /> : <Menu size={24} />}
          </button>
        </div>
  
        {/* Sidebar - Desktop and Mobile Overlay */}
        <aside className={`
          fixed inset-0 z-40 transform md:relative md:translate-x-0 md:flex md:w-64 bg-slate-900 border-r border-slate-800 flex flex-col transition-all duration-300
          ${isSidebarOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0'}
          ${isMonitorMode ? 'opacity-50 grayscale pointer-events-none' : ''}
        `}>
          <div className="p-6 overflow-y-auto flex-1 custom-scrollbar">
            <div className="hidden md:flex items-center gap-3 mb-8 cursor-pointer select-none" onClick={(e) => handleLogoClick(e)}>
              <div className={`w-8 h-8 rounded bg-gradient-to-tr from-primary to-secondary flex items-center justify-center shadow-lg shadow-primary/20 ${isShaking ? 'logo-shake' : ''}`}> 
                <span className="text-white font-bold font-mono">Ai</span>
              </div>
              <h1 className="text-xl font-bold text-white tracking-tight">AiNiee <span className="text-primary text-xs ml-1">{elysiaActive ? 'Elysia' : 'UI'}</span></h1>
            </div>
  
            <nav className="space-y-2">
              <SidebarItem to="/" icon={LayoutDashboard} labelKey="menu_title" currentPath={pathname} activeTheme={activeTheme} />
              <SidebarItem to="/task" icon={PlayCircle} labelKey="menu_start_translation" currentPath={pathname} activeTheme={activeTheme} />
              <SidebarItem to="/manga-editor" icon={Images} labelKey="menu_manga_editor" currentPath={pathname} activeTheme={activeTheme} />
              <SidebarItem to="/cache-editor" icon={Database} labelKey="menu_cache_editor" currentPath={pathname} activeTheme={activeTheme} />
              <SidebarItem to="/prompts" icon={Sparkles} labelKey="menu_prompt_features" currentPath={pathname} activeTheme={activeTheme} />
              <SidebarItem to="/rules" icon={BookOpen} labelKey="menu_glossary_rules" currentPath={pathname} activeTheme={activeTheme} />
              <SidebarItem to="/plugins" icon={Puzzle} labelKey="menu_plugin_settings" currentPath={pathname} activeTheme={activeTheme} />
              <SidebarItem to="/queue" icon={ListPlus} labelKey="menu_task_queue" currentPath={pathname} activeTheme={activeTheme} />
              <SidebarItem to="/settings" icon={SettingsIcon} labelKey="menu_settings" currentPath={pathname} activeTheme={activeTheme} />
              <SidebarItem to="/export" icon={Archive} labelKey="menu_export_only" currentPath={pathname} activeTheme={activeTheme} />
              <SidebarItem to="/logs" icon={TerminalIcon} labelKey="setting_session_logging" currentPath={pathname} activeTheme={activeTheme} />
            </nav>
          {/* Quick Settings Bar in Sidebar */}
          <div className="mt-8 space-y-4 pt-6 border-t border-slate-800/50">
             <div className="flex flex-col gap-2">
                <span className="text-[10px] font-bold text-slate-500 uppercase tracking-widest px-2">{t('ui_language')}</span>
                <div className="flex bg-slate-800/50 p-1 rounded-lg">
                    {availableLanguages.map(lang => (
                        <button 
                            key={lang.code}
                            onClick={() => handleLanguageChange(lang.code)}
                            className={`flex-1 py-1 text-[10px] font-bold rounded transition-all ${language === lang.code ? 'bg-primary text-slate-900' : 'text-slate-500 hover:text-slate-300'}`}
                        >
                            {lang.label.split(' ')[0]}
                        </button>
                    ))}
                </div>
             </div>

             <div className="space-y-3 px-2">
                <div className="flex flex-col">
                    <span className={`text-[10px] font-black uppercase tracking-tighter ${labelColor}`}>{t('banner_langs')}</span>
                    <span className={`text-xs font-bold ${elysiaActive || activeTheme === 'herrscher_of_human' ? 'text-pink-400' : 'text-slate-200'}`}>{config?.source_language || '...'} → {config?.target_language || '...'}</span>
                </div>

                <div className="flex flex-col">
                    <span className={`text-[10px] font-black uppercase tracking-tighter ${labelColor}`}>{t('banner_conv')}</span>
                    <span className={`text-xs font-black ${convOn ? 'text-green-400 shadow-[0_0_8px_rgba(74,222,128,0.2)]' : 'text-red-400'}`}>
                        {convOn ? `${t('banner_on')} (${convPreset})` : t('banner_off')}
                    </span>
                </div>

                <div className="flex flex-col">
                    <span className={`text-[10px] font-black uppercase tracking-tighter ${labelColor}`}>{t('banner_bilingual')}</span>
                    <span className={`text-xs font-black ${bilingualActive ? 'text-cyan-400' : 'text-slate-400'}`}>
                        {bilingualActive ? (bilingualOrder === 'translation_first' ? t('banner_trans_first') : t('banner_source_first')) : t('banner_not_enabled')}
                    </span>
                </div>

                <div className="pt-2 border-t border-slate-800/30 space-y-2">
                    <div className="flex justify-between items-center">
                        <span className={`text-[10px] font-black uppercase ${labelColor}`}>{t('banner_api')}</span>
                        <span className={`text-[10px] font-black truncate max-w-[100px] ${valueColor}`}>{targetPlatform}</span>
                    </div>
                    <div className="flex justify-between items-center">
                        <span className={`text-[10px] font-black uppercase ${labelColor}`}>{t('banner_threads')}</span>
                        <span className={`text-[10px] font-black ${valueColor}`}>{userThreads === 0 ? 'Auto' : userThreads}</span>
                    </div>
                    <div className="flex justify-between items-center">
                        <span className={`text-[10px] font-black uppercase ${labelColor}`}>{t('banner_context')}</span>
                        <span className={`text-[10px] font-black ${valueColor}`}>{contextLines}</span>
                    </div>
                    {!isLocal && (
                        <div className="flex justify-between items-center">
                            <span className={`text-[10px] font-black uppercase ${labelColor}`}>{t('banner_think')}</span>
                            <span className={`text-[10px] font-black ${thinkOn ? 'text-green-600' : 'text-red-600'}`}>{thinkOn ? t('banner_on') : t('banner_off')}</span>
                        </div>
                    )}
                </div>
             </div>
          </div>
        </div>
        
        <div className="p-6 border-t border-slate-800">
          {elysiaActive && (
            <div className="mb-4 text-center animate-in fade-in duration-1000">
              <p className="text-[10px] text-pink-400/60 italic font-serif leading-relaxed">
                "Between the beginning and the end,<br/>
                there's a miracle called 'us'."
              </p>
            </div>
          )}
          <div 
            className={`flex items-center gap-3 cursor-pointer select-none transition-all duration-300 ${activeShake ? 'logo-shake' : 'hint-shake'} ${isExploding ? 'scale-150 blur-sm' : ''}`}
            onClick={handleVersionClick}
          >
            <div className={`w-2 h-2 rounded-full animate-pulse ${activeTheme === 'herrscher_of_human' ? 'bg-pink-300 shadow-[0_0_12px_#ffccd5]' : (elysiaActive ? 'bg-pink-400 shadow-[0_0_8px_#ff8fa3]' : 'bg-green-500')}`} />
            <div className="flex flex-col">
                <span className={`text-xs transition-all duration-500 ${activeTheme === 'herrscher_of_human' ? 'text-pink-500 font-black tracking-widest' : (isVersionMetamorphosed ? 'text-pink-400 font-black tracking-widest' : 'text-slate-400')}`}>
                    {activeTheme === 'herrscher_of_human' ? '始源之真我' : (isVersionMetamorphosed ? 'TRUE SELF' : (elysiaActive ? '完美无瑕' : t('ui_system_online')))}
                </span>
                <span className={`text-[10px] font-mono ${activeTheme === 'herrscher_of_human' ? 'text-pink-400 font-bold' : 'text-slate-600'}`}>
                    {activeTheme === 'herrscher_of_human' ? `∞ ${version}` : (isExploding ? '✨BOOM✨' : version)}
                </span>
            </div>
          </div>
        </div>
      </aside>

      {/* Sidebar Backdrop Mobile */}
      {isSidebarOpen && (
        <div 
            className="fixed inset-0 bg-black/50 backdrop-blur-sm z-30 md:hidden"
            onClick={() => setIsSidebarOpen(false)}
        />
      )}

      {/* Main Content */}
      <main className="flex-1 overflow-auto relative">
        {/* Ripple Transition Overlay */}
        {rippleData.active && (
          <div 
            className={`fixed inset-0 z-[10000] pointer-events-none ${rippleData.type === 'out' ? 'elysia-ripple-out' : 'elysia-ripple-in'}`}
            style={{ 
                '--ripple-x': `${rippleData.x}px`, 
                '--ripple-y': `${rippleData.y}px`
            } as any}
          />
        )}

        {elysiaActive && (
          <div className="fixed bottom-8 right-8 pointer-events-none opacity-20 z-0">
            <Sparkles size={200} className="text-pink-300 animate-[spin_20s_linear_infinite]" />
          </div>
        )}
        <div className={`mx-auto h-full ${pathname === '/cache-editor' ? 'max-w-none p-0' : 'max-w-7xl p-4 md:p-8'}`}>
          {content}
        </div>
      </main>
    </div>
  );
};

const App: React.FC = () => {
  return (
    <I18nProvider>
      <GlobalProvider>
        <MainLayout />
      </GlobalProvider>
    </I18nProvider>
  );
}

export default App;
