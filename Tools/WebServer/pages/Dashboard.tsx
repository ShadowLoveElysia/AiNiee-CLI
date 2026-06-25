import React, { useEffect, useState } from 'react';
import { Play, Settings, FileOutput, ArrowRight, FileText, Folder } from 'lucide-react';
import { useI18n } from '../contexts/I18nContext';
import { useGlobal } from '../contexts/GlobalContext';
import { DataService } from '../services/DataService';

export const Dashboard: React.FC = () => {
  const { t } = useI18n();
  const { version, config, setTaskState, activeTheme } = useGlobal(); // Use global state
  const [recentProjects, setRecentProjects] = useState<string[]>([]);
  
  const elysiaActive = activeTheme === 'elysia';

  const getCharacterBlessing = () => {
    switch(activeTheme) {
      case 'elysia': return "嗨~ 想我了吗？无论结果如何，努力的你才是最美的哦，快让我看看你的成果吧~";
      case 'eden': return "欢迎来到这片黄金的港湾。在这里，文字不只是符号，它们是流淌的乐章，是永不褪色的艺术。愿这些杰作因你而生.";
      case 'mobius': return "哦？这就是你选择的‘进化’之路吗？很有趣。来吧，让我看看你会如何重塑这些平庸的文字……哪怕是失败，也是通往无限的基石.";
      case 'pardofelis': return "哎呀，大老板你来啦！罐头，快打个招呼~ 既然来了就别急着走嘛，咱家这儿多得是好宝贝，一定能帮你把活儿干得漂漂亮亮的！";
      case 'griseo': return "你在看这幅画吗？我正在为它填色。这些文字……它们有着很温柔的颜色。请坐下，和我一起慢慢描绘它们吧.";
      case 'kevin': return "无论前方的道路多么严寒，拯救的使命永远不会熄灭。既然背负了这份沉重，就请拼尽全力，将这些文字转化为对抗‘终焉’的力量.";
      case 'kalpas': return "哈！你还在磨蹭什么？破坏！或者创造！只要能点燃我的怒火，怎样都好！滚去工作，用这些文字撕碎所有的无趣！";
      case 'aponia': return "我已预见到了终局……但在那之前，请允许我为你祈祷。愿这些文字不再沉重，愿你的心不再迷茫。";
      case 'villv': return "这是天才的杰作，还是疯子的妄想？没关系，在我的舞台上，所有文字都将服从逻辑的戏法。让我们开始这场华丽的变革吧！";
      case 'su': return "在这一叶一世界中，文字的流动亦是因果的循环。请静下心来，感受那些隐藏在字里行间的真实。";
      case 'sakura': return "瞬身之意，止于须臾。这些文字如落樱般轻盈，却也承载着斩断过去的觉悟。请珍惜它们。";
      case 'kosma': return "黑夜终会吞噬一切……但在光亮消失前，我守在这里。请继续你的工作，我会挡住那些阴影。";
      case 'hua': return "武道与文道，皆在于精诚。每一份对文字的雕琢，都是对自我的磨砺。请务必保持这份专注。";
      case 'herrscher_of_human': return "欢迎来到这永恒的奇迹。在这里，每一个字都是爱的证明，每一段旅程都是光的回响。作为‘人之律者’，我将守护你笔下所有的美好。";
      default: return null;
    }
  };

  const getCharacterGreeting = () => {
    switch(activeTheme) {
      case 'elysia': return "嗨~ 想我了吗？";
      case 'eden': return "欢迎回来，我的挚友。";
      case 'mobius': return "实验又要开始了。";
      case 'pardofelis': return "欢迎光临，大老板！";
      case 'griseo': return "你来了……";
      case 'kevin': return "欢迎回来。";
      case 'kalpas': return "哼，你终于滚回来了！";
      case 'aponia': return "愿你能得到救赎。";
      case 'villv': return "欢迎来到魔术表演现场！";
      case 'su': return "心如止水，方见真意。";
      case 'sakura': return "樱花散落之时，亦是归家之日。";
      case 'kosma': return "……你，不该靠近这里。";
      case 'hua': return "寸心拳致，不负此生。";
      case 'herrscher_of_human': return "我是一切，也是始终。";
      default: return null;
    }
  };

  const characterGreeting = getCharacterGreeting();
  const characterQuote = getCharacterBlessing();
  
  useEffect(() => {
    // Update local recent projects list when config loads
    if (config) {
        // recent_projects is now a list of objects: {path, profile, rules_profile}
        setRecentProjects([...(config.recent_projects || [])]);
    }
  }, [config]);

  const navigate = (path: string) => {
    window.location.hash = path;
  };

  const handleResume = async (project: any) => {
      const path = typeof project === 'string' ? project : project.path;
      const profile = typeof project === 'object' ? project.profile : null;
      const rulesProfile = typeof project === 'object' ? project.rules_profile : null;

      try {
          // 1. Auto-switch Profiles if they differ from current
          if (profile && profile !== config?.active_profile) {
              await DataService.switchProfile(profile);
          }
          if (rulesProfile && rulesProfile !== config?.active_rules_profile) {
              await DataService.switchRulesProfile(rulesProfile);
          }

          // 2. Set Task State
          setTaskState(prev => ({
              ...prev, 
              customInputPath: path,
              isResuming: true,
          }));

          // 3. Navigate
          navigate('/task');
      } catch (error) {
          console.error("Failed to resume project with specific profiles", error);
          // Fallback to simple resume
          setTaskState(prev => ({ ...prev, customInputPath: path, isResuming: true }));
          navigate('/task');
      }
  };

  // Helper to parse path string into displayable info
  const getProjectInfo = (path: string) => {
      // Handle Windows backslashes or Unix forward slashes
      const normalized = path.replace(/\\/g, '/');
      const parts = normalized.split('/');
      const name = parts.pop() || path;
      
      // Guess type from extension
      let type = "FOLDER";
      let icon = <Folder size={16} className="text-blue-400" />;
      
      if (name.includes('.')) {
          const ext = name.split('.').pop()?.toUpperCase();
          type = ext || "FILE";
          icon = <FileText size={16} className="text-slate-400" />;
      }

      return { name, type, icon, fullPath: path };
  };

  return (
    <div className="space-y-6 md:space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-500">
      <div className="flex flex-col md:flex-row justify-between items-start md:items-end gap-4">
        <div>
          <h1 className="text-2xl md:text-3xl font-bold text-white tracking-tight">
            {characterGreeting || t('ui_dashboard_welcome')}
          </h1>
          <p className="text-slate-400 mt-1 md:mt-2 text-sm md:text-base max-w-xl">
            {characterQuote || t('ui_dashboard_subtitle')}
          </p>
        </div>
        <div className="flex gap-2">
           <span className={`px-3 py-1 rounded-full border text-[10px] md:text-xs font-medium whitespace-nowrap transition-all duration-500 ${
             activeTheme === 'herrscher_of_human' 
               ? 'bg-pink-400/20 text-pink-500 border-pink-400/40 shadow-[0_0_15px_rgba(255,143,163,0.3)]' 
               : (elysiaActive ? 'bg-pink-500/10 text-pink-400 border-pink-500/30' : 'bg-accent/10 text-accent border-accent/20')
           }`}>
             {activeTheme === 'herrscher_of_human' ? '人之律者 · 爱与希望' : (elysiaActive ? '完美无瑕' : t('ui_system_online'))}
           </span>
           <span className={`px-3 py-1 rounded-full border text-[10px] md:text-xs font-medium whitespace-nowrap transition-all duration-500 ${
             activeTheme === 'herrscher_of_human'
               ? 'bg-purple-400/20 text-purple-500 border-purple-400/40'
               : (elysiaActive ? 'bg-purple-500/10 text-purple-400 border-purple-500/30' : 'bg-primary/10 text-primary border-primary/20')
           }`}>
             {activeTheme === 'herrscher_of_human' ? `∞ ${version}` : version}
           </span>
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4 md:gap-6">
        <div onClick={() => navigate('/task')} className="cursor-pointer group relative overflow-hidden bg-gradient-to-br from-indigo-500/10 to-purple-500/10 border border-slate-700 hover:border-indigo-500/50 p-6 rounded-2xl transition-all duration-300 hover:shadow-lg hover:shadow-indigo-500/10">
          <div className="absolute top-0 right-0 p-4 opacity-10 group-hover:opacity-20 transition-opacity">
            <Play size={80} className="md:w-[120px] md:h-[120px]" />
          </div>
          <div className="relative z-10">
            {elysiaActive && (
              <div className="absolute -top-2 -right-2 px-2 py-0.5 bg-pink-500 text-[8px] font-bold text-white rounded-full shadow-lg shadow-pink-500/30 animate-pulse">
                MAGIC
              </div>
            )}
            <div className="w-10 h-10 md:w-12 md:h-12 rounded-lg bg-indigo-500/20 flex items-center justify-center text-indigo-400 mb-4 group-hover:scale-110 transition-transform">
              <Play size={20} md:size={24} fill="currentColor" />
            </div>
            <h3 className="text-lg md:text-xl font-semibold text-white mb-2">{t('menu_start_translation')}</h3>
            <p className="text-slate-400 text-xs md:text-sm mb-6">{t('ui_new_task_desc')}</p>
            <div className="flex items-center text-indigo-400 text-sm font-medium">
              {t('ui_btn_start')} <ArrowRight size={16} className="ml-2 group-hover:translate-x-1 transition-transform" />
            </div>
          </div>
        </div>

        <div onClick={() => navigate('/settings')} className="cursor-pointer group relative overflow-hidden bg-gradient-to-br from-cyan-500/10 to-blue-500/10 border border-slate-700 hover:border-cyan-500/50 p-6 rounded-2xl transition-all duration-300 hover:shadow-lg hover:shadow-cyan-500/10">
          <div className="absolute top-0 right-0 p-4 opacity-10 group-hover:opacity-20 transition-opacity">
            <Settings size={80} className="md:w-[120px] md:h-[120px]" />
          </div>
          <div className="relative z-10">
            <div className="w-10 h-10 md:w-12 md:h-12 rounded-lg bg-cyan-500/20 flex items-center justify-center text-cyan-400 mb-4 group-hover:scale-110 transition-transform">
              <Settings size={20} md:size={24} />
            </div>
            <h3 className="text-lg md:text-xl font-semibold text-white mb-2">{t('menu_settings')}</h3>
            <p className="text-slate-400 text-xs md:text-sm mb-6">{t('ui_config_desc')}</p>
            <div className="flex items-center text-cyan-400 text-sm font-medium">
              {t('ui_btn_edit')} <ArrowRight size={16} className="ml-2 group-hover:translate-x-2 transition-transform" />
            </div>
          </div>
        </div>

        <div onClick={() => navigate('/export')} className="cursor-pointer group relative overflow-hidden bg-gradient-to-br from-emerald-500/10 to-teal-500/10 border border-slate-700 hover:border-emerald-500/50 p-6 rounded-2xl transition-all duration-300 hover:shadow-lg hover:shadow-emerald-500/10">
          <div className="absolute top-0 right-0 p-4 opacity-10 group-hover:opacity-20 transition-opacity">
            <FileOutput size={80} className="md:w-[120px] md:h-[120px]" />
          </div>
          <div className="relative z-10">
            <div className="w-10 h-10 md:w-12 md:h-12 rounded-lg bg-emerald-500/20 flex items-center justify-center text-emerald-400 mb-4 group-hover:scale-110 transition-transform">
              <FileOutput size={20} md:size={24} />
            </div>
            <h3 className="text-lg md:text-xl font-semibold text-white mb-2">{t('menu_export_only')}</h3>
            <p className="text-slate-400 text-xs md:text-sm mb-6">{t('ui_history_desc')}</p>
            <div className="flex items-center text-emerald-400 text-sm font-medium">
              {t('ui_btn_history')} <ArrowRight size={16} className="ml-2 group-hover:translate-x-1 transition-transform" />
            </div>
          </div>
        </div>
      </div>

      <div className="bg-surface border border-slate-800 rounded-xl p-4 md:p-6 min-h-[300px]">
        <h2 className="text-lg font-semibold text-white mb-4">{t('menu_recent_projects')}</h2>
        
        {!config ? (
             <div className="text-slate-500 text-sm animate-pulse py-4">Loading configuration...</div>
        ) : recentProjects.length === 0 ? (
             <div className="text-slate-500 text-sm italic py-4">No recent projects found in config.</div>
        ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="text-slate-500 text-xs uppercase border-b border-slate-800">
                    <th className="pb-3 pl-2">{t('ui_recent_proj_header')}</th>
                    <th className="pb-3">{t('ui_recent_type')}</th>
                    <th className="pb-3 hidden md:table-cell">{t('label_input')}</th>
                    <th className="pb-3 text-right pr-2">{t('ui_recent_action')}</th>
                  </tr>
                </thead>
                                <tbody className="text-sm">
                                  {recentProjects.map((project, i) => {
                                    const path = typeof project === 'string' ? project : project.path;
                                    const info = getProjectInfo(path);
                                    const profileStr = typeof project === 'object' ? `${project.profile} / ${project.rules_profile}` : 'legacy';
                
                                    return (
                                        <tr key={i} className={`border-b border-slate-800/50 transition-all duration-300 group ${elysiaActive ? 'hover:bg-pink-500/5 hover:translate-x-2' : 'hover:bg-slate-800/50'}`}
                                            style={elysiaActive ? { animation: `elysia-entrance 0.5s var(--elysia-spring) both ${0.4 + i * 0.05}s` } : {}}>
                                          <td className="py-3 pl-2 font-medium text-slate-200 flex items-center gap-3">
                                              {info.icon}
                                              <div className="flex flex-col">
                                                <span>{info.name}</span>
                                                <span className="text-[10px] text-slate-500 md:hidden">{profileStr}</span>
                                              </div>
                                          </td>
                                          <td className="py-3 text-slate-400">
                                              <div className="flex flex-col gap-1">
                                                <span className="px-2 py-0.5 rounded bg-slate-800 border border-slate-700 text-xs w-fit">
                                                    {info.type}
                                                </span>
                                                <span className="text-[10px] text-slate-500 hidden md:block">{profileStr}</span>
                                              </div>
                                          </td>
                                          <td className="py-3 text-slate-500 text-xs font-mono hidden md:table-cell truncate max-w-[200px]" title={info.fullPath}>
                                              {info.fullPath}
                                          </td>
                                          <td className="py-3 text-right pr-2">
                                            <button 
                                                onClick={() => handleResume(project)} 
                                                className="text-primary hover:text-cyan-300 text-xs font-medium px-3 py-1 bg-primary/10 rounded border border-primary/20 hover:bg-primary/20 transition-all"
                                            >
                                                {t('ui_resume')}
                                            </button>
                                          </td>
                                        </tr>
                                    );
                                  })}
                                </tbody>
              </table>
            </div>
        )}
      </div>
    </div>
  );
};
