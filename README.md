重构PubChem 批量标识符检索模块

背景：DES原始数据集中化合物名称不统一，去重困难。因此借助PubChem数据库，为每个化合物补充规范的标识符（smile、uname、cas）。
问题：PubChem数据库接口不稳定，导致大量标识获取失败。
目的：重构标识符检索模块，提升ids_map-tb数据集的完整性。

核心机制更新
1. 两阶段补漏重试 (Retry-on-Miss)
初次全量检索：正常执行批量并发请求。遇到请求失败或数据缺失（None）时，不中断主进程，直接记录至失败列表。

全局冷静期：在一轮检索结束后，若存在失败记录，主线程强制休眠 10 秒，缓解 PubChem 服务端并发压力。

缓存清理与降速回炉：二轮重试前，调用 f_fetch_compound_info.cache_clear() 清空失效的 LRU 缓存。随后以单点降速的方式重新发起网络请求，避免读取错误缓存。

2. 细化 API 异常捕获与容错
网络异常拦截：在请求 CAS 节点（get_cas_directly_from_pubchem）时，新增对 socket.timeout、HTTPError、URLError 及 json.JSONDecodeError 的显式捕获与处理。

JSON 解析容错：针对深层嵌套的节点提取，引入对 KeyError、IndexError、TypeError 的宽容处理，防止因远端节点数据残缺导致脚本崩溃。

3. 逻辑优化
重构字段调度：将动态的 missing_fields 变更为固定的 target_fields (['smiles', 'uname', 'cas'])，统一执行全量字段检索。

日志打印优化：retry_on_failure 装饰器更新为打印具体的异常类型（type(e).__name__），替代笼统的网络错误提示。

终端可视化：主循环中新增 CLI 进度条，实时显示总体进度百分比与当前处理的目标化合物。

4. 开发者体验优化
增加了进度条，方便看进度。

测试结果
机制实测有效：ids_map_tb 数据集的字段完整度从 80% 提升至 95%。
