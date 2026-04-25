# HomeMaster V1.2 Stage 03 Memory RAG

## Summary

Stage 03 已落地 object_memory-only RAG 检索链路：Mimo 从 `TaskCard` 生成
`MemoryRetrievalQuery`，程序执行 `jieba + BM25` lexical retrieval 与 BGE-M3
`/v1/embeddings` dense retrieval，融合排序后输出 `MemoryRetrievalResult`。

本阶段不引入 reranker，不生成 `GroundedMemoryTarget`，不接旧 `task_brain`。

## Results

- 工程测试：`30 passed`
- Live Stage 03：`5 passed`
- Ruff：通过
- 依赖：`bm25s`、`jieba` 已加入 `pyproject.toml`，并在项目 `.venv` 中验证可导入。

## Live Cases

- `mimo_memory_retrieval_query`：top hit `mem-cup-1`
- `cup_object_memory_rag`：top hit `mem-cup-1`
- `medicine_object_memory_rag`：top hit `mem-medicine-1`
- `negative_evidence_excludes_location`：`mem-cup-1` 进入 excluded，top hit `mem-cup-2`
- `reranker_not_required_stage_03`：ranking stage 为 `bm25_dense_fusion`，reranker 字段为空

## Artifacts

- Debug cases: `tests/homemaster/llm_cases/stage_03/`
- Raw logs and trace: `plan/V1.2/test_results/stage_03/`
- Embedding cache: `.cache/homemaster/embeddings/`

## Notes

- Mimo 负责 query 语义构造；程序只做 schema/source/top_k/negative evidence 边界校验。
- BGE-M3 通过 embeddings endpoint 调用，不复用 chat/messages client。
- Debug 资产已扫描常见 secret 关键词，未发现 API key、Authorization 或 Bearer token。
