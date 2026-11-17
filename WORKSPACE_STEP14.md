# V24 · 本地智能分析与跨电脑启动

## 本次改动

- 新增 `/ai`，统一五项主导航，首页增加智能分析入口；保留四章开场和真实Blender资产。
- 固定本机Ollama API，提供安装入口、两种DeepSeek模型准备、下载状态、手动分析和可选持续分析；不自动使用云端模型。
- Python从实时60模拟秒采样或完整已完成Simulink记录计算指标、时间窗电量与规则证据。DeepSeek只生成解释，要求JSON结构和有效证据引用，结果与来源窗口一起保存。
- 同时仅一个模型准备/分析任务，提交有幂等标识；重启保留已完成结果，中断任务不自动重跑，失败不填充模拟AI答案。
- Windows双击入口从包内解压官方Python 3.13.17 x64，使用随包哈希锁定轮子离线准备环境；桌面快捷方式指向启动脚本，支持移动目录后重建。
- 同项目重复点击复用已有实例，端口冲突尝试8766–8785；启动失败保留窗口与尽可能写入日志；统一储能安装旧入口。
- 默认仍本机监听。显式局域网模式才绑定所有网卡，私有网络访客需临时令牌Cookie，只读访问；禁止访客POST/PUT/DELETE和无授权WebSocket，不自动改防火墙。
- 服务生命周期改用FastAPI lifespan，避免旧on_event弃用信息干扰启动日志。

## 已完成验证

1. `python test_local_ai.py`：7项测试通过。有限数值/严格时钟/积分；本地HTTP Ollama协议夹具；无效JSON、无效证据、截断返回拒绝；模型下载最终确认；单任务、幂等、失败、持久化与重启中断；真实路由live/po/inc样本数和状态隔离；来源限制与只读LAN Cookie/WebSocket；便携包CRC、SHA及x64 PE。
2. Windows依赖：CPython嵌入ZIP和pip轮子与官方SHA一致；Windows AMD64 / CPython3.13环境下解析包METADATA依赖闭包（包括Windows专用colorama），离线 `pip --dry-run --ignore-installed --require-hashes --no-index`成功。锁定版本和哈希见portable目录；轮子为原始分发件，许可证保留。
3. `python test_showcase.py`：源SLX、Blender、GLB、完成任务证据、资源包CRC/逐文件哈希和保留路由通过。
4. `python test_server_wire.py`：实际localhost HTTP及两个WebSocket、回放、实时推进/暂停、重连、导入/导出及记录隔离通过。
5. `python test_matlab_jobs.py`：任务协议/子进程夹具测试通过；没有执行MATLAB。
6. `python test_paired_capture.py`：真实P&O/INC各80,001点完整响应、来源一致性、缓存篡改拒绝、全记录导出与精确指标通过。
7. TypeScript `check`、生产 `build`；原有四组交互、传输、Blender集成、双记录同步测试通过。
8. 浏览器只读QA由实际GET响应构建：检查沉浸开场→总览→智能分析，未安装Ollama时禁用开始分析并显示实测计算证据；桌面与360像素布局可读。此静态QA不声称执行POST、WebSocket或AI推理。

## 验证限度

当前是Linux环境，不能执行Windows的CMD/PowerShell和x64 Python exe，没有真实Ollama/DeepSeek推理。本次验证了接入、数据与生命周期协议，不代表本机安装成功、模型准确性或推理速度已验收。尚需用户Windows首次解压启动、安装Ollama、下载模型和真实分析测试；实际两台Windows电脑局域网连通也受网络/防火墙环境影响。没有重新求解MATLAB，原R2025a完成证据沿用V23并保持字节不变。

持续分析间隔是上次结束后至少30个墙钟秒，不是硬实时控制；实时数据来自独立Python简化模型，Simulink来自已完成离线记录。模型所引用证据ID经过核验，不代表已证实原因或设备故障。
