import React, { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { AppConfig, LogEntry, TaskStats, ChartDataPoint, TaskType, ThemeType } from '../types';
import { DEFAULT_CONFIG } from '../constants';
import { DataService } from '../services/DataService';

// Define the shape of the Task Runner's persistent state
interface TaskRunnerState {
  logs: LogEntry[];
  stats: TaskStats;
  chartData: ChartDataPoint[];
  isRunning: boolean;
  taskType: TaskType;
  customInputPath: string;
  isResuming: boolean;
  isMangaMode: boolean;
  comparison?: {
    source: string;
    translation: string;
  };
}

const DEFAULT_TASK_STATE: TaskRunnerState = {
  logs: [],
  stats: {
    rpm: 0,
    tpm: 0,
    totalProgress: 0,
    completedProgress: 0,
    totalTokens: 0,
    elapsedTime: 0,
    status: 'idle',
    currentFile: 'Waiting for task...'
  },
  chartData: [],
  isRunning: false,
  taskType: TaskType.TRANSLATE,
  customInputPath: '',
  isResuming: false,
  isMangaMode: false,
  comparison: undefined // Set to undefined to match optional interface
};

interface GlobalContextType {
  // System Info
  version: string;
  setVersion: (v: string) => void;

  // Theme State
  activeTheme: ThemeType;
  setActiveTheme: (theme: ThemeType) => void;
  unlockedThemes: ThemeType[];
  unlockTheme: (theme: ThemeType) => void;
  unlockThemeWithNotification: (theme: ThemeType) => void; // New persistent unlock with modal
  notification: ThemeType | null;
  setNotification: (theme: ThemeType | null) => void;
  elysiaActive: boolean; 
  rippleData: { active: boolean, x: number, y: number, type: 'in' | 'out', targetTheme?: ThemeType };
  triggerRipple: (x: number, y: number, targetTheme?: ThemeType) => void;

  // Config
  config: AppConfig | null;
  configError: string | null;
  isConfigLoading: boolean;
  setConfig: (c: AppConfig | null) => void;
  refreshConfig: () => Promise<void>;
  profiles: string[];
  rulesProfiles: string[];

  // Task Runner State
  taskState: TaskRunnerState;
  setTaskState: (state: TaskRunnerState | ((prev: TaskRunnerState) => TaskRunnerState)) => void;

  // Helper to clear task state (optional, if needed manually)
  resetTaskState: () => void;
}

const GlobalContext = createContext<GlobalContextType | undefined>(undefined);

export const GlobalProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [version, setVersion] = useState<string>("Loading...");
  const [config, setConfigState] = useState<AppConfig | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  const [isConfigLoading, setIsConfigLoading] = useState(true);
  const [profiles, setProfiles] = useState<string[]>([]);
  const [rulesProfiles, setRulesProfiles] = useState<string[]>([]);

  const normalizeConfig = (rawConfig: unknown): AppConfig => {
    const raw = (rawConfig && typeof rawConfig === 'object' && !Array.isArray(rawConfig))
      ? (rawConfig as Record<string, any>)
      : {};

    const rawPlatforms = raw.platforms;
    const hasValidPlatforms =
      rawPlatforms &&
      typeof rawPlatforms === 'object' &&
      !Array.isArray(rawPlatforms) &&
      Object.keys(rawPlatforms).length > 0;
    const platforms = hasValidPlatforms ? rawPlatforms : { ...(DEFAULT_CONFIG.platforms || {}) };

    const backupApis = Array.isArray(raw.backup_apis)
      ? raw.backup_apis.filter((item: unknown) => typeof item === 'string')
      : [...(DEFAULT_CONFIG.backup_apis || [])];

    const responseCheckSwitch =
      raw.response_check_switch &&
      typeof raw.response_check_switch === 'object' &&
      !Array.isArray(raw.response_check_switch)
        ? { ...DEFAULT_CONFIG.response_check_switch, ...raw.response_check_switch }
        : { ...DEFAULT_CONFIG.response_check_switch };

    const apiSettings =
      raw.api_settings &&
      typeof raw.api_settings === 'object' &&
      !Array.isArray(raw.api_settings)
        ? { ...DEFAULT_CONFIG.api_settings, ...raw.api_settings }
        : { ...DEFAULT_CONFIG.api_settings };

    const unlockedThemes = Array.isArray(raw.unlocked_themes)
      ? raw.unlocked_themes
      : [...(DEFAULT_CONFIG.unlocked_themes || ['default'])];

    let targetPlatform = typeof raw.target_platform === 'string'
      ? raw.target_platform
      : DEFAULT_CONFIG.target_platform;
    if (!platforms[targetPlatform]) {
      const firstPlatform = Object.keys(platforms)[0];
      targetPlatform = firstPlatform || DEFAULT_CONFIG.target_platform;
    }

    return {
      ...DEFAULT_CONFIG,
      ...(raw as Partial<AppConfig>),
      backup_apis: backupApis,
      platforms,
      target_platform: targetPlatform,
      response_check_switch: responseCheckSwitch,
      api_settings: apiSettings,
      unlocked_themes: unlockedThemes
    };
  };

  // Theme & Transition
  const [activeTheme, setActiveThemeState] = useState<ThemeType>('default');
  const [notification, setNotification] = useState<ThemeType | null>(null);

  const [unlockedThemes, setUnlockedThemes] = useState<ThemeType[]>(['default']);

  // Update unlockedThemes and activeTheme when config is loaded
  useEffect(() => {
    if (config?.unlocked_themes) {
        setUnlockedThemes(config.unlocked_themes as ThemeType[]);

        // Auto-unlock Herrscher of Human if all 13 Flame-Chasers are unlocked
        const flameChasers: ThemeType[] = ['elysia', 'eden', 'mobius', 'pardofelis', 'griseo', 'kevin', 'kalpas', 'aponia', 'villv', 'su', 'sakura', 'kosma', 'hua'];
        const allUnlocked = flameChasers.every(t => config.unlocked_themes?.includes(t));

        if (allUnlocked && !config.unlocked_themes.includes('herrscher_of_human')) {
            unlockThemeWithNotification('herrscher_of_human');
        }
    }
    // Load active theme from config (server-side), fallback to localStorage for backwards compatibility
    if (config?.active_theme) {
        setActiveThemeState(config.active_theme as ThemeType);
    } else {
        const localTheme = localStorage.getItem('active_theme') as ThemeType;
        if (localTheme) {
            setActiveThemeState(localTheme);
        }
    }
  }, [config?.unlocked_themes, config?.active_theme]);

  const elysiaActive = activeTheme === 'elysia';

  const [rippleData, setRippleData] = useState<{ active: boolean, x: number, y: number, type: 'in' | 'out', targetTheme?: ThemeType }>({ active: false, x: 0, y: 0, type: 'out' });

  const setActiveTheme = async (theme: ThemeType) => {
    setActiveThemeState(theme);
    localStorage.setItem('active_theme', theme);
    // Also save to server-side config for persistence across port changes
    if (config) {
      const updatedConfig = { ...config, active_theme: theme };
      setConfigState(updatedConfig);
      try {
        await DataService.saveConfig(updatedConfig);
      } catch (e) {
        console.error("Failed to persist active theme to config", e);
      }
    }
  };

  const unlockTheme = (theme: ThemeType) => {
    setUnlockedThemes(prev => {
      if (prev.includes(theme)) return prev;
      return [...prev, theme];
    });
  };

  const unlockThemeWithNotification = async (theme: ThemeType) => {
    // 1. Check if already unlocked
    const alreadyUnlocked = config?.unlocked_themes?.includes(theme);
    if (alreadyUnlocked) return;

    // 2. Add to config and save to server
    if (config) {
        const newUnlocked = [...(config.unlocked_themes || ['default']), theme];
        const updatedConfig = { ...config, unlocked_themes: newUnlocked };
        setConfigState(updatedConfig);
        
        try {
            await DataService.saveConfig(updatedConfig);
            // 3. Show notification
            setNotification(theme);
            // 4. Update local state
            setUnlockedThemes(newUnlocked as ThemeType[]);
        } catch (e) {
            console.error("Failed to persist unlock to config", e);
        }
    }
  };

  const triggerRipple = (x: number, y: number, targetTheme?: ThemeType) => {
    const currentIsDefault = activeTheme === 'default';
    const isClosing = targetTheme === 'default' || (targetTheme === undefined && !currentIsDefault);
    const newType = isClosing ? 'in' : 'out';

    setRippleData({ active: true, x, y, type: newType, targetTheme });

    setTimeout(() => {
        const next = targetTheme !== undefined ? targetTheme : (activeTheme === 'default' ? 'elysia' : 'default');
        setActiveTheme(next);
        if (next !== 'default') unlockTheme(next);
    }, 1500); // Increased from 1000ms for more buffer

    setTimeout(() => {
        setRippleData(prev => ({ ...prev, active: false }));
    }, 2500); // Increased from 2000ms to allow smooth fade out
  };

  // Initialize Task State
  const [taskState, setTaskState] = useState<TaskRunnerState>(DEFAULT_TASK_STATE);

  // Initial Data Fetch
  useEffect(() => {
    // Fetch Version
    DataService.getVersion()
      .then(data => setVersion(data.version))
      .catch(() => setVersion("Offline"));

    // Fetch Config
    refreshConfig();
  }, []);

  const refreshConfig = async () => {
    setIsConfigLoading(true);
    setConfigError(null);
    try {
      const data = await DataService.getConfig();
      const normalized = normalizeConfig(data);
      setConfigState(normalized);

      // Fetch profiles
      const pList = await DataService.getProfiles();
      setProfiles(pList);
      const rList = await DataService.getRulesProfiles();
      setRulesProfiles(rList);

      // Also sync input path if not set yet
      setTaskState(prev => {
         if (!prev.customInputPath && normalized.label_input_path) {
             return { ...prev, customInputPath: normalized.label_input_path };
         }
         return prev;
      });
    } catch (e) {
      console.error("Global Context: Failed to load config", e);
      setConfigState(null);
      setConfigError(e instanceof Error ? e.message : String(e));
    } finally {
      setIsConfigLoading(false);
    }
  };

  // Wrap setConfig to allow simpler updates from components
  const setConfig = (newConfig: AppConfig | null) => {
      setConfigState(newConfig ? normalizeConfig(newConfig) : null);
      // In a real app, you might want to auto-save here or debounce save
  };

  const resetTaskState = () => {
      setTaskState(DEFAULT_TASK_STATE);
  };

  return (
    <GlobalContext.Provider value={{
      version,
      setVersion,
      activeTheme,
      setActiveTheme,
      unlockedThemes,
      unlockTheme,
      unlockThemeWithNotification,
      notification,
      setNotification,
      elysiaActive,
      rippleData,
      triggerRipple,
      config,
      configError,
      isConfigLoading,
      setConfig,
      refreshConfig,
      profiles,
      rulesProfiles,
      taskState,
      setTaskState,
      resetTaskState
    }}>
      {children}
    </GlobalContext.Provider>
  );
};
export const useGlobal = () => {
  const context = useContext(GlobalContext);
  if (!context) {
    throw new Error('useGlobal must be used within a GlobalProvider');
  }
  return context;
};
