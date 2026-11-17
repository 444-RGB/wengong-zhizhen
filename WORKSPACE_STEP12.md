# V22 · 全站精简与已有成果集中展示

## 具体修改

新增home.html/home.ts：首页直接读取真实WG_refined_station.glb并核对SHA256，调用既有GLTFLoader加载120个视觉组件，支持预设视角与点选。失败时明确显示“几何示意”；软件渲染时标注简化预览。原沉浸开场移至/story，仍可访问。

product.css/product.ts统一首页、三维、MATLAB、对照、储能对照和研究工作台导航。技术说明改为原生details按需展开，错误与关键结果仍显示。保留所有旧DOM ID、操作绑定和API；没有删掉研究工具。研究工作台手机端保留五组底部导航，修复与旧版!important样式叠加导致的全屏遮挡。首页及场站全景在宽高比变化时重新适配视角。

三维页改为模型优先，导入/下载面板默认收起。新增严格校验的只读presets接口，默认加载已核对的80001点P&O，支持INC和早期6001点记录，保留本机runtime记录与导入。presets不写入用户运行数据库，导出CSV/JSON保持原始字节。

MATLAB页表单前置；环境路径、任务编号和日志收起，失败/中断/仅采集时展开日志。as_saved在任务标题中显示“P&O · 保存值”，避免下载时误选。没有改变任务执行、幂等、取消、超时或恢复逻辑。

新增只读/api/showcase和白名单/api/resources/{key}、/api/resources/package：提供两条真实完成任务证据、原生Blender、SLX、参数及可下载完整建模包。模型打包排除runs和__pycache__，附每个成员的SHA256；不接受任意文件路径。

## 验收依据

- 用户原始两条任务：mat_83e20abe1c4346e0805b32d37c42eeb9（P&O）、mat_001f9d79a95347cfa2f321340af5fd99（INC），均completed，R2025a。证据ZIP CRC/全部hashes通过，web CSV与5路raw逐点一致，并与当前算法对照预置CSV字节一致。没有重新制造仿真数据。
- test_showcase.py验证主路由、原生BLENDER/glTF签名、SLX字节、完整模型包ZIP CRC/全部SHA、实际证据包及任务ID、预置结果80001点/4s和导出字节、非法资源拒绝。
- test_matlab_jobs.py、test_server_wire.py、test_integration.py、test_paired_capture.py通过；新页面没有触发实时模型控制。MATLAB任务测试仍为明确标注的Python夹具，用户真实运行验证另由上面两包证明。
- TypeScript检查/生产构建、交互、传输、接入和对照前端测试通过；原实际Blender资产的GLTFLoader解析及120个唯一网格绑定通过。
- 浏览器采用实际GET响应的只读预览，无POST/WebSocket实现；不以静态预览证明实时操作。验证首页实际资产载入、视角按钮、双算法证据、P&O/INC结果切换、80001点、滑块End定位4秒及INC最终849.90V/59.62kW、导入面板和下载入口。
- 首页、三维、MATLAB、算法对照、研究页在360/768/1280容器检查：页面clientWidth为345/753/1265，对应scrollWidth相等；无横向溢出。人工截图检查桌面首页和手机首页/研究页。一次对照页截图超时，随后AX确认已完整载入；不将该次截图计为图像验收。

## 保持的范围

网页仍区分Python实时简化模型、MATLAB本机批处理及离线回放。Blender为已生成设计资产，无现场测绘/逐片电气求解/SOC信号。没有增加未经建模的电解槽、储氢或燃料电池求解，没有引入新的论文算法或宣称算法最优。

V22未在用户Windows重新完整运行新前端；已有MATLAB两路径是在V21后端上验证，V22执行器不改。新构建已验证接口和资产；新ZIP需用户解压启动后确认本机浏览器WebGL与MATLAB发现状态。
