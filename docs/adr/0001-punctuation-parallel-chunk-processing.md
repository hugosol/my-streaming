# 标点 chunk 并行处理，失败即停

标点阶段原本在 `worker.py` 中逐 chunk 串行调用 DeepSeek。现在改为 `ThreadPoolExecutor` 并行处理，线程数由 `punctuation.thread_num` 配置（默认 4）。与翻译阶段“全部跑完再汇总失败”不同，标点阶段在第一个 chunk 失败后立即取消排队任务并失败，因为 `srt_marker.py finalize --from-chunks` 要求所有 `chunk_NNN_punctuated.txt` 都存在，缺少一个整个阶段就无法合并。
