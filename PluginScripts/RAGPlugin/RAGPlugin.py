import os
import re
from typing import List, Dict
from PluginScripts.PluginBase import PluginBase, Priority
from ModuleFolders.Infrastructure.Cache.CacheProject import CacheProject
from ModuleFolders.Infrastructure.Cache.CacheItem import TranslationStatus
from ModuleFolders.Infrastructure.TaskConfig.TaskConfig import TaskConfig

class RAGPlugin(PluginBase):
    def __init__(self) -> None:
        super().__init__()
        self.name = "RAG Context Plugin"
        self.description = "Provides long-context consistency using RAG (keyword-based retrieval)."
        self.visibility = True
        self.default_enable = False # 默认关闭，由用户在设置中开启
        
        # 注册事件
        # 1. 预处理，用于构建全局索引
        self.add_event("preproces_text", Priority.NORMAL)
        # 2. 为每个翻译任务构建特定的 RAG 上下文 (由 TranslatorTask 触发)
        self.add_event("build_rag_context", Priority.NORMAL)
        # 3. 翻译完成后，如果用户手动点“继续翻译”，我们可以标记索引过期
        self.add_event("translation_completed", Priority.NORMAL)

    def load(self) -> None:
        # 初始化索引存储 (在内存中)
        self.index = [] 
        self.is_indexed = False
        self.project_cache = None

    def on_event(self, event: str, config: TaskConfig, event_data) -> None:
        if event == "preproces_text":
            # event_data 是 CacheProject
            self.project_cache = event_data
            self._build_index(event_data)
        elif event == "build_rag_context":
            # event_data 是 dict: {"source_text_dict": ..., "rag_context": ""}
            self._handle_build_rag_context(config, event_data)
        elif event == "translation_completed":
            self.is_indexed = False

    def _handle_build_rag_context(self, config: TaskConfig, data: dict) -> None:
        """为当前任务块寻找最相关的上下文"""
        # 如果索引还没建立，且我们有项目句柄，则建立
        if not self.is_indexed and self.project_cache:
            self._build_index(self.project_cache)

        if not self.index:
            return

        source_text_dict = data.get("source_text_dict", {})
        # 合并当前块的所有文本用于搜索，或者只取第一行？为了效率取全量关键词
        combined_text = "\n".join(source_text_dict.values())
        
        relevant_entries = self.retrieve_context(combined_text, top_k=5)
        
        if not relevant_entries:
            return

        # 构建上下文提示词字符串
        rag_lines = []
        for entry in relevant_entries:
            rag_lines.append(f"Original: {entry['src']}\nTranslation: {entry['dst']}")
        
        rag_context_str = "\n---\n".join(rag_lines)
        data["rag_context"] = rag_context_str

    def _build_index(self, project: CacheProject):
        """构建简单的关键词索引"""
        self.index = []
        # 只索引已翻译或已润色的条目
        for item in project.items_iter():
            if item.translation_status in (TranslationStatus.TRANSLATED, TranslationStatus.POLISHED):
                if item.source_text and item.final_text:
                    self.index.append({
                        "src": item.source_text,
                        "dst": item.final_text,
                        "keywords": self._extract_keywords(item.source_text)
                    })
        self.is_indexed = True

    def _extract_keywords(self, text: str) -> set:
        """简单的关键词提取 (排除常见短词)"""
        # 匹配 2 个字符及以上的词
        words = re.findall(r'[\u4e00-\u9fa5]{2,}|[a-zA-Z]{3,}|[\u3041-\u3096\u30a0-\u30ff\u4e00-\u9faf]{2,}', text)
        return set(words)

    def retrieve_context(self, current_source_text: str, top_k: int = 5) -> List[Dict]:
        """检索最相关的上下文"""
        if not self.index:
            return []
            
        current_keywords = self._extract_keywords(current_source_text)
        if not current_keywords:
            return []
            
        scored_results = []
        for entry in self.index:
            # 计算关键词交集得分
            score = len(current_keywords.intersection(entry["keywords"]))
            if score > 0:
                # 还可以根据关键词在当前文本中的稀有度加权，但目前保持简单
                scored_results.append((score, entry))
        
        # 按得分降序排序
        scored_results.sort(key=lambda x: x[0], reverse=True)
        
        # 去重：如果同一个原文出现了多次（比如对话），只取一个
        unique_results = []
        seen_src = set()
        for _, entry in scored_results:
            if entry['src'] not in seen_src:
                unique_results.append(entry)
                seen_src.add(entry['src'])
                if len(unique_results) >= top_k:
                    break
        
        return unique_results