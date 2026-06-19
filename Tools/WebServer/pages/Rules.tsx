import React, { useState, useEffect, useRef } from 'react';
import { Save, Plus, Trash2, BookOpen, Ban, AlertTriangle, RefreshCw, Search, ToggleLeft, ToggleRight, Download, Upload, History, Users, Map as MapIcon, PenTool, Languages, FileJson, ChevronDown, Sparkles, Play, Square, Layers } from 'lucide-react';
import { AppConfig, GlossaryItem, ExclusionItem, ReplacementRuleItem, CharacterizationItem, TranslationExampleItem, TermItem, TermOption } from '../types';
import { DataService } from '../services/DataService';
import { nativeConfirm } from '../services/nativeDialog';
import { useI18n } from '../contexts/I18nContext';
import { useGlobal } from '../contexts/GlobalContext';
import { TermSelector } from '../components/TermSelector';

type TabType = 'pre_translation' | 'post_translation' | 'glossary' | 'exclusion' | 'characterization' | 'world' | 'style' | 'example' | 'timeline' | 'ai_glossary';
type ReplacementRuleKind = 'pre' | 'post';

export const Rules: React.FC = () => {
    const { t } = useI18n();
    const { config, setConfig, activeTheme } = useGlobal(); // Access global config and active theme
    const [activeTab, setActiveTab] = useState<TabType>('glossary');

    const getThemeColor = () => {
        switch(activeTheme) {
            case 'elysia': return '#ff6699';
            case 'herrscher_of_human': return '#ff8fa3';
            case 'eden': return '#d4af37';
            case 'mobius': return '#00ff41';
            case 'pardofelis': return '#e6b8a2';
            case 'griseo': return '#a2c2e1';
            case 'kevin': return '#00f2ff';
            case 'kalpas': return '#ff4d4d';
            case 'aponia': return '#9d81ba';
            case 'villv': return '#c5a059';
            case 'su': return '#88a070';
            case 'sakura': return '#f4b0c7';
            case 'kosma': return '#1a2a4a';
            case 'hua': return '#d4a017';
            default: return '#06b6d4';
        }
    };
    
    const themeColor = getThemeColor();
    
    // Data State
    const [glossary, setGlossary] = useState<GlossaryItem[]>([]);
    const [preTranslationRules, setPreTranslationRules] = useState<ReplacementRuleItem[]>([]);
    const [postTranslationRules, setPostTranslationRules] = useState<ReplacementRuleItem[]>([]);
    const [exclusion, setExclusion] = useState<ExclusionItem[]>([]);
    const [characterization, setCharacterization] = useState<CharacterizationItem[]>([]);
    const [worldBuilding, setWorldBuilding] = useState<string>("");
    const [writingStyle, setWritingStyle] = useState<string>("");
    const [translationExample, setTranslationExample] = useState<TranslationExampleItem[]>([]);
    
    const [loading, setLoading] = useState(false);
    const [saving, setSaving] = useState(false);
    const [hasDraft, setHasDraft] = useState(false);
    const [historyErrors, setHistoryErrors] = useState<Record<string, string>>({});
    
    // Profile State
    const [profiles, setProfiles] = useState<string[]>([]);
    const [isProfileMenuOpen, setIsProfileMenuOpen] = useState(false);
    
    // Filter State
    const [filter, setFilter] = useState('');
    const [timelineVolume, setTimelineVolume] = useState(1);

    // AI Glossary Analysis State
    const [aiInputPath, setAiInputPath] = useState('');
    const [aiPercent, setAiPercent] = useState(30);
    const [aiLines, setAiLines] = useState<number | undefined>(undefined);
    const [aiAnalysisMode, setAiAnalysisMode] = useState<'full' | 'split' | 'incremental_split'>('full');
    const [aiIncrementalSplitTargetTokens, setAiIncrementalSplitTargetTokens] = useState<number>(200000);
    const [aiPromptFile, setAiPromptFile] = useState('');
    const [aiStatus, setAiStatus] = useState<any>({ status: 'idle', progress: 0, total: 0, message: '', results: [] });
    const [aiMinFreq, setAiMinFreq] = useState(2);
    const [aiFilename, setAiFilename] = useState('auto_glossary');
    const [aiLogs, setAiLogs] = useState<string[]>([]);
    const [aiUseTempConfig, setAiUseTempConfig] = useState(false);
    const [aiTempPlatform, setAiTempPlatform] = useState('');
    const [aiTempApiKey, setAiTempApiKey] = useState('');
    const [aiTempApiUrl, setAiTempApiUrl] = useState('');
    const [aiTempModel, setAiTempModel] = useState('');
    const [aiTempThreads, setAiTempThreads] = useState<number>(5);

    // Term Selector State
    const [showTermSelector, setShowTermSelector] = useState(false);
    const [selectorTerms, setSelectorTerms] = useState<TermItem[]>([]);

    const normalizeVolume = (value: any): number | null => {
        if (value === null || value === undefined || value === '' || typeof value === 'boolean') return null;
        const numeric = Number(value);
        if (Number.isInteger(numeric)) return numeric;
        const match = String(value).match(/(?:vol(?:ume)?|book|v|第)?[\s._-]*0*(\d{1,4})(?:\s*[卷册集部])?/i);
        return match ? Number(match[1]) : null;
    };

    const hasText = (value: any) => {
        if (Array.isArray(value)) return value.some(item => hasText(item));
        return value !== null && value !== undefined && String(value).trim().length > 0;
    };

    const createReplacementRuleId = () => {
        const randomValue = typeof crypto !== 'undefined' && 'randomUUID' in crypto
            ? crypto.randomUUID()
            : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
        return `rr_${randomValue}`;
    };

    const normalizeReplacementRuleItems = (items: any, persisted = false, preserveServerIndex = false): ReplacementRuleItem[] => {
        if (!Array.isArray(items)) return [];
        return items
            .filter(item => item && typeof item === 'object')
            .map((item, index) => {
                const normalized: ReplacementRuleItem = {
                    ...item,
                    src: item.src ?? '',
                    dst: item.dst ?? '',
                    regex: item.regex ?? '',
                    info: item.info ?? '',
                    _id: String(item._id || createReplacementRuleId()),
                };
                delete normalized._serverIndex;
                if (persisted) normalized._serverIndex = index;
                else if (preserveServerIndex && Number.isInteger(item._serverIndex)) normalized._serverIndex = item._serverIndex;
                return normalized;
            });
    };

    const stripReplacementRuleUiFields = (items: ReplacementRuleItem[]) => (
        items.map(item => {
            const { _serverIndex, ...persistedItem } = item;
            return persistedItem;
        })
    );

    const mergeReplacementRuleServerState = (
        localItems: ReplacementRuleItem[],
        serverItems: ReplacementRuleItem[],
    ): ReplacementRuleItem[] => {
        const serverById = new Map(
            normalizeReplacementRuleItems(serverItems, true).map(item => [item._id, item])
        );
        return localItems.map(item => {
            const serverItem = serverById.get(item._id);
            if (!serverItem) return item;
            return { ...item, _serverIndex: serverItem._serverIndex };
        });
    };

    const getHistory = (item: any): Record<string, any>[] => (
        Array.isArray(item?.history) ? item.history.filter((entry: any) => entry && typeof entry === 'object') : []
    );

    const historySortKey = (entry: Record<string, any>) => {
        const volume = normalizeVolume(entry.volume);
        return volume === null ? 1000000000 : volume;
    };

    const timelineTextAppend = (existing: string, addition: any) => {
        const current = String(existing || '').trim();
        const next = String(addition || '').trim();
        if (!next) return current;
        if (!current) return next;
        if (current.includes(next)) return current;
        if (next.includes(current)) return next;
        return `${current}\n\n${next}`;
    };

    const timelineTextForVolume = (history: any[], volume: number) => {
        if (!Array.isArray(history)) return '';
        return history
            .filter(entry => entry && typeof entry === 'object')
            .sort((a, b) => historySortKey(a) - historySortKey(b))
            .reduce((text, entry) => {
                const entryVolume = normalizeVolume(entry.volume);
                if (entryVolume === null || entryVolume > volume) return text;
                return timelineTextAppend(text, entry.content);
            }, '');
    };

    const timelineItemForVolume = <T extends Record<string, any>>(
        item: T,
        volume: number,
        keyField: string,
        trackedFields: string[],
    ): T | null => {
        const history = getHistory(item);
        if (history.length === 0) {
            const itemVolume = normalizeVolume(item.volume);
            return itemVolume !== null && itemVolume > volume ? null : item;
        }

        const selected: Record<string, any> = {};
        let selectedVolume: number | null = null;
        let selectedSource = '';
        history
            .sort((a, b) => historySortKey(a) - historySortKey(b))
            .forEach(entry => {
                const entryVolume = normalizeVolume(entry.volume);
                if (entryVolume === null || entryVolume > volume) return;
                if (hasText(entry[keyField])) selected[keyField] = entry[keyField];
                trackedFields.forEach(field => {
                    if (hasText(entry[field])) selected[field] = entry[field];
                });
                selectedVolume = entryVolume;
                selectedSource = entry.source || `Vol_${entryVolume}`;
            });

        if (Object.keys(selected).length === 0) return null;
        const result: Record<string, any> = {};
        if (hasText(item[keyField])) result[keyField] = item[keyField];
        else if (hasText(selected[keyField])) result[keyField] = selected[keyField];
        trackedFields.forEach(field => {
            if (field in selected) result[field] = selected[field];
        });
        if (selectedSource) result.source = selectedSource;
        if (selectedVolume !== null) result.volume = selectedVolume;
        return result as T;
    };

    const hasTimelineData = (glossaryItems = glossary, characterItems = characterization) => (
        glossaryItems.some(item => getHistory(item).length > 0) ||
        characterItems.some(item => getHistory(item).length > 0) ||
        Boolean(config?.prompt_dictionary_data?.some(item => getHistory(item).length > 0)) ||
        Boolean(config?.characterization_data?.some(item => getHistory(item).length > 0)) ||
        Boolean(config?.world_building_history?.length) ||
        Boolean(config?.writing_style_history?.length)
    );

    // Refs
    const draftTimerRef = useRef<any>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const latestRulesRef = useRef({
        glossary,
        preTranslationRules,
        postTranslationRules,
        exclusion,
        characterization,
        worldBuilding,
        writingStyle,
        translationExample
    });

    useEffect(() => {
        latestRulesRef.current = {
            glossary,
            preTranslationRules,
            postTranslationRules,
            exclusion,
            characterization,
            worldBuilding,
            writingStyle,
            translationExample
        };
    }, [glossary, preTranslationRules, postTranslationRules, exclusion, characterization, worldBuilding, writingStyle, translationExample]);

    useEffect(() => {
        loadData();
        loadProfiles();
        checkDrafts();
    }, []);

    useEffect(() => {
        if (activeTab === 'timeline' && !hasTimelineData()) {
            setActiveTab('glossary');
        }
    }, [
        activeTab,
        glossary,
        characterization,
        config?.prompt_dictionary_data,
        config?.characterization_data,
        config?.world_building_history,
        config?.writing_style_history,
    ]);

    // 切换到AI术语分析标签页时加载当前状态
    useEffect(() => {
        if (activeTab === 'ai_glossary') {
            const loadAnalysisStatus = async () => {
                try {
                    const status = await DataService.getAnalysisStatus();
                    setAiStatus(status);
                    if (status.logs && Array.isArray(status.logs)) {
                        setAiLogs(status.logs);
                    }
                    // 如果正在运行，继续轮询
                    if (status.status === 'running') {
                        pollAnalysisStatus();
                    }
                } catch (e) {
                    console.error("Failed to load analysis status", e);
                }
            };
            loadAnalysisStatus();
        }
    }, [activeTab]);

    const loadProfiles = async () => {
        try {
            const list = await DataService.getRulesProfiles();
            setProfiles(list);
        } catch (e) {
            console.error("Failed to load rules profiles", e);
        }
    };

    const handleProfileSwitch = async (profileName: string) => {
        if (config?.active_rules_profile === profileName) return;
        setLoading(true);
        try {
            const newConfig = await DataService.switchRulesProfile(profileName);
            setConfig(newConfig);
            // Reload data for the new profile
            await loadData(); 
            setIsProfileMenuOpen(false);
        } catch (e) {
            alert("Failed to switch rules profile");
        } finally {
            setLoading(false);
        }
    };

    const handleRulesProfileDelete = async (profileName: string) => {
        if (profileName === 'None' || config?.active_rules_profile === profileName) return;
        const confirmed = await nativeConfirm(
            t('msg_profile_delete_confirm').replace('{}', profileName),
            { title: t('menu_profile_delete') }
        );
        if (!confirmed) return;

        setLoading(true);
        try {
            await DataService.deleteRulesProfile(profileName);
            await loadProfiles();
        } catch (e: any) {
            alert(e?.message || "Failed to delete rules profile");
        } finally {
            setLoading(false);
        }
    };

    const loadData = async () => {
        setLoading(true);
        try {
            const [g, pre, post, e, c, w, s, ex] = await Promise.allSettled([
                DataService.getGlossary(),
                DataService.getPreTranslationRules(),
                DataService.getPostTranslationRules(),
                DataService.getExclusion(),
                DataService.getCharacterization(),
                DataService.getWorldBuilding(),
                DataService.getWritingStyle(),
                DataService.getTranslationExample()
            ]);
            if (g.status === 'fulfilled') setGlossary(g.value || []);
            if (pre.status === 'fulfilled') setPreTranslationRules(normalizeReplacementRuleItems(pre.value, true));
            if (post.status === 'fulfilled') setPostTranslationRules(normalizeReplacementRuleItems(post.value, true));
            if (e.status === 'fulfilled') setExclusion(e.value || []);
            if (c.status === 'fulfilled') setCharacterization(c.value || []);
            if (w.status === 'fulfilled') setWorldBuilding(w.value || "");
            if (s.status === 'fulfilled') setWritingStyle(s.value || "");
            if (ex.status === 'fulfilled') setTranslationExample(ex.value || []);
        } catch (error) {
            console.error("Failed to load rules", error);
        } finally {
            setLoading(false);
        }
    };

    const applyLocalConfigPatch = (patch: Partial<AppConfig>) => {
        if (!config) return;
        setConfig({ ...config, ...patch });
    };

    const buildSwitchPatch = (): Partial<AppConfig> => {
        if (!config) return {};
        switch (activeTab) {
            case 'pre_translation':
                return { pre_translation_switch: config.pre_translation_switch };
            case 'post_translation':
                return { post_translation_switch: config.post_translation_switch };
            case 'glossary':
                return { prompt_dictionary_switch: config.prompt_dictionary_switch };
            case 'exclusion':
                return { exclusion_list_switch: config.exclusion_list_switch };
            case 'characterization':
                return { characterization_switch: config.characterization_switch };
            case 'world':
                return { world_building_switch: config.world_building_switch };
            case 'style':
                return { writing_style_switch: config.writing_style_switch };
            case 'example':
                return { translation_example_switch: config.translation_example_switch };
            default:
                return {};
        }
    };

    const saveSwitchPatch = async (patch: Partial<AppConfig>) => {
        if (!Object.keys(patch).length) return;
        const currentConfig = await DataService.getConfig();
        await DataService.saveConfig({ ...currentConfig, ...patch });
    };

    const checkDrafts = async () => {
        try {
            const drafts = await Promise.all([
                DataService.getGlossaryDraft(),
                DataService.getPreTranslationDraft(),
                DataService.getPostTranslationDraft(),
                DataService.getExclusionDraft(),
                DataService.getCharacterizationDraft(),
                DataService.getWorldBuildingDraft(),
                DataService.getWritingStyleDraft(),
                DataService.getTranslationExampleDraft()
            ]);
            if (drafts.some(d => d && (Array.isArray(d) ? d.length > 0 : d.length > 0))) {
                setHasDraft(true);
            }
        } catch {}
    };

    const recoverDraft = async () => {
        if (!(await nativeConfirm(t('msg_recover_draft_confirm')))) return;
        try {
            const [gd, pred, postd, ed, cd, wd, sd, exd] = await Promise.all([
                DataService.getGlossaryDraft(),
                DataService.getPreTranslationDraft(),
                DataService.getPostTranslationDraft(),
                DataService.getExclusionDraft(),
                DataService.getCharacterizationDraft(),
                DataService.getWorldBuildingDraft(),
                DataService.getWritingStyleDraft(),
                DataService.getTranslationExampleDraft()
            ]);
            
            if (gd && gd.length > 0) {
                latestRulesRef.current = { ...latestRulesRef.current, glossary: gd };
                setGlossary(gd);
            }
            if (pred && pred.length > 0) {
                const next = normalizeReplacementRuleItems(pred, false, true);
                latestRulesRef.current = { ...latestRulesRef.current, preTranslationRules: next };
                setPreTranslationRules(next);
            }
            if (postd && postd.length > 0) {
                const next = normalizeReplacementRuleItems(postd, false, true);
                latestRulesRef.current = { ...latestRulesRef.current, postTranslationRules: next };
                setPostTranslationRules(next);
            }
            if (ed && ed.length > 0) {
                latestRulesRef.current = { ...latestRulesRef.current, exclusion: ed };
                setExclusion(ed);
            }
            if (cd && cd.length > 0) {
                latestRulesRef.current = { ...latestRulesRef.current, characterization: cd };
                setCharacterization(cd);
            }
            if (wd) {
                latestRulesRef.current = { ...latestRulesRef.current, worldBuilding: wd };
                setWorldBuilding(wd);
            }
            if (sd) {
                latestRulesRef.current = { ...latestRulesRef.current, writingStyle: sd };
                setWritingStyle(sd);
            }
            if (exd && exd.length > 0) {
                latestRulesRef.current = { ...latestRulesRef.current, translationExample: exd };
                setTranslationExample(exd);
            }
            
            alert(t('msg_draft_recovered'));
        } catch (error) {
            alert("Failed to recover draft.");
        }
    };

    const handleSave = async () => {
        setSaving(true);
        try {
            const switchPatch = buildSwitchPatch();
            let dataPatch: Partial<AppConfig> = {};
            switch (activeTab) {
                case 'glossary':
                    await DataService.saveGlossary(glossary);
                    dataPatch = { prompt_dictionary_data: glossary };
                    break;
                case 'pre_translation':
                    {
                        const saved = normalizeReplacementRuleItems(
                            await DataService.savePreTranslationRules(stripReplacementRuleUiFields(preTranslationRules)),
                            true,
                        );
                        latestRulesRef.current = { ...latestRulesRef.current, preTranslationRules: saved };
                        setPreTranslationRules(saved);
                        dataPatch = { pre_translation_data: stripReplacementRuleUiFields(saved) };
                    }
                    break;
                case 'post_translation':
                    {
                        const saved = normalizeReplacementRuleItems(
                            await DataService.savePostTranslationRules(stripReplacementRuleUiFields(postTranslationRules)),
                            true,
                        );
                        latestRulesRef.current = { ...latestRulesRef.current, postTranslationRules: saved };
                        setPostTranslationRules(saved);
                        dataPatch = { post_translation_data: stripReplacementRuleUiFields(saved) };
                    }
                    break;
                case 'exclusion':
                    await DataService.saveExclusion(exclusion);
                    dataPatch = { exclusion_list_data: exclusion };
                    break;
                case 'characterization':
                    await DataService.saveCharacterization(characterization);
                    dataPatch = { characterization_data: characterization };
                    break;
                case 'world':
                    await DataService.saveWorldBuilding(worldBuilding);
                    dataPatch = { world_building_content: worldBuilding };
                    break;
                case 'style':
                    await DataService.saveWritingStyle(writingStyle);
                    dataPatch = { writing_style_content: writingStyle };
                    break;
                case 'example':
                    await DataService.saveTranslationExample(translationExample);
                    dataPatch = { translation_example_data: translationExample };
                    break;
            }
            await saveSwitchPatch(switchPatch);
            applyLocalConfigPatch({ ...dataPatch, ...switchPatch });
            alert(t('msg_saved'));
        } catch (error) {
            console.error("Failed to save", error);
            alert("Save failed.");
        } finally {
            setSaving(false);
        }
    };

    const triggerDraftSave = () => {
        if (draftTimerRef.current) clearTimeout(draftTimerRef.current);
        draftTimerRef.current = setTimeout(async () => {
            const latest = latestRulesRef.current;
            switch (activeTab) {
                case 'glossary': await DataService.saveGlossaryDraft(latest.glossary); break;
                case 'pre_translation': await DataService.savePreTranslationDraft(latest.preTranslationRules); break;
                case 'post_translation': await DataService.savePostTranslationDraft(latest.postTranslationRules); break;
                case 'exclusion': await DataService.saveExclusionDraft(latest.exclusion); break;
                case 'characterization': await DataService.saveCharacterizationDraft(latest.characterization); break;
                case 'world': await DataService.saveWorldBuildingDraft(latest.worldBuilding); break;
                case 'style': await DataService.saveWritingStyleDraft(latest.writingStyle); break;
                case 'example': await DataService.saveTranslationExampleDraft(latest.translationExample); break;
            }
            setHasDraft(true);
        }, 2000); // 2s debounce
    };

    const exportData = () => {
        let data: any;
        let filename = `${activeTab}.json`;
        let isText = false;

        switch (activeTab) {
            case 'glossary': data = glossary; break;
            case 'pre_translation': data = stripReplacementRuleUiFields(preTranslationRules); break;
            case 'post_translation': data = stripReplacementRuleUiFields(postTranslationRules); break;
            case 'exclusion': data = exclusion; break;
            case 'characterization': data = characterization; break;
            case 'world': data = worldBuilding; filename = 'world_building.txt'; isText = true; break;
            case 'style': data = writingStyle; filename = 'writing_style.txt'; isText = true; break;
            case 'example': data = translationExample; filename = 'examples.json'; break;
        }

        const blob = new Blob([isText ? data : JSON.stringify(data, null, 4)], { type: isText ? 'text/plain' : 'application/json' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
    };

    const importData = (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (!file) return;

        const reader = new FileReader();
        reader.onload = (event) => {
            const content = event.target?.result as string;
            
            try {
                if (activeTab === 'world' || activeTab === 'style') {
                    if (activeTab === 'world') {
                        latestRulesRef.current = { ...latestRulesRef.current, worldBuilding: content };
                        setWorldBuilding(content);
                    } else {
                        latestRulesRef.current = { ...latestRulesRef.current, writingStyle: content };
                        setWritingStyle(content);
                    }
                } else {
                    const json = JSON.parse(content);
                    if (Array.isArray(json)) {
                        switch (activeTab) {
                            case 'glossary':
                                latestRulesRef.current = { ...latestRulesRef.current, glossary: json };
                                setGlossary(json);
                                break;
                            case 'pre_translation':
                                {
                                    const next = normalizeReplacementRuleItems(json);
                                    latestRulesRef.current = { ...latestRulesRef.current, preTranslationRules: next };
                                    setPreTranslationRules(next);
                                }
                                break;
                            case 'post_translation':
                                {
                                    const next = normalizeReplacementRuleItems(json);
                                    latestRulesRef.current = { ...latestRulesRef.current, postTranslationRules: next };
                                    setPostTranslationRules(next);
                                }
                                break;
                            case 'exclusion':
                                latestRulesRef.current = { ...latestRulesRef.current, exclusion: json };
                                setExclusion(json);
                                break;
                            case 'characterization':
                                latestRulesRef.current = { ...latestRulesRef.current, characterization: json };
                                setCharacterization(json);
                                break;
                            case 'example':
                                latestRulesRef.current = { ...latestRulesRef.current, translationExample: json };
                                setTranslationExample(json);
                                break;
                        }
                    } else {
                        alert(t('msg_import_error_list'));
                        return;
                    }
                }
                triggerDraftSave();
                alert(t('msg_import_success'));
            } catch (err) {
                alert(t('msg_import_error_parse'));
            }
        };
        reader.readAsText(file);
        if (fileInputRef.current) fileInputRef.current.value = '';
    };

    // --- Toggle Handlers ---
    const toggleSwitch = (field: keyof typeof config) => {
        if (!config) return;
        const patch = { [field]: !config[field] } as Partial<AppConfig>;
        setConfig({ ...config, ...patch });
    };

    const isLocal = config?.target_platform && ["sakura", "localllm", "murasaki"].includes(config.target_platform.toLowerCase());
    const isOnlineOnlyTab = ['characterization', 'world', 'style', 'example'].includes(activeTab);
    const glossaryHasTimeline = glossary.some(item => getHistory(item).length > 0);
    const characterHasTimeline = characterization.some(item => getHistory(item).length > 0);
    const timelineGlossarySource = glossaryHasTimeline
        ? glossary
        : (config?.prompt_dictionary_data || []);
    const timelineCharacterSource = characterHasTimeline
        ? characterization
        : (config?.characterization_data || []);
    const timelineAvailable = hasTimelineData(timelineGlossarySource, timelineCharacterSource);
    const previewGlossary = timelineGlossarySource
        .map(item => timelineItemForVolume(item, timelineVolume, 'src', ['dst', 'info']))
        .filter((item): item is GlossaryItem => Boolean(item));
    const previewCharacters = timelineCharacterSource
        .map(item => timelineItemForVolume(item, timelineVolume, 'original_name', [
            'translated_name',
            'aliases',
            'gender',
            'age',
            'personality',
            'speech_style',
            'pronouns',
            'speech_quirks',
            'additional_info',
        ]))
        .filter((item): item is CharacterizationItem => Boolean(item));
    const worldTimelineHistory = config?.world_building_history || [];
    const styleTimelineHistory = config?.writing_style_history || [];
    const previewWorldBuilding = worldTimelineHistory.length > 0
        ? timelineTextForVolume(worldTimelineHistory, timelineVolume)
        : worldBuilding;
    const previewWritingStyle = styleTimelineHistory.length > 0
        ? timelineTextForVolume(styleTimelineHistory, timelineVolume)
        : writingStyle;

    // --- UI Helpers ---
    const TabButton = ({ id, icon: Icon, label }: { id: TabType, icon: any, label: string }) => {
        const isProtected = ['characterization', 'world', 'style', 'example'].includes(id);
        const isActive = activeTab === id;
        const isHerrscher = activeTheme === 'herrscher_of_human';

        return (
            <button
                onClick={() => setActiveTab(id)}
                className={`flex items-center gap-2 px-3 py-2 text-xs md:text-sm rounded-md transition-all shrink-0 border ${
                    isActive 
                        ? (isHerrscher ? 'bg-pink-400/20 text-[#ff4d6d] border-pink-400 shadow-[0_0_15px_rgba(255,143,163,0.4)] font-black' : 'font-bold shadow-md') 
                        : 'text-slate-400 hover:text-slate-200 border-transparent'
                }`}
                style={isActive && !isHerrscher ? { 
                    backgroundColor: `${themeColor}20`, 
                    color: activeTheme === 'default' ? '#000' : themeColor, 
                    borderColor: activeTheme === 'default' ? 'var(--primary)' : `${themeColor}60`,
                    boxShadow: activeTheme !== 'default' ? `0 0 10px ${themeColor}30` : 'none'
                } : {}}
            >
                <Icon size={16} className={isActive ? 'animate-pulse' : ''} /> {label}
                {isProtected && <span className="hidden sm:inline text-[10px] opacity-70 px-1 border border-current rounded uppercase ml-1 font-black">Online</span>}
            </button>
        );
    };

    // --- CRUD Helpers ---
    const addCharacterItem = () => {
        const next = [{
            original_name: '', translated_name: '', gender: '', age: '', 
            aliases: [], personality: '', speech_style: '', pronouns: '', speech_quirks: '', additional_info: ''
        }, ...characterization];
        latestRulesRef.current = { ...latestRulesRef.current, characterization: next };
        setCharacterization(next);
        triggerDraftSave();
    };

    const formatAliases = (value: CharacterizationItem['aliases']) => {
        if (Array.isArray(value)) return value.join('、');
        return value || '';
    };

    const parseAliases = (value: string) => value
        .split(/[,;|/，、；／\n]+/)
        .map(item => item.trim())
        .filter(Boolean);

    const formatOptionalNumber = (value: any) => (
        value === null || value === undefined ? '' : String(value)
    );

    const parseOptionalNumber = (value: string) => {
        const trimmed = value.trim();
        if (!trimmed) return undefined;
        const numeric = Number(trimmed);
        return Number.isFinite(numeric) ? numeric : trimmed;
    };

    const formatHistoryJson = (value: any) => {
        if (!Array.isArray(value)) return '';
        try {
            return JSON.stringify(value, null, 2);
        } catch {
            return '';
        }
    };

    const parseHistoryJson = (scope: string, value: string, onValid: (history: Record<string, any>[]) => void) => {
        const trimmed = value.trim();
        if (!trimmed) {
            setHistoryErrors(prev => {
                const next = { ...prev };
                delete next[scope];
                return next;
            });
            onValid([]);
            return;
        }

        try {
            const parsed = JSON.parse(trimmed);
            if (!Array.isArray(parsed)) throw new Error('History must be a JSON array');
            onValid(parsed.filter(item => item && typeof item === 'object'));
            setHistoryErrors(prev => {
                const next = { ...prev };
                delete next[scope];
                return next;
            });
        } catch (error: any) {
            setHistoryErrors(prev => ({ ...prev, [scope]: error?.message || 'Invalid JSON' }));
        }
    };

    const updateGlossaryItem = (originalIdx: number, field: keyof GlossaryItem, val: GlossaryItem[keyof GlossaryItem]) => {
        const newItems = [...glossary];
        newItems[originalIdx] = { ...newItems[originalIdx], [field]: val };
        latestRulesRef.current = { ...latestRulesRef.current, glossary: newItems };
        setGlossary(newItems);
        triggerDraftSave();
    };

    const updateReplacementRuleItem = (
        kind: 'pre' | 'post',
        originalIdx: number,
        field: keyof ReplacementRuleItem,
        val: ReplacementRuleItem[keyof ReplacementRuleItem],
    ) => {
        const source = kind === 'pre' ? preTranslationRules : postTranslationRules;
        const next = [...source];
        next[originalIdx] = { ...next[originalIdx], [field]: val };
        if (kind === 'pre') {
            latestRulesRef.current = { ...latestRulesRef.current, preTranslationRules: next };
            setPreTranslationRules(next);
        } else {
            latestRulesRef.current = { ...latestRulesRef.current, postTranslationRules: next };
            setPostTranslationRules(next);
        }
        triggerDraftSave();
    };

    const updateCharacterItem = (originalIdx: number, field: keyof CharacterizationItem, val: CharacterizationItem[keyof CharacterizationItem]) => {
        const newItems = [...characterization];
        newItems[originalIdx] = { ...newItems[originalIdx], [field]: val };
        latestRulesRef.current = { ...latestRulesRef.current, characterization: newItems };
        setCharacterization(newItems);
        triggerDraftSave();
    };

    const removeGlossaryItem = (originalIdx: number) => {
        const next = glossary.filter((_, i) => i !== originalIdx);
        latestRulesRef.current = { ...latestRulesRef.current, glossary: next };
        setGlossary(next);
        triggerDraftSave();
    };

    const deleteReplacementRuleItem = async (kind: ReplacementRuleKind, originalIdx: number) => {
        try {
            if (!(await nativeConfirm(t('ui_rules_delete_confirm')))) return;
            const source = kind === 'pre' ? preTranslationRules : postTranslationRules;
            const item = source[originalIdx];
            const serverIndex = typeof item?._serverIndex === 'number' ? item._serverIndex : undefined;
            const hasPersistedTarget = Boolean(item?._id) && serverIndex !== undefined;
            const result = hasPersistedTarget
                ? (kind === 'pre'
                    ? await DataService.deletePreTranslationRule(serverIndex, item._id)
                    : await DataService.deletePostTranslationRule(serverIndex, item._id))
                : null;
            const localAfterDelete = source.filter((_, idx) => idx !== originalIdx);
            const next = result
                ? mergeReplacementRuleServerState(localAfterDelete, result.items)
                : localAfterDelete;
            const persistedItems = result
                ? stripReplacementRuleUiFields(normalizeReplacementRuleItems(result.items, true))
                : stripReplacementRuleUiFields(next);
            if (kind === 'pre') {
                latestRulesRef.current = { ...latestRulesRef.current, preTranslationRules: next };
                setPreTranslationRules(next);
                if (result) applyLocalConfigPatch({ pre_translation_data: persistedItems });
            } else {
                latestRulesRef.current = { ...latestRulesRef.current, postTranslationRules: next };
                setPostTranslationRules(next);
                if (result) applyLocalConfigPatch({ post_translation_data: persistedItems });
            }
            triggerDraftSave();
        } catch (error) {
            console.error("Failed to delete replacement rule", error);
            alert("Delete failed.");
        }
    };

    const addReplacementRuleItem = (kind: ReplacementRuleKind) => {
        const source = kind === 'pre' ? preTranslationRules : postTranslationRules;
        const next = [{ _id: createReplacementRuleId(), src: '', dst: '', regex: '', info: '' }, ...source];
        if (kind === 'pre') {
            latestRulesRef.current = { ...latestRulesRef.current, preTranslationRules: next };
            setPreTranslationRules(next);
        } else {
            latestRulesRef.current = { ...latestRulesRef.current, postTranslationRules: next };
            setPostTranslationRules(next);
        }
        triggerDraftSave();
    };

    const removeExclusionItem = (originalIdx: number) => {
        const next = exclusion.filter((_, i) => i !== originalIdx);
        latestRulesRef.current = { ...latestRulesRef.current, exclusion: next };
        setExclusion(next);
        triggerDraftSave();
    };

    const removeCharacterItem = (originalIdx: number) => {
        const next = characterization.filter((_, i) => i !== originalIdx);
        latestRulesRef.current = { ...latestRulesRef.current, characterization: next };
        setCharacterization(next);
        triggerDraftSave();
    };

    const removeExampleItem = (originalIdx: number) => {
        const next = translationExample.filter((_, i) => i !== originalIdx);
        latestRulesRef.current = { ...latestRulesRef.current, translationExample: next };
        setTranslationExample(next);
        triggerDraftSave();
    };

    const addExampleItem = () => {
        const next = [{ src: '', dst: '' }, ...translationExample];
        latestRulesRef.current = { ...latestRulesRef.current, translationExample: next };
        setTranslationExample(next);
        triggerDraftSave();
    };

    const updateExampleItem = (originalIdx: number, field: keyof TranslationExampleItem, val: string) => {
        const newItems = [...translationExample];
        newItems[originalIdx] = { ...newItems[originalIdx], [field]: val };
        latestRulesRef.current = { ...latestRulesRef.current, translationExample: newItems };
        setTranslationExample(newItems);
        triggerDraftSave();
    };

    // --- AI Glossary Analysis Functions ---
    const startAiAnalysis = async () => {
        if (!aiInputPath) { alert('请输入文件路径'); return; }
        try {
            let analysisModeToStart = aiAnalysisMode;
            let splitTargetTokensToStart = aiAnalysisMode === 'incremental_split' ? aiIncrementalSplitTargetTokens : undefined;
            if (aiAnalysisMode === 'full') {
                const preflight = await DataService.preflightGlossaryAnalysis(aiInputPath, aiPercent, undefined);
                if (preflight.exceeds_warning) {
                    const acceptSplit = await nativeConfirm(t(
                        'ai_glossary_token_warning_confirm',
                        Number(preflight.estimated_tokens || 0).toLocaleString(),
                        Number(preflight.warning_threshold || 0).toLocaleString(),
                        Number(preflight.recommended_split_target_tokens || 200000).toLocaleString(),
                        Number(preflight.max_split_target_tokens || 256000).toLocaleString()
                    ));
                    if (acceptSplit) {
                        analysisModeToStart = 'incremental_split';
                        splitTargetTokensToStart = Math.min(256000, Math.max(1000, Number(aiIncrementalSplitTargetTokens) || Number(preflight.recommended_split_target_tokens) || 200000));
                    }
                }
            }
            await DataService.startGlossaryAnalysis(
                aiInputPath, aiPercent, aiAnalysisMode === 'split' ? aiLines : undefined,
                analysisModeToStart,
                splitTargetTokensToStart,
                aiPromptFile || undefined,
                aiUseTempConfig, aiTempPlatform, aiTempApiKey, aiTempApiUrl, aiTempModel, aiTempThreads
            );
            pollAnalysisStatus();
        } catch (e: any) {
            setAiLogs(prev => [...prev, `[${new Date().toLocaleTimeString()}] 错误: ${e.message}`]);
        }
    };

    const pollAnalysisStatus = () => {
        const poll = async () => {
            try {
                const status = await DataService.getAnalysisStatus();
                setAiStatus(status);
                // 从后端获取日志
                if (status.logs && Array.isArray(status.logs)) {
                    setAiLogs(status.logs);
                }
                if (status.status === 'running') {
                    setTimeout(poll, 1000);
                }
            } catch (e) {
                console.error(e);
            }
        };
        poll();
    };

    const stopAiAnalysis = async () => {
        try {
            await DataService.stopGlossaryAnalysis();
            setAiLogs(prev => [...prev, `[${new Date().toLocaleTimeString()}] 已停止分析`]);
        } catch (e: any) {
            setAiLogs(prev => [...prev, `[${new Date().toLocaleTimeString()}] 停止失败: ${e.message}`]);
        }
    };

    const saveAiResults = async () => {
        try {
            const result = await DataService.saveAnalysisResults(aiMinFreq, aiFilename);
            setAiLogs(prev => [...prev, `[${new Date().toLocaleTimeString()}] ${result.message}`]);
            alert(result.message);
            // Reload glossary data
            const g = await DataService.getGlossary();
            setGlossary(g || []);
        } catch (e: any) {
            setAiLogs(prev => [...prev, `[${new Date().toLocaleTimeString()}] 保存失败: ${e.message}`]);
            alert('保存失败: ' + e.message);
        }
    };

    // --- Term Selection Handlers ---
    const enterTermSelection = () => {
        try {
            if (!aiStatus || !Array.isArray(aiStatus.results)) {
                alert('尚未进行分析或分析结果无效');
                return;
            }

            const filtered = aiStatus.results.filter((r: any) => r && r.src && r.count >= aiMinFreq);
            if (filtered.length === 0) {
                alert('没有符合条件的术语');
                return;
            }

            // Create a map of existing glossary terms for quick lookup
            const glossaryMap = new Map();
            if (Array.isArray(glossary)) {
                glossary.forEach(item => {
                    if (item && item.src) glossaryMap.set(item.src, item);
                });
            }

            const initialTerms: TermItem[] = filtered.map((r: any) => {
                const existing = glossaryMap.get(r.src);
                return {
                    src: r.src,
                    type: r.type || (existing ? (existing.info || '专有名词') : '专有名词'),
                    analysis_info: r.info || 'null',
                    options: existing ? [{ dst: existing.dst, info: existing.info || '术语表' }] : [],
                    selected_index: 0,
                    saved: !!existing
                };
            });
            
            setSelectorTerms(initialTerms);
            setShowTermSelector(true);
        } catch (err: any) {
            console.error("Error entering term selection:", err);
            alert("进入选择界面失败: " + err.message);
        }
    };

    const handleTermRetry = async (item: TermItem) => {
        const avoid = item.options.map(o => o.dst);
        try {
            const res = await DataService.retryTermTranslation(
                item.src, 
                item.type, 
                avoid,
                aiUseTempConfig ? {
                    platform: aiTempPlatform,
                    api_key: aiTempApiKey,
                    api_url: aiTempApiUrl,
                    model: aiTempModel
                } : undefined,
                item.analysis_info
            );
            return res;
        } catch (e: any) {
            console.error(e);
            alert("重试失败: " + e.message);
            return null;
        }
    };

    const handleTermSaveSingle = async (item: TermItem) => {
        const selected = item.options[item.selected_index];
        if (!selected) return;
        try {
            const analysisInfo = `${item.type || '专有名词'} | ${item.analysis_info || 'null'}`;
            await DataService.addGlossaryItem({
                src: item.src,
                dst: selected.dst,
                info: analysisInfo
            });
            // Update glossary list in state if needed
            const g = await DataService.getGlossary();
            setGlossary(g || []);
        } catch (e) {
            alert('保存失败');
        }
    };

    const handleTermSaveAll = async (termsToSave: TermItem[]) => {
        const items = termsToSave.map(t => {
            const sel = t.options[t.selected_index];
            return { src: t.src, dst: sel?.dst || '', info: `${t.type || '专有名词'} | ${t.analysis_info || 'null'}` };
        }).filter(i => i.dst);
        
        if (items.length === 0) {
            alert('没有待保存的已翻译术语');
            return;
        }

        try {
            await DataService.batchAddGlossaryItems(items);
            alert(`成功保存 ${items.length} 条术语`);
            setShowTermSelector(false);
            // Refresh glossary
            const g = await DataService.getGlossary();
            setGlossary(g || []);
        } catch (e) {
            alert('批量保存失败');
        }
    };

    // --- Renderers ---

    const isLightCityTheme = ['herrscher_of_human', 'elysia', 'pardofelis', 'griseo'].includes(activeTheme);

    const renderTextEditor = (value: string, setter: (v: string) => void, placeholder: string) => (
        <div className="space-y-4 animate-in fade-in slide-in-from-bottom-2 duration-300">
            <div className={`flex justify-between items-center p-3 rounded-lg border transition-all ${isLightCityTheme ? 'bg-white/40 border-pink-200/50' : 'bg-slate-900/50 border-slate-800'}`}>
                <div className="flex items-center gap-2">
                    <button 
                        onClick={() => toggleSwitch(activeTab === 'world' ? 'world_building_switch' : 'writing_style_switch')}
                        className={`transition-all hover:scale-110 ${config?.[activeTab === 'world' ? 'world_building_switch' : 'writing_style_switch'] ? (isLightCityTheme ? 'text-pink-500' : 'text-green-400') : 'text-slate-600'}`}
                    >
                        {config?.[activeTab === 'world' ? 'world_building_switch' : 'writing_style_switch'] ? <ToggleRight size={32} /> : <ToggleLeft size={32} />}
                    </button>
                    <span className={`text-sm font-semibold ${isLightCityTheme ? 'text-pink-700' : 'text-slate-300'}`}>
                        {activeTab === 'world' ? t('feature_world_building_switch') : t('feature_writing_style_switch')}
                    </span>
                </div>
            </div>
            <textarea
                value={value}
                onChange={(e) => {
                    setter(e.target.value);
                    latestRulesRef.current = {
                        ...latestRulesRef.current,
                        [activeTab === 'world' ? 'worldBuilding' : 'writingStyle']: e.target.value
                    };
                    triggerDraftSave();
                }}
                placeholder={placeholder}
                className={`w-full h-96 border rounded-lg p-4 outline-none font-mono text-sm leading-relaxed transition-all bg-slate-950 ${
                    isLightCityTheme 
                        ? 'border-pink-200 text-pink-400 focus:border-pink-400' 
                        : 'border-slate-800 text-slate-300 focus:border-primary'
                }`}
                style={activeTheme !== 'default' ? { borderColor: `${themeColor}40` } : {}}
            />
        </div>
    );

    const renderTimelineBadge = (item: any) => {
        const count = getHistory(item).length;
        if (!count && !item?.source && !item?.volume) return null;
        return (
            <div className="flex flex-wrap gap-1 text-[11px]">
                {item?.source && <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-300 border border-slate-700">{item.source}</span>}
                {normalizeVolume(item?.volume) !== null && <span className="px-2 py-0.5 rounded bg-cyan-500/10 text-cyan-300 border border-cyan-500/30">Vol_{normalizeVolume(item.volume)}</span>}
                {count > 0 && <span className="px-2 py-0.5 rounded bg-amber-500/10 text-amber-300 border border-amber-500/30">{count} versions</span>}
            </div>
        );
    };

    const renderHistoryTimeline = (item: any, keyField: string, fields: string[]) => {
        const history = getHistory(item).sort((a, b) => historySortKey(a) - historySortKey(b));
        if (!history.length) return <div className="text-xs text-slate-500">No timeline history</div>;
        return (
            <div className="mt-3 grid gap-2">
                {history.map((entry, idx) => (
                    <div key={idx} className="border border-slate-800 rounded bg-slate-950/60 p-3">
                        <div className="flex flex-wrap items-center gap-2 mb-2">
                            <span className="text-xs font-bold text-cyan-300">{entry.source || `Vol_${normalizeVolume(entry.volume) ?? '?'}`}</span>
                            {normalizeVolume(entry.volume) !== null && <span className="text-[11px] text-slate-500">volume {normalizeVolume(entry.volume)}</span>}
                        </div>
                        <div className="grid gap-1 text-xs">
                            {hasText(entry[keyField]) && <div><span className="text-slate-500">{keyField}: </span><span className="text-slate-300">{entry[keyField]}</span></div>}
                            {fields.filter(field => hasText(entry[field])).map(field => (
                                <div key={field} className="break-words">
                                    <span className="text-slate-500">{field}: </span>
                                    <span className="text-slate-300 whitespace-pre-wrap">{Array.isArray(entry[field]) ? entry[field].join('、') : String(entry[field])}</span>
                                </div>
                            ))}
                        </div>
                    </div>
                ))}
            </div>
        );
    };

    const renderTimelineView = () => {
        const timelineGlossary = timelineGlossarySource.filter(item => getHistory(item).length > 0);
        const timelineCharacters = timelineCharacterSource.filter(item => getHistory(item).length > 0);
        const worldHistory = config?.world_building_history || [];
        const styleHistory = config?.writing_style_history || [];

        return (
            <div className="space-y-5 animate-in fade-in slide-in-from-bottom-2 duration-300">
                <div className={`p-4 rounded-lg border ${isLightCityTheme ? 'bg-white/50 border-pink-200/60' : 'bg-slate-900/50 border-slate-800'}`}>
                    <div className="flex flex-col md:flex-row md:items-center justify-between gap-3">
                        <div className="flex items-center gap-2">
                            <Layers size={18} style={{ color: themeColor }} />
                            <div>
                                <div className={`text-sm font-bold ${isLightCityTheme ? 'text-pink-700' : 'text-slate-200'}`}>多层时间线术语表</div>
                                <div className="text-xs text-slate-500">只读预览。按卷号查看翻译时实际注入的术语、角色、世界观和文风。</div>
                            </div>
                        </div>
                        <div className="flex items-center gap-2">
                            <span className="text-xs text-slate-500">预览卷号</span>
                            <input
                                type="number"
                                min={1}
                                value={timelineVolume}
                                onChange={(e) => setTimelineVolume(Math.max(1, Number(e.target.value) || 1))}
                                className="w-24 px-3 py-2 bg-slate-950 border border-slate-700 rounded text-sm text-slate-200 focus:border-primary outline-none"
                            />
                        </div>
                    </div>
                </div>

                {!timelineAvailable && (
                    <div className="p-4 rounded-lg border border-slate-800 bg-slate-900/50 text-sm text-slate-400">
                        当前规则配置没有检测到时间线历史。
                    </div>
                )}

                {timelineAvailable && (
                    <>
                        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                            <div className={`p-4 rounded-lg border ${isLightCityTheme ? 'bg-white/50 border-pink-100' : 'bg-slate-900 border-slate-800'}`}>
                                <div className="flex items-center justify-between mb-3">
                                    <h3 className="text-sm font-bold text-cyan-300">Vol_{timelineVolume} 实际术语</h3>
                                    <span className="text-xs text-slate-500">{previewGlossary.length}</span>
                                </div>
                                <div className="max-h-80 overflow-y-auto space-y-2 pr-1">
                                    {previewGlossary.map((item, idx) => (
                                        <div key={`${item.src}-${idx}`} className="p-3 rounded border border-slate-800 bg-slate-950/60">
                                            <div className="flex items-start justify-between gap-3">
                                                <div>
                                                    <div className="text-sm text-green-300">{item.src}</div>
                                                    <div className="text-sm text-blue-300">{item.dst}</div>
                                                </div>
                                                {renderTimelineBadge(item)}
                                            </div>
                                            {item.info && <div className="mt-2 text-xs text-slate-400 whitespace-pre-wrap">{item.info}</div>}
                                        </div>
                                    ))}
                                </div>
                            </div>

                            <div className={`p-4 rounded-lg border ${isLightCityTheme ? 'bg-white/50 border-pink-100' : 'bg-slate-900 border-slate-800'}`}>
                                <div className="flex items-center justify-between mb-3">
                                    <h3 className="text-sm font-bold text-cyan-300">Vol_{timelineVolume} 实际角色设定</h3>
                                    <span className="text-xs text-slate-500">{previewCharacters.length}</span>
                                </div>
                                <div className="max-h-80 overflow-y-auto space-y-2 pr-1">
                                    {previewCharacters.map((item, idx) => (
                                        <div key={`${item.original_name}-${idx}`} className="p-3 rounded border border-slate-800 bg-slate-950/60">
                                            <div className="flex items-start justify-between gap-3">
                                                <div>
                                                    <div className="text-sm text-green-300">{item.original_name}</div>
                                                    <div className="text-sm text-blue-300">{item.translated_name}</div>
                                                </div>
                                                {renderTimelineBadge(item)}
                                            </div>
                                            <div className="mt-2 grid gap-1 text-xs text-slate-400">
                                                {item.personality && <div>personality: {item.personality}</div>}
                                                {item.speech_style && <div>speech_style: {item.speech_style}</div>}
                                                {item.pronouns && <div>pronouns: {item.pronouns}</div>}
                                                {item.aliases && formatAliases(item.aliases) && <div>aliases: {formatAliases(item.aliases)}</div>}
                                                {item.speech_quirks && <div>speech_quirks: {item.speech_quirks}</div>}
                                                {item.additional_info && <div className="whitespace-pre-wrap">additional_info: {item.additional_info}</div>}
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        </div>

                        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                            <div className={`p-4 rounded-lg border ${isLightCityTheme ? 'bg-white/50 border-pink-100' : 'bg-slate-900 border-slate-800'}`}>
                                <div className="flex items-center justify-between mb-3">
                                    <h3 className="text-sm font-bold text-cyan-300">Vol_{timelineVolume} 世界观预览</h3>
                                    <span className="text-xs text-slate-500">{worldHistory.length} versions</span>
                                </div>
                                <pre className="max-h-72 overflow-y-auto whitespace-pre-wrap break-words rounded bg-slate-950/70 border border-slate-800 p-3 text-xs text-slate-300">{previewWorldBuilding || 'No content'}</pre>
                            </div>
                            <div className={`p-4 rounded-lg border ${isLightCityTheme ? 'bg-white/50 border-pink-100' : 'bg-slate-900 border-slate-800'}`}>
                                <div className="flex items-center justify-between mb-3">
                                    <h3 className="text-sm font-bold text-cyan-300">Vol_{timelineVolume} 文风预览</h3>
                                    <span className="text-xs text-slate-500">{styleHistory.length} versions</span>
                                </div>
                                <pre className="max-h-72 overflow-y-auto whitespace-pre-wrap break-words rounded bg-slate-950/70 border border-slate-800 p-3 text-xs text-slate-300">{previewWritingStyle || 'No content'}</pre>
                            </div>
                        </div>

                        {(timelineGlossary.length > 0 || timelineCharacters.length > 0) && (
                            <div className={`p-4 rounded-lg border ${isLightCityTheme ? 'bg-white/50 border-pink-100' : 'bg-slate-900 border-slate-800'}`}>
                                <h3 className="text-sm font-bold text-cyan-300 mb-3">原始时间线版本</h3>
                                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                                    {timelineGlossary.map((item, idx) => (
                                        <div key={`g-${item.src}-${idx}`} className="p-3 rounded border border-slate-800 bg-slate-950/40">
                                            <div className="text-sm text-green-300">{item.src}</div>
                                            {renderHistoryTimeline(item, 'src', ['dst', 'info'])}
                                        </div>
                                    ))}
                                    {timelineCharacters.map((item, idx) => (
                                        <div key={`c-${item.original_name}-${idx}`} className="p-3 rounded border border-slate-800 bg-slate-950/40">
                                            <div className="text-sm text-green-300">{item.original_name}</div>
                                            {renderHistoryTimeline(item, 'original_name', ['translated_name', 'aliases', 'gender', 'age', 'personality', 'speech_style', 'pronouns', 'speech_quirks', 'additional_info'])}
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}
                    </>
                )}
            </div>
        );
    };

    return (
        <div className="max-w-6xl mx-auto space-y-6 pb-12">
            {/* Header */}
            <div className="flex flex-col md:flex-row justify-between items-start md:items-center sticky top-0 bg-background/95 backdrop-blur z-20 py-4 border-b border-slate-800 gap-4">
                <div className="flex flex-wrap items-center gap-4 w-full md:w-auto">
                    <h1 className="text-xl md:text-2xl font-bold text-white">{t('menu_glossary_rules')}</h1>
                    
                    {/* Profile Selector */}
                    <div className="relative">
                        <button 
                            onClick={() => setIsProfileMenuOpen(!isProfileMenuOpen)}
                            className="flex items-center gap-2 px-3 py-1.5 bg-slate-900 border border-slate-700 rounded-lg text-sm text-slate-300 hover:text-white hover:border-slate-500 transition-colors"
                        >
                            <FileJson size={14} className="text-primary" />
                            <span className="font-medium max-w-[120px] md:max-w-[150px] truncate">{config?.active_rules_profile || 'Loading...'}</span>
                            <ChevronDown size={14} className={`transition-transform ${isProfileMenuOpen ? 'rotate-180' : ''}`} />
                        </button>
                        
                        {isProfileMenuOpen && (
                            <>
                                <div className="fixed inset-0 z-30" onClick={() => setIsProfileMenuOpen(false)} />
                                <div className="absolute top-full left-0 mt-2 w-56 bg-slate-900 border border-slate-700 rounded-lg shadow-xl z-40 py-1 max-h-60 overflow-y-auto no-scrollbar">
                                    {profiles.map(p => {
                                        const isActive = config?.active_rules_profile === p;
                                        const canDelete = p !== 'None' && !isActive;
                                        return (
                                            <div key={p} className="flex items-center hover:bg-slate-800 transition-colors">
                                                <button
                                                    onClick={() => handleProfileSwitch(p)}
                                                    className={`min-w-0 flex-1 text-left px-4 py-2 text-sm truncate ${isActive ? 'text-primary font-bold' : 'text-slate-300'}`}
                                                >
                                                    {p}
                                                </button>
                                                {canDelete && (
                                                    <button
                                                        onClick={(event) => {
                                                            event.stopPropagation();
                                                            void handleRulesProfileDelete(p);
                                                        }}
                                                        title={t('menu_profile_delete')}
                                                        className="shrink-0 p-2 mr-1 text-slate-500 hover:text-red-300 rounded transition-colors"
                                                    >
                                                        <Trash2 size={14} />
                                                    </button>
                                                )}
                                            </div>
                                        );
                                    })}
                                </div>
                            </>
                        )}
                    </div>
                </div>
                
                <div className="flex items-center gap-2 w-full md:w-auto justify-between md:justify-end">
                    <div className="relative hidden lg:block">
                        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
                        <input 
                            type="text" 
                            placeholder={t('ui_rules_filter')}
                            value={filter}
                            onChange={(e) => setFilter(e.target.value)}
                            className="bg-slate-900 border border-slate-700 rounded-lg pl-9 pr-3 py-2 text-sm text-white focus:border-primary w-32"
                        />
                    </div>
                    
                    {activeTab !== 'timeline' && (
                        <>
                            <div className="flex items-center gap-2">
                                <button onClick={exportData} title="Export to File" className="p-2 bg-slate-800 text-slate-300 rounded-lg hover:text-white transition-colors border border-slate-700">
                                    <Download size={18} />
                                </button>

                                <button onClick={() => fileInputRef.current?.click()} title="Import from File" className="p-2 bg-slate-800 text-slate-300 rounded-lg hover:text-white transition-colors border border-slate-700">
                                    <Upload size={18} />
                                    <input type="file" ref={fileInputRef} className="hidden" accept={activeTab === 'world' || activeTab === 'style' ? ".txt" : ".json"} onChange={importData} />
                                </button>

                                {hasDraft && (
                                    <button onClick={recoverDraft} title="Recover Unsaved Draft" className="p-2 bg-orange-500/10 text-orange-400 rounded-lg hover:bg-orange-500/20 transition-colors border border-orange-500/30">
                                        <History size={18} />
                                    </button>
                                )}
                            </div>

                            <button
                                onClick={handleSave}
                                disabled={saving}
                                className="flex items-center gap-2 bg-primary hover:bg-cyan-400 text-slate-900 px-4 py-2 rounded-lg font-bold transition-colors shadow-lg shadow-cyan-500/20 disabled:opacity-50"
                            >
                                {saving ? <RefreshCw size={18} className="animate-spin" /> : <Save size={18} />}
                                <span className="hidden sm:inline">{t('ui_rules_save')}</span>
                            </button>
                        </>
                    )}
                </div>
            </div>

            {/* Navigation Tabs */}
            <div
                className="flex max-w-full gap-2 bg-slate-900/50 p-2 rounded-lg border border-slate-800 overflow-x-auto whitespace-nowrap overscroll-x-contain"
            >
                <TabButton id="glossary" icon={BookOpen} label={t('ui_rules_glossary')} />
                <TabButton id="pre_translation" icon={FileJson} label={t('feature_pre_translation_switch')} />
                <TabButton id="post_translation" icon={FileJson} label={t('feature_post_translation_switch')} />
                <TabButton id="exclusion" icon={Ban} label={t('ui_rules_exclusion')} />
                <TabButton id="characterization" icon={Users} label={t('feature_characterization_switch')} />
                <TabButton id="world" icon={MapIcon} label={t('feature_world_building_switch')} />
                <TabButton id="style" icon={PenTool} label={t('feature_writing_style_switch')} />
                <TabButton id="example" icon={Languages} label={t('feature_translation_example_switch')} />
                {timelineAvailable && <TabButton id="timeline" icon={Layers} label="时间线" />}
                <TabButton id="ai_glossary" icon={Sparkles} label={t('ui_ai_glossary')} />
            </div>
            <p className="px-1 -mt-4 text-xs text-slate-500">
                {t('ui_rules_tabs_scroll_hint')}
            </p>

            {/* Content Area */}
            <div className="bg-surface/50 border border-slate-800 rounded-xl p-4 md:p-6 backdrop-blur-sm min-h-[500px]">
                {loading || !config ? (
                    <div className="flex flex-col items-center justify-center h-64 text-slate-500 gap-3">
                        <RefreshCw size={32} className="animate-spin text-primary" />
                        <p>Loading...</p>
                    </div>
                ) : (
                    <>
                        {isLocal && isOnlineOnlyTab && (
                            <div className="p-4 bg-red-500/10 border border-red-500/20 rounded-lg flex items-center gap-3 mb-6 shadow-lg animate-pulse">
                                <AlertTriangle size={24} className="text-red-500 shrink-0" />
                                <div>
                                    <h4 className="text-sm font-bold text-red-500 uppercase tracking-wider">Online Only / 仅限在线接口</h4>
                                    <p className="text-xs text-slate-200 mt-1 font-medium">{t('msg_online_features_warning')}</p>
                                </div>
                            </div>
                        )}
                        
                        {/* Tab Content: Glossary */}
                        {activeTab === 'glossary' && (
                            <div className="space-y-4 animate-in fade-in slide-in-from-bottom-2 duration-300">
                                <div className="flex justify-between items-center bg-slate-900/50 p-3 rounded-lg border border-slate-800"
                                     style={activeTheme !== 'default' ? { borderColor: `${themeColor}20` } : {}}>
                                    <div className="flex items-center gap-4">
                                        <div className="flex items-center gap-2 border-r border-slate-700 pr-4 mr-2">
                                            <button onClick={() => toggleSwitch('prompt_dictionary_switch')} 
                                                className="transition-all hover:scale-110"
                                                style={{ color: config.prompt_dictionary_switch ? themeColor : '#475569' }}
                                            >
                                                {config.prompt_dictionary_switch ? <ToggleRight size={32} /> : <ToggleLeft size={32} />}
                                            </button>
                                            <span className={`text-sm font-semibold ${isLightCityTheme ? 'text-pink-700' : 'text-slate-300'}`}>{t('ui_rules_enable_glossary')}</span>
                                        </div>
                                    </div>
                                    <button onClick={() => {
                                        const next = [{ src: '', dst: '', info: '' }, ...glossary];
                                        latestRulesRef.current = { ...latestRulesRef.current, glossary: next };
                                        setGlossary(next);
                                        triggerDraftSave();
                                    }}
                                        className="flex items-center gap-1 text-sm font-bold px-3 py-1.5 rounded transition-all hover:scale-105 active:scale-95"
                                        style={{ 
                                            backgroundColor: `${themeColor}20`, 
                                            color: isLightCityTheme ? '#ff4d6d' : themeColor,
                                            border: `1px solid ${themeColor}40`
                                        }}
                                    >
                                        <Plus size={16} /> {t('ui_rules_add')}
                                    </button>
                                </div>
                                <div className="grid gap-3">
                                    {glossary.map((item, originalIdx) => {
                                        if (filter && !item.src?.toLowerCase().includes(filter.toLowerCase())) return null;
                                        const historyScope = `glossary-${originalIdx}`;
                                        return (
                                            <div key={originalIdx} className={`grid grid-cols-12 gap-3 p-3 border rounded-lg transition-colors group ${isLightCityTheme ? 'bg-white/60 border-pink-100 hover:border-pink-300' : 'bg-slate-900 border-slate-800 hover:border-slate-600'}`}
                                                 style={!isLightCityTheme && activeTheme !== 'default' ? { borderColor: `${themeColor}20` } : {}}>
                                                <div className="col-span-12 md:col-span-3"><input type="text" placeholder={t('ui_rules_source')} value={item.src} onChange={(e) => updateGlossaryItem(originalIdx, 'src', e.target.value)} className="w-full bg-slate-950 border border-slate-700 rounded px-3 py-2 outline-none transition-all text-green-300 focus:border-primary" /></div>
                                                <div className="col-span-12 md:col-span-3"><input type="text" placeholder={t('ui_rules_target')} value={item.dst} onChange={(e) => updateGlossaryItem(originalIdx, 'dst', e.target.value)} className="w-full bg-slate-950 border border-slate-700 rounded px-3 py-2 outline-none transition-all text-blue-300 focus:border-primary" /></div>
                                                <div className="col-span-12 md:col-span-5"><input type="text" placeholder={t('ui_rules_note')} value={item.info || ''} onChange={(e) => updateGlossaryItem(originalIdx, 'info', e.target.value)} className="w-full bg-slate-950 border border-slate-700 rounded px-3 py-2 outline-none text-sm transition-all text-slate-400 focus:border-primary" /></div>
                                                <div className="col-span-12 md:col-span-1 flex justify-end"><button onClick={() => removeGlossaryItem(originalIdx)} className={`p-2 transition-colors ${isLightCityTheme ? 'text-pink-300 hover:text-red-500' : 'text-slate-600 hover:text-red-400'}`}><Trash2 size={18} /></button></div>
                                                <details className="col-span-12 rounded border border-slate-800 bg-slate-950/40 px-3 py-2">
                                                    <summary className="cursor-pointer select-none text-xs font-semibold text-slate-400">{t('ui_rules_metadata') || 'Metadata'}</summary>
                                                    <div className="mt-3 grid grid-cols-1 md:grid-cols-4 gap-3">
                                                        <input type="text" placeholder="source" value={item.source || ''} onChange={(e) => updateGlossaryItem(originalIdx, 'source', e.target.value)} className="bg-slate-950 border border-slate-700 rounded px-3 py-2 text-xs text-slate-300 focus:border-primary outline-none" />
                                                        <input type="text" placeholder="volume" value={formatOptionalNumber(item.volume)} onChange={(e) => updateGlossaryItem(originalIdx, 'volume', parseOptionalNumber(e.target.value))} className="bg-slate-950 border border-slate-700 rounded px-3 py-2 text-xs text-slate-300 focus:border-primary outline-none" />
                                                        <input type="text" placeholder="updated_in" value={item.updated_in || ''} onChange={(e) => updateGlossaryItem(originalIdx, 'updated_in', e.target.value)} className="bg-slate-950 border border-slate-700 rounded px-3 py-2 text-xs text-slate-300 focus:border-primary outline-none" />
                                                        <input type="text" placeholder="updated_volume" value={formatOptionalNumber(item.updated_volume)} onChange={(e) => updateGlossaryItem(originalIdx, 'updated_volume', parseOptionalNumber(e.target.value))} className="bg-slate-950 border border-slate-700 rounded px-3 py-2 text-xs text-slate-300 focus:border-primary outline-none" />
                                                        <div className="md:col-span-4">
                                                            <textarea
                                                                placeholder="history JSON"
                                                                defaultValue={formatHistoryJson(item.history)}
                                                                onBlur={(e) => parseHistoryJson(historyScope, e.target.value, (history) => updateGlossaryItem(originalIdx, 'history', history))}
                                                                className={`w-full h-28 bg-slate-950 border rounded px-3 py-2 text-xs font-mono text-slate-300 focus:border-primary outline-none resize-y ${historyErrors[historyScope] ? 'border-red-500' : 'border-slate-700'}`}
                                                            />
                                                            {historyErrors[historyScope] && <div className="mt-1 text-xs text-red-400">{historyErrors[historyScope]}</div>}
                                                        </div>
                                                    </div>
                                                </details>
                                            </div>
                                        );
                                    })}
                                </div>
                            </div>
                        )}

                        {/* Tab Content: Pre/Post Translation Replacement Rules */}
                        {(activeTab === 'pre_translation' || activeTab === 'post_translation') && (() => {
                            const kind = activeTab === 'pre_translation' ? 'pre' : 'post';
                            const items = kind === 'pre' ? preTranslationRules : postTranslationRules;
                            const switchKey = kind === 'pre' ? 'pre_translation_switch' : 'post_translation_switch';
                            const enabled = kind === 'pre' ? config.pre_translation_switch : config.post_translation_switch;
                            return (
                                <div className="space-y-4 animate-in fade-in slide-in-from-bottom-2 duration-300">
                                    <div className="flex justify-between items-center bg-slate-900/50 p-3 rounded-lg border border-slate-800"
                                         style={activeTheme !== 'default' ? { borderColor: `${themeColor}20` } : {}}>
                                        <div className="flex items-center gap-2">
                                            <button onClick={() => toggleSwitch(switchKey)}
                                                className="transition-all hover:scale-110"
                                                style={{ color: enabled ? themeColor : '#475569' }}
                                            >
                                                {enabled ? <ToggleRight size={32} /> : <ToggleLeft size={32} />}
                                            </button>
                                            <span className={`text-sm font-semibold ${isLightCityTheme ? 'text-pink-700' : 'text-slate-300'}`}>
                                                {kind === 'pre' ? t('feature_pre_translation_switch') : t('feature_post_translation_switch')}
                                            </span>
                                        </div>
                                        <button onClick={() => addReplacementRuleItem(kind)}
                                            className="flex items-center gap-1 text-sm font-bold px-3 py-1.5 rounded transition-all hover:scale-105 active:scale-95"
                                            style={{
                                                backgroundColor: `${themeColor}20`,
                                                color: isLightCityTheme ? '#ff4d6d' : themeColor,
                                                border: `1px solid ${themeColor}40`
                                            }}
                                        >
                                            <Plus size={16} /> {t('ui_rules_add')}
                                        </button>
                                    </div>
                                    <div className="grid gap-4">
                                        {items.map((item, originalIdx) => {
                                            if (filter && !`${item.src || ''} ${item.dst || ''} ${item.regex || ''} ${item.info || ''}`.toLowerCase().includes(filter.toLowerCase())) return null;
                                            return (
                                                <div key={originalIdx} className={`p-4 border rounded-lg transition-all group space-y-3 ${isLightCityTheme ? 'bg-white/60 border-pink-100 hover:border-pink-300' : 'bg-slate-900 border-slate-800 hover:border-slate-600'}`}>
                                                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                                                        <input type="text" placeholder={t('ui_rules_source')} value={item.src || ''} onChange={(e) => updateReplacementRuleItem(kind, originalIdx, 'src', e.target.value)} className="bg-slate-950 border border-slate-700 rounded px-3 py-2 text-green-300 focus:border-primary outline-none transition-all" />
                                                        <input type="text" placeholder={t('ui_rules_target')} value={item.dst || ''} onChange={(e) => updateReplacementRuleItem(kind, originalIdx, 'dst', e.target.value)} className="bg-slate-950 border border-slate-700 rounded px-3 py-2 text-blue-300 focus:border-primary outline-none transition-all" />
                                                    </div>
                                                    <div className="grid grid-cols-1 md:grid-cols-[1fr_auto] gap-3">
                                                        <input type="text" placeholder={t('ui_rules_regex')} value={item.regex || ''} onChange={(e) => updateReplacementRuleItem(kind, originalIdx, 'regex', e.target.value)} className="bg-slate-950 border border-slate-700 rounded px-3 py-2 text-purple-300 focus:border-primary outline-none transition-all font-mono text-sm" />
                                                        <button onClick={() => { void deleteReplacementRuleItem(kind, originalIdx); }} className={`p-2 transition-colors rounded border ${isLightCityTheme ? 'text-pink-300 hover:text-red-500 bg-white/80 border-pink-100' : 'text-slate-600 hover:text-red-400 bg-slate-950 border-slate-800'}`}><Trash2 size={18} /></button>
                                                    </div>
                                                    <input type="text" placeholder={t('ui_rules_note')} value={item.info || ''} onChange={(e) => updateReplacementRuleItem(kind, originalIdx, 'info', e.target.value)} className="w-full bg-slate-950 border border-slate-700 rounded px-3 py-2 text-slate-400 focus:border-primary outline-none transition-all" />
                                                </div>
                                            );
                                        })}
                                    </div>
                                </div>
                            );
                        })()}

                        {/* Tab Content: Exclusion */}
                        {activeTab === 'exclusion' && (
                            <div className="space-y-4 animate-in fade-in slide-in-from-bottom-2 duration-300">
                                <div className="flex justify-between items-center bg-slate-900/50 p-3 rounded-lg border border-slate-800"
                                     style={activeTheme !== 'default' ? { borderColor: `${themeColor}20` } : {}}>
                                    <div className="flex items-center gap-4">
                                        <div className="flex items-center gap-2 border-r border-slate-700 pr-4 mr-2">
                                            <button onClick={() => toggleSwitch('exclusion_list_switch')} 
                                                className="transition-all hover:scale-110"
                                                style={{ color: config.exclusion_list_switch ? themeColor : '#475569' }}
                                            >
                                                {config.exclusion_list_switch ? <ToggleRight size={32} /> : <ToggleLeft size={32} />}
                                            </button>
                                            <span className={`text-sm font-semibold ${isLightCityTheme ? 'text-pink-700' : 'text-slate-300'}`}>{t('ui_rules_enable_exclusion')}</span>
                                        </div>
                                    </div>
                                    <button onClick={() => {
                                        const next = [{ markers: '', regex: '', info: '' }, ...exclusion];
                                        latestRulesRef.current = { ...latestRulesRef.current, exclusion: next };
                                        setExclusion(next);
                                        triggerDraftSave();
                                    }}
                                        className="flex items-center gap-1 text-sm font-bold px-3 py-1.5 rounded transition-all hover:scale-105 active:scale-95"
                                        style={{ 
                                            backgroundColor: `${themeColor}20`, 
                                            color: isLightCityTheme ? '#ff4d6d' : themeColor,
                                            border: `1px solid ${themeColor}40`
                                        }}
                                    >
                                        <Plus size={16} /> {t('ui_rules_add')}
                                    </button>
                                </div>
                                <div className="grid gap-3">
                                    {exclusion.map((item, originalIdx) => {
                                        if (filter && !item.markers?.toLowerCase().includes(filter.toLowerCase())) return null;
                                        return (
                                            <div key={originalIdx} className={`grid grid-cols-12 gap-3 p-3 border rounded-lg transition-colors group ${isLightCityTheme ? 'bg-white/60 border-pink-100 hover:border-pink-300' : 'bg-slate-900 border-slate-800 hover:border-slate-600'}`}>
                                                <div className="col-span-4"><input type="text" placeholder={t('ui_rules_marker')} value={item.markers} onChange={(e) => { const n = [...exclusion]; n[originalIdx].markers = e.target.value; setExclusion(n); triggerDraftSave(); }} className="w-full bg-slate-950 border border-slate-700 rounded px-3 py-2 outline-none font-mono text-sm transition-all text-orange-300 focus:border-primary" /></div>
                                                <div className="col-span-4"><input type="text" placeholder={t('ui_rules_regex')} value={item.regex || ''} onChange={(e) => { const n = [...exclusion]; n[originalIdx].regex = e.target.value; setExclusion(n); triggerDraftSave(); }} className="w-full bg-slate-950 border border-slate-700 rounded px-3 py-2 outline-none font-mono text-sm transition-all text-purple-300 focus:border-primary" /></div>
                                                <div className="col-span-3"><input type="text" placeholder={t('ui_rules_note')} value={item.info || ''} onChange={(e) => { const n = [...exclusion]; n[originalIdx].info = e.target.value; setExclusion(n); triggerDraftSave(); }} className="w-full bg-slate-950 border border-slate-700 rounded px-3 py-2 outline-none text-sm transition-all text-slate-400 focus:border-primary" /></div>
                                                <div className="col-span-1 flex justify-end"><button onClick={() => removeExclusionItem(originalIdx)} className={`p-2 transition-colors ${isLightCityTheme ? 'text-pink-300 hover:text-red-500' : 'text-slate-600 hover:text-red-400'}`}><Trash2 size={18} /></button></div>
                                            </div>
                                        );
                                    })}
                                </div>
                            </div>
                        )}

                        {/* Tab Content: Characterization */}
                        {activeTab === 'characterization' && (
                            <div className="space-y-4 animate-in fade-in slide-in-from-bottom-2 duration-300">
                                <div className="flex justify-between items-center bg-slate-900/50 p-3 rounded-lg border border-slate-800"
                                     style={activeTheme !== 'default' ? { borderColor: `${themeColor}20` } : {}}>
                                    <div className="flex items-center gap-2">
                                        <button onClick={() => toggleSwitch('characterization_switch')} 
                                            className="transition-all hover:scale-110"
                                            style={{ color: config.characterization_switch ? themeColor : '#475569' }}
                                        >
                                            {config.characterization_switch ? <ToggleRight size={32} /> : <ToggleLeft size={32} />}
                                        </button>
                                        <span className={`text-sm font-semibold ${isLightCityTheme ? 'text-pink-700' : 'text-slate-300'}`}>{t('feature_characterization_switch')}</span>
                                    </div>
                                    <button onClick={addCharacterItem} 
                                        className="flex items-center gap-1 text-sm font-bold px-3 py-1.5 rounded transition-all hover:scale-105 active:scale-95"
                                        style={{ 
                                            backgroundColor: `${themeColor}20`, 
                                            color: isLightCityTheme ? '#ff4d6d' : themeColor,
                                            border: `1px solid ${themeColor}40`
                                        }}
                                    >
                                        <Plus size={16} /> {t('ui_rules_add')}
                                    </button>
                                </div>
                                <div className="grid gap-4">
                                    {characterization.map((item, originalIdx) => {
                                        if (filter && !item.original_name?.includes(filter) && !item.translated_name?.includes(filter)) return null;
                                        const historyScope = `character-${originalIdx}`;
                                        return (
                                            <div key={originalIdx} className={`p-4 border rounded-lg transition-all group space-y-3 ${isLightCityTheme ? 'bg-white/60 border-pink-100 hover:border-pink-300' : 'bg-slate-900 border-slate-800 hover:border-slate-600'}`}>
                                                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                                                    <input type="text" placeholder={t('ui_rules_character_orig')} value={item.original_name} onChange={(e) => updateCharacterItem(originalIdx, 'original_name', e.target.value)} className="bg-slate-950 border border-slate-700 rounded px-3 py-2 text-green-300 focus:border-primary outline-none transition-all" />
                                                    <input type="text" placeholder={t('ui_rules_character_trans')} value={item.translated_name} onChange={(e) => updateCharacterItem(originalIdx, 'translated_name', e.target.value)} className="bg-slate-950 border border-slate-700 rounded px-3 py-2 text-blue-300 focus:border-primary outline-none transition-all" />
                                                    <input type="text" placeholder={t('ui_rules_character_gender')} value={item.gender} onChange={(e) => updateCharacterItem(originalIdx, 'gender', e.target.value)} className="bg-slate-950 border border-slate-700 rounded px-3 py-2 text-slate-300 focus:border-primary outline-none transition-all" />
                                                    <input type="text" placeholder={t('ui_rules_character_age')} value={item.age} onChange={(e) => updateCharacterItem(originalIdx, 'age', e.target.value)} className="bg-slate-950 border border-slate-700 rounded px-3 py-2 text-slate-300 focus:border-primary outline-none transition-all" />
                                                </div>
                                                <div className="grid grid-cols-2 gap-3">
                                                    <input type="text" placeholder={t('ui_rules_character_personality')} value={item.personality} onChange={(e) => updateCharacterItem(originalIdx, 'personality', e.target.value)} className="bg-slate-950 border border-slate-700 rounded px-3 py-2 text-slate-300 focus:border-primary outline-none transition-all" />
                                                    <input type="text" placeholder={t('ui_rules_character_speech')} value={item.speech_style} onChange={(e) => updateCharacterItem(originalIdx, 'speech_style', e.target.value)} className="bg-slate-950 border border-slate-700 rounded px-3 py-2 text-slate-300 focus:border-primary outline-none transition-all" />
                                                </div>
                                                <input type="text" placeholder={t('ui_rules_character_aliases') || '原文别名/称呼'} value={formatAliases(item.aliases)} onChange={(e) => updateCharacterItem(originalIdx, 'aliases', parseAliases(e.target.value))} className="w-full bg-slate-950 border border-slate-700 rounded px-3 py-2 text-slate-300 focus:border-primary outline-none transition-all" />
                                                <div className="grid grid-cols-2 gap-3">
                                                    <input type="text" placeholder={t('ui_rules_character_pronouns') || '第一人称/第二人称'} value={item.pronouns || ''} onChange={(e) => updateCharacterItem(originalIdx, 'pronouns', e.target.value)} className="bg-slate-950 border border-slate-700 rounded px-3 py-2 text-slate-300 focus:border-primary outline-none transition-all" />
                                                    <input type="text" placeholder={t('ui_rules_character_speech_quirks') || '口癖/语尾'} value={item.speech_quirks || ''} onChange={(e) => updateCharacterItem(originalIdx, 'speech_quirks', e.target.value)} className="bg-slate-950 border border-slate-700 rounded px-3 py-2 text-slate-300 focus:border-primary outline-none transition-all" />
                                                </div>
                                                <div className="flex gap-3">
                                                    <input type="text" placeholder={t('ui_rules_character_info')} value={item.additional_info} onChange={(e) => updateCharacterItem(originalIdx, 'additional_info', e.target.value)} className="flex-1 bg-slate-950 border border-slate-700 rounded px-3 py-2 text-slate-400 focus:border-primary outline-none transition-all" />
                                                    <button onClick={() => removeCharacterItem(originalIdx)} className={`p-2 transition-colors rounded border ${isLightCityTheme ? 'text-pink-300 hover:text-red-500 bg-white/80 border-pink-100' : 'text-slate-600 hover:text-red-400 bg-slate-950 border-slate-800'}`}><Trash2 size={18} /></button>
                                                </div>
                                                <details className="rounded border border-slate-800 bg-slate-950/40 px-3 py-2">
                                                    <summary className="cursor-pointer select-none text-xs font-semibold text-slate-400">{t('ui_rules_metadata') || 'Metadata'}</summary>
                                                    <div className="mt-3 grid grid-cols-1 md:grid-cols-4 gap-3">
                                                        <input type="text" placeholder="source" value={item.source || ''} onChange={(e) => updateCharacterItem(originalIdx, 'source', e.target.value)} className="bg-slate-950 border border-slate-700 rounded px-3 py-2 text-xs text-slate-300 focus:border-primary outline-none" />
                                                        <input type="text" placeholder="volume" value={formatOptionalNumber(item.volume)} onChange={(e) => updateCharacterItem(originalIdx, 'volume', parseOptionalNumber(e.target.value))} className="bg-slate-950 border border-slate-700 rounded px-3 py-2 text-xs text-slate-300 focus:border-primary outline-none" />
                                                        <input type="text" placeholder="updated_in" value={item.updated_in || ''} onChange={(e) => updateCharacterItem(originalIdx, 'updated_in', e.target.value)} className="bg-slate-950 border border-slate-700 rounded px-3 py-2 text-xs text-slate-300 focus:border-primary outline-none" />
                                                        <input type="text" placeholder="updated_volume" value={formatOptionalNumber(item.updated_volume)} onChange={(e) => updateCharacterItem(originalIdx, 'updated_volume', parseOptionalNumber(e.target.value))} className="bg-slate-950 border border-slate-700 rounded px-3 py-2 text-xs text-slate-300 focus:border-primary outline-none" />
                                                        <div className="md:col-span-4">
                                                            <textarea
                                                                placeholder="history JSON"
                                                                defaultValue={formatHistoryJson(item.history)}
                                                                onBlur={(e) => parseHistoryJson(historyScope, e.target.value, (history) => updateCharacterItem(originalIdx, 'history', history))}
                                                                className={`w-full h-28 bg-slate-950 border rounded px-3 py-2 text-xs font-mono text-slate-300 focus:border-primary outline-none resize-y ${historyErrors[historyScope] ? 'border-red-500' : 'border-slate-700'}`}
                                                            />
                                                            {historyErrors[historyScope] && <div className="mt-1 text-xs text-red-400">{historyErrors[historyScope]}</div>}
                                                        </div>
                                                    </div>
                                                </details>
                                            </div>
                                        );
                                    })}
                                </div>
                            </div>
                        )}

                        {/* Tab Content: World Building */}
                        {activeTab === 'world' && renderTextEditor(worldBuilding, setWorldBuilding, t('ui_rules_world_placeholder'))}

                        {/* Tab Content: Writing Style */}
                        {activeTab === 'style' && renderTextEditor(writingStyle, setWritingStyle, t('ui_rules_style_placeholder'))}

                        {/* Tab Content: Translation Examples */}
                        {activeTab === 'example' && (
                            <div className="space-y-4 animate-in fade-in slide-in-from-bottom-2 duration-300">
                                <div className="flex justify-between items-center bg-slate-900/50 p-3 rounded-lg border border-slate-800"
                                     style={activeTheme !== 'default' ? { borderColor: `${themeColor}20` } : {}}>
                                    <div className="flex items-center gap-2">
                                        <button onClick={() => toggleSwitch('translation_example_switch')} 
                                            className="transition-all hover:scale-110"
                                            style={{ color: config.translation_example_switch ? themeColor : '#475569' }}
                                        >
                                            {config.translation_example_switch ? <ToggleRight size={32} /> : <ToggleLeft size={32} />}
                                        </button>
                                        <span className={`text-sm font-semibold ${isLightCityTheme ? 'text-pink-700' : 'text-slate-300'}`}>{t('feature_translation_example_switch')}</span>
                                    </div>
                                    <button onClick={addExampleItem} 
                                        className="flex items-center gap-1 text-sm font-bold px-3 py-1.5 rounded transition-all hover:scale-105 active:scale-95"
                                        style={{ 
                                            backgroundColor: `${themeColor}20`, 
                                            color: isLightCityTheme ? '#ff4d6d' : themeColor,
                                            border: `1px solid ${themeColor}40`
                                        }}
                                    >
                                        <Plus size={16} /> {t('ui_rules_add')}
                                    </button>
                                </div>
                                <div className="grid gap-3">
                                    {translationExample.map((item, originalIdx) => {
                                        if (filter && !item.src?.includes(filter) && !item.dst?.includes(filter)) return null;
                                        return (
                                            <div key={originalIdx} className={`grid grid-cols-12 gap-3 p-3 border rounded-lg transition-colors group ${isLightCityTheme ? 'bg-white/60 border-pink-100 hover:border-pink-300' : 'bg-slate-900 border-slate-800 hover:border-slate-600'}`}>
                                                <div className="col-span-5"><textarea placeholder={t('ui_rules_example_src')} value={item.src} onChange={(e) => updateExampleItem(originalIdx, 'src', e.target.value)} className="w-full h-24 bg-slate-950 border border-slate-700 rounded p-2 text-green-300 focus:border-primary outline-none text-sm resize-none transition-all" /></div>
                                                <div className="col-span-1 flex items-center justify-center text-slate-600">→</div>
                                                <div className="col-span-5"><textarea placeholder={t('ui_rules_example_dst')} value={item.dst} onChange={(e) => updateExampleItem(originalIdx, 'dst', e.target.value)} className="w-full h-24 bg-slate-950 border border-slate-700 rounded p-2 text-blue-300 focus:border-primary outline-none text-sm resize-none transition-all" /></div>
                                                <div className="col-span-1 flex items-center justify-end"><button onClick={() => removeExampleItem(originalIdx)} className={`p-2 transition-colors rounded border ${isLightCityTheme ? 'text-pink-300 hover:text-red-500 bg-white/80 border-pink-100' : 'text-slate-600 hover:text-red-400 bg-slate-950 border-slate-800'}`}><Trash2 size={18} /></button></div>
                                            </div>
                                        );
                                    })}
                                </div>
                            </div>
                        )}

                        {activeTab === 'timeline' && renderTimelineView()}

                        {activeTab === 'ai_glossary' && (
                            <div className="space-y-4 animate-in fade-in slide-in-from-bottom-2 duration-300">
                                {showTermSelector ? (
                                    <TermSelector 
                                        terms={selectorTerms}
                                        onSaveAll={handleTermSaveAll}
                                        onSaveSingle={handleTermSaveSingle}
                                        onRetry={handleTermRetry}
                                        onCancel={() => setShowTermSelector(false)}
                                        themeColor={themeColor}
                                        isLightCityTheme={isLightCityTheme}
                                    />
                                ) : (
                                    <>
                                        {/* Warning */}
                                        <div className={`p-3 rounded-lg border ${isLightCityTheme ? 'bg-yellow-50 border-yellow-200 text-yellow-800' : 'bg-yellow-900/20 border-yellow-700/50 text-yellow-300'}`}>
                                            <div className="flex items-center gap-2">
                                                <AlertTriangle size={16} />
                                                <span className="text-sm">{t('ai_glossary_warning')}</span>
                                            </div>
                                        </div>

                                        {/* Input Controls */}
                                        <div className={`p-4 rounded-lg border ${isLightCityTheme ? 'bg-white/40 border-pink-200/50' : 'bg-slate-900/50 border-slate-800'}`}>
                                            <div className="mb-4">
                                                <label className={`text-sm font-medium ${isLightCityTheme ? 'text-pink-700' : 'text-slate-300'}`}>{t('ai_glossary_mode')}</label>
                                                <div className="mt-2 grid grid-cols-1 md:grid-cols-3 gap-2">
                                                    <button
                                                        type="button"
                                                        onClick={() => setAiAnalysisMode('full')}
                                                        className={`text-left px-3 py-2 rounded border transition-all ${aiAnalysisMode === 'full' ? 'font-bold' : 'text-slate-400 hover:text-slate-200'}`}
                                                        style={aiAnalysisMode === 'full' ? { backgroundColor: `${themeColor}20`, color: themeColor, borderColor: `${themeColor}60` } : { borderColor: 'rgb(51 65 85)' }}
                                                    >
                                                        <div className="text-sm">{t('ai_glossary_mode_full')}</div>
                                                        <div className="text-xs opacity-75 mt-1">{t('ai_glossary_mode_full_desc')}</div>
                                                    </button>
                                                    <button
                                                        type="button"
                                                        onClick={() => setAiAnalysisMode('split')}
                                                        className={`text-left px-3 py-2 rounded border transition-all ${aiAnalysisMode === 'split' ? 'font-bold' : 'text-slate-400 hover:text-slate-200'}`}
                                                        style={aiAnalysisMode === 'split' ? { backgroundColor: `${themeColor}20`, color: themeColor, borderColor: `${themeColor}60` } : { borderColor: 'rgb(51 65 85)' }}
                                                    >
                                                        <div className="text-sm">{t('ai_glossary_mode_split')}</div>
                                                        <div className="text-xs opacity-75 mt-1">{t('ai_glossary_mode_split_desc')}</div>
                                                    </button>
                                                    <button
                                                        type="button"
                                                        onClick={() => setAiAnalysisMode('incremental_split')}
                                                        className={`text-left px-3 py-2 rounded border transition-all ${aiAnalysisMode === 'incremental_split' ? 'font-bold' : 'text-slate-400 hover:text-slate-200'}`}
                                                        style={aiAnalysisMode === 'incremental_split' ? { backgroundColor: `${themeColor}20`, color: themeColor, borderColor: `${themeColor}60` } : { borderColor: 'rgb(51 65 85)' }}
                                                    >
                                                        <div className="text-sm">{t('ai_glossary_mode_incremental_split')}</div>
                                                        <div className="text-xs opacity-75 mt-1">{t('ai_glossary_mode_incremental_split_desc')}</div>
                                                    </button>
                                                </div>
                                            </div>
                                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                                <div>
                                                    <label className={`text-sm font-medium ${isLightCityTheme ? 'text-pink-700' : 'text-slate-300'}`}>{t('ai_glossary_input_path')}</label>
                                                    <input type="text" value={aiInputPath} onChange={(e) => setAiInputPath(e.target.value)}
                                                        className="w-full mt-1 px-3 py-2 bg-slate-950 border border-slate-700 rounded text-sm text-slate-200 focus:border-primary outline-none transition-all"
                                                        placeholder="C:/path/to/file.epub" />
                                                </div>
                                                <div className="grid grid-cols-2 gap-2">
                                                    <div>
                                                        <label className={`text-sm font-medium ${isLightCityTheme ? 'text-pink-700' : 'text-slate-300'}`}>{t('ai_glossary_percent')}</label>
                                                        <input type="number" value={aiPercent} onChange={(e) => setAiPercent(Number(e.target.value))}
                                                            className="w-full mt-1 px-3 py-2 bg-slate-950 border border-slate-700 rounded text-sm text-slate-200 focus:border-primary outline-none transition-all" min={1} max={100} />
                                                    </div>
                                                    <div>
                                                        <label className={`text-sm font-medium ${isLightCityTheme ? 'text-pink-700' : 'text-slate-300'}`}>{t('ai_glossary_lines')}</label>
                                                        <input type="number" value={aiLines || ''} onChange={(e) => setAiLines(e.target.value ? Number(e.target.value) : undefined)}
                                                            disabled={aiAnalysisMode !== 'split'}
                                                            className="w-full mt-1 px-3 py-2 bg-slate-950 border border-slate-700 rounded text-sm text-slate-200 focus:border-primary outline-none transition-all disabled:opacity-50" placeholder={aiAnalysisMode === 'split' ? '可选' : t('ai_glossary_lines_split_only')} />
                                                    </div>
                                                </div>
                                            </div>
                                            {(aiAnalysisMode === 'full' || aiAnalysisMode === 'incremental_split') && (
                                                <div className="mt-4">
                                                    <label className={`text-sm font-medium ${isLightCityTheme ? 'text-pink-700' : 'text-slate-300'}`}>{t('ai_glossary_incremental_split_target_tokens')}</label>
                                                    <input type="number" value={aiIncrementalSplitTargetTokens} onChange={(e) => setAiIncrementalSplitTargetTokens(Math.min(256000, Math.max(1000, Number(e.target.value) || 200000)))}
                                                        className="w-full mt-1 px-3 py-2 bg-slate-950 border border-slate-700 rounded text-sm text-slate-200 focus:border-primary outline-none transition-all" min={1000} max={256000} />
                                                    <p className="mt-1 text-xs text-slate-500">{t('ai_glossary_incremental_split_hint')}</p>
                                                </div>
                                            )}
                                            <div className="mt-4">
                                                <label className={`text-sm font-medium ${isLightCityTheme ? 'text-pink-700' : 'text-slate-300'}`}>{t('ai_glossary_prompt_file')}</label>
                                                <input type="text" value={aiPromptFile} onChange={(e) => setAiPromptFile(e.target.value)}
                                                    className="w-full mt-1 px-3 py-2 bg-slate-950 border border-slate-700 rounded text-sm text-slate-200 focus:border-primary outline-none transition-all"
                                                    placeholder={t('ai_glossary_prompt_file_placeholder')} />
                                                <p className="mt-1 text-xs text-slate-500">{t('ai_glossary_prompt_file_hint')}</p>
                                            </div>

                                            {/* API Config Selection */}
                                            <div className="mt-4 pt-4 border-t border-slate-700">
                                                <div className="flex items-center gap-3 mb-3">
                                                    <label className={`text-sm font-medium ${isLightCityTheme ? 'text-pink-700' : 'text-slate-300'}`}>
                                                        {t('ai_glossary_api_config')}:
                                                    </label>
                                                    <button
                                                        onClick={() => setAiUseTempConfig(false)}
                                                        className={`px-3 py-1 text-sm rounded transition-all ${!aiUseTempConfig
                                                            ? 'font-bold' : 'text-slate-400 hover:text-slate-200'}`}
                                                        style={!aiUseTempConfig ? { backgroundColor: `${themeColor}20`, color: themeColor, border: `1px solid ${themeColor}40` } : { border: '1px solid transparent' }}
                                                    >
                                                        {t('ai_glossary_use_current')}
                                                    </button>
                                                    <button
                                                        onClick={() => setAiUseTempConfig(true)}
                                                        className={`px-3 py-1 text-sm rounded transition-all ${aiUseTempConfig
                                                            ? 'font-bold' : 'text-slate-400 hover:text-slate-200'}`}
                                                        style={aiUseTempConfig ? { backgroundColor: `${themeColor}20`, color: themeColor, border: `1px solid ${themeColor}40` } : { border: '1px solid transparent' }}
                                                    >
                                                        {t('ai_glossary_use_temp')}
                                                    </button>
                                                </div>

                                                {aiUseTempConfig && (
                                                    <div className="grid grid-cols-1 md:grid-cols-3 gap-3 p-3 bg-slate-800/50 rounded-lg border border-slate-700">
                                                        <div>
                                                            <label className="text-xs text-slate-400">{t('ai_glossary_temp_platform')}</label>
                                                            <input type="text" value={aiTempPlatform} onChange={(e) => setAiTempPlatform(e.target.value)}
                                                                className="w-full mt-1 px-2 py-1.5 bg-slate-950 border border-slate-700 rounded text-sm text-slate-200 outline-none"
                                                                placeholder="openai / anthropic / google" />
                                                        </div>
                                                        <div>
                                                            <label className="text-xs text-slate-400">{t('ai_glossary_temp_model')}</label>
                                                            <input type="text" value={aiTempModel} onChange={(e) => setAiTempModel(e.target.value)}
                                                                className="w-full mt-1 px-2 py-1.5 bg-slate-950 border border-slate-700 rounded text-sm text-slate-200 outline-none"
                                                                placeholder="gpt-4o / claude-3-5-sonnet" />
                                                        </div>
                                                        <div>
                                                            <label className="text-xs text-slate-400">{t('ai_glossary_temp_threads')}</label>
                                                            <input type="number" value={aiTempThreads} onChange={(e) => setAiTempThreads(Number(e.target.value))}
                                                                className="w-full mt-1 px-2 py-1.5 bg-slate-950 border border-slate-700 rounded text-sm text-slate-200 outline-none"
                                                                min={1} max={100} placeholder="5" />
                                                        </div>
                                                        <div>
                                                            <label className="text-xs text-slate-400">{t('ai_glossary_temp_url')}</label>
                                                            <input type="text" value={aiTempApiUrl} onChange={(e) => setAiTempApiUrl(e.target.value)}
                                                                className="w-full mt-1 px-2 py-1.5 bg-slate-950 border border-slate-700 rounded text-sm text-slate-200 outline-none"
                                                                placeholder="https://api.openai.com" />
                                                        </div>
                                                        <div className="md:col-span-2">
                                                            <label className="text-xs text-slate-400">{t('ai_glossary_temp_key')}</label>
                                                            <input type="password" value={aiTempApiKey} onChange={(e) => setAiTempApiKey(e.target.value)}
                                                                className="w-full mt-1 px-2 py-1.5 bg-slate-950 border border-slate-700 rounded text-sm text-slate-200 outline-none"
                                                                placeholder="sk-..." />
                                                        </div>
                                                    </div>
                                                )}
                                            </div>

                                            <div className="flex gap-2 mt-4">
                                                <button onClick={startAiAnalysis} disabled={aiStatus.status === 'running'}
                                                    className="flex items-center gap-2 px-4 py-2 rounded font-bold transition-all hover:scale-105 disabled:opacity-50"
                                                    style={{ backgroundColor: `${themeColor}20`, color: themeColor, border: `1px solid ${themeColor}40` }}>
                                                    <Play size={16} /> {t('ai_glossary_start')}
                                                </button>
                                                <button onClick={stopAiAnalysis} disabled={aiStatus.status !== 'running'}
                                                    className="flex items-center gap-2 px-4 py-2 rounded font-bold transition-all hover:scale-105 disabled:opacity-50 bg-red-900/20 text-red-400 border border-red-700/50">
                                                    <Square size={16} /> {t('ai_glossary_stop')}
                                                </button>
                                            </div>
                                        </div>

                                        {/* Status & Logs */}
                                        <div className={`p-4 rounded-lg border ${isLightCityTheme ? 'bg-white/40 border-pink-200/50' : 'bg-slate-900/50 border-slate-800'}`}>
                                            <div className="flex items-center justify-between mb-2">
                                                <span className={`text-sm font-medium ${isLightCityTheme ? 'text-pink-700' : 'text-slate-300'}`}>{t('ai_glossary_status')}: {aiStatus.message || t(`ai_glossary_${aiStatus.status}`)}</span>
                                                {aiStatus.total > 0 && <span className="text-sm text-slate-400">{aiStatus.progress}/{aiStatus.total}</span>}
                                            </div>
                                            {aiStatus.total > 0 && (
                                                <div className="w-full h-2 bg-slate-800 rounded-full overflow-hidden">
                                                    <div className="h-full transition-all" style={{ width: `${(aiStatus.progress / aiStatus.total) * 100}%`, backgroundColor: themeColor }} />
                                                </div>
                                            )}
                                            <div className="mt-3 h-32 overflow-y-auto bg-slate-950 rounded p-2 text-xs font-mono text-slate-400">
                                                {aiLogs.map((log, i) => <div key={i}>{log}</div>)}
                                            </div>
                                        </div>

                                        {/* Results Table */}
                                        {aiStatus.status === 'completed' && aiStatus.results.length > 0 && (
                                            <div className={`p-4 rounded-lg border ${isLightCityTheme ? 'bg-white/40 border-pink-200/50' : 'bg-slate-900/50 border-slate-800'}`}>
                                                <div className="flex items-center justify-between mb-3">
                                                    <span className={`text-sm font-medium ${isLightCityTheme ? 'text-pink-700' : 'text-slate-300'}`}>
                                                        {t('ai_glossary_results')} ({aiStatus.results.length})
                                                    </span>
                                                    <div className="flex items-center gap-2">
                                                        <label className="text-xs text-slate-400">{t('ai_glossary_min_freq')}:</label>
                                                        <input type="number" value={aiMinFreq} onChange={(e) => setAiMinFreq(Number(e.target.value))}
                                                            className="w-16 px-2 py-1 bg-slate-950 border border-slate-700 rounded text-sm text-slate-200 outline-none" min={1} />
                                                        <input type="text" value={aiFilename} onChange={(e) => setAiFilename(e.target.value)}
                                                            className="w-32 px-2 py-1 bg-slate-950 border border-slate-700 rounded text-sm text-slate-200 outline-none" placeholder="filename" />
                                                        <button onClick={saveAiResults}
                                                            className="flex items-center gap-1 px-3 py-1 rounded font-bold text-sm transition-all hover:scale-105"
                                                            style={{ backgroundColor: `${themeColor}20`, color: themeColor, border: `1px solid ${themeColor}40` }}>
                                                            <Save size={14} /> {t('ai_glossary_save')}
                                                        </button>
                                                        <button onClick={enterTermSelection}
                                                            className="flex items-center gap-1 px-3 py-1 rounded font-bold text-sm transition-all hover:scale-105 bg-primary/20 text-primary border border-primary/40">
                                                            <Sparkles size={14} /> 选择翻译
                                                        </button>
                                                    </div>
                                                </div>
                                                <div className="max-h-64 overflow-y-auto">
                                                    <table className="w-full text-sm">
                                                        <thead className="sticky top-0 bg-slate-900">
                                                            <tr className="text-slate-400 text-left">
                                                                <th className="p-2">{t('ai_glossary_term')}</th>
                                                                <th className="p-2">{t('ai_glossary_type')}</th>
                                                                <th className="p-2">{t('ai_glossary_info')}</th>
                                                                <th className="p-2 text-right">{t('ai_glossary_count')}</th>
                                                            </tr>
                                                        </thead>
                                                        <tbody>
                                                            {aiStatus.results.filter((r: any) => r.count >= aiMinFreq).map((r: any, i: number) => (
                                                                <tr key={i} className="border-t border-slate-800 hover:bg-slate-800/50">
                                                                    <td className="p-2 text-cyan-300">{r.src}</td>
                                                                    <td className="p-2 text-slate-400">{r.type}</td>
                                                                    <td className="p-2 text-slate-400">{r.info || 'null'}</td>
                                                                    <td className="p-2 text-right text-yellow-400">{r.count}</td>
                                                                </tr>
                                                            ))}
                                                        </tbody>
                                                    </table>
                                                </div>
                                            </div>
                                        )}
                                    </>
                                )}
                            </div>
                        )}
                    </>
                )}
            </div>
        </div>
    );
};
