# 第十一步 · V21 本机 MATLAB 批处理任务

## 交付行为

从 /matlab 选择白名单工况和算法，服务复制原模型/参数/输入，顺序启动本机 MATLAB。新任务未完成前只显示状态和进程日志；完成后核对回执、目录边界、原文件指纹、算法、工况及严格CSV规范，通过后进入现有记录仓库和 /integration?record=... 回放。原来的Python实时模型、双记录离线对照继续独立存在。

本版包含用户提供的原始SLX、参数、输入与适配器；SOURCE_FILES.json保护原文件，RELEASE_SHA256.txt保护整个交付。不覆盖原模型，不向原工程save_system。wg_run_for_web增加可选outputRoot，wg_task_entry是固定批处理入口。

## 实现与约束

matlab_jobs.py负责单工作线程、同运行目录跨进程锁、持久化状态、32位请求键幂等、排队取消、20分钟超时、仅终止自己启动的进程组、重启转中断和证据导出。subprocess shell=False，执行参数为固定函数，不将网页输入拼成MATLAB代码。Windows加入-wait以取得实际退出状态，-sd设置副本目录；不使用Python Engine绑定。

本机接口检查Host、客户端回环地址及同源Origin；JSON参数拒绝多余字段，工况与算法枚举限定。接口不是可对外开放的远程计算服务。MATLAB发现状态不认证许可证和工具箱。

## 本次通过的验证

- test_matlab_jobs.py：真实Python子进程夹具验证单线程顺序、跨实例文件锁、幂等提交及参数冲突、排队取消、成功保存与重复结果复用、完整证据哈希、重启读取、异常恢复、源文件篡改拒绝、错误退出、仅采集状态、超时和关闭中断。
- 同一测试通过真实FastAPI路由提交夹具任务，验证Host/Origin拒绝、任意字段拒绝、结果记录读回80001点和导出；原实时状态不受影响。
- test_server_wire.py：真实TCP的任务页面、环境接口、历史读取、无MATLAB时创建返回503，原HTTP与两个WebSocket回归通过。
- test_paired_capture.py、test_integration.py、test_backend.py通过；前端交互、传输、接入与对照测试及TypeScript检查、生产构建通过。
- MISS_HIT静态解析wg_task_entry.m和修改后的wg_run_for_web.m通过；verify_package.py检查SLX XML真实接口、源文件及包文件哈希。
- 浏览器：只读API录制预览明确显示“Python测试进程，非MATLAB”；无MATLAB时提交按钮禁用；成功/失败任务切换、日志、结果链接通过。完成记录链接载入80001点，Blender120/120绑定显示。静态预览用显式index.html避开静态目录重定向丢失query；生产FastAPI直接返回/integration，不重定向。
- 360/768/1280容器实际内容宽度345/753/1265，scrollWidth分别等于clientWidth，无横向溢出；人工截图检查桌面与手机布局。

## 未完成的外部验证

没有MATLAB安装，未实际启动MATLAB批处理，未测Windows许可证/工具箱/编译/文件锁或进程树终止。测试夹具复用用户已上传的历史CSV，并明确标记，不能算新仿真。需用户按MATLAB_TASKS.md先运行test+沿用P&O，再运行INC，成功返回后确认兼容性。没有硬实时联合求解或3D反向控制SLX。

官方命令参考：https://www.mathworks.com/help/matlab/ref/matlabwindows.html
