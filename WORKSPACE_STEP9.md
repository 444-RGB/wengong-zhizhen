# 第九步：Simulink离线结果 × Blender场站

## 本版实际交付

| 内容 | 状态与范围 |
| --- | --- |
| Blender资产 | 已使用Blender核心bpy 4.3.0实际生成、重新打开原生文件并渲染检查 |
| 可编辑模型 | software_models/generated/WG_refined_station.blend |
| 网页资产 | static/integration/WG_refined_station.glb，另有generated目录副本 |
| 组件绑定 | 120个唯一网格PV_S1_M01至PV_S6_M20，含支架/底座/门缝/把手/散热口等细节 |
| Simulink导出器 | wg_export_simulink.m与映射例子；已提供，未在MATLAB中执行 |
| 离线后台 | CSV校验、独立记录保存、读取、哈希校验、完整导出；已测试 |
| 在线Simulink | 未接入；缺少用户原始SLX、初始化脚本及本机MATLAB执行环境 |

生成环境为隔离的Python3.11.17 / bpy4.3.0 / NumPy1.26.4。该环境不打入ZIP，也不会修改用户Python环境。模型生成脚本的指纹和资产指纹见static/integration/model_manifest.json。网页先核对GLB指纹，再解析并核对120个绑定编号；失败会明确提示，重载失败保留此前已加载资产。

## 启动与体验

1. 关闭旧服务，完整解压本版，双击start_windows.bat；不要仅复制server.py，也不要直接在压缩文件中运行。
2. 从终端输出的地址进入工作台。“实验与软件 ↗”包含“虚拟对照实验”和“Simulink × Blender”。新页面路径为/integration。
3. 默认加载内置P&O历史归档：6001个样本、0–6秒。这是既有用户归档的再展示，不是本版运行Simulink所得。
4. 拖动时间轴或播放回放，查看Pgrid、Qgrid、Vdc及G。点击组件仅检查全局辐照映射，点“查看逆变器信号”查看Vdc与VdcRef。
5. 使用“下载可编辑.blend”在Blender中继续建模；也可下载GLB。无需先安装Blender即可在网页查看资产。

## 从用户自己的Simulink结果导入

先在本机保存原始模型并完成仿真。对需要的信号启用logging，输出应为Simulink.SimulationOutput，logsout为Dataset，其元素Values为实数标量timeseries。五个信号的时间向量必须完全一致，从0开始且严格递增。此脚本不会修改模型、触发sim、调用模型回调或自动插值。

在MATLAB中将software_models加入路径。编辑wg_export_mapping_example.m中的source_name和source_unit，使其与实际logsout一致，再执行映射脚本得到map，最后执行：

```matlab
% out是你已经完成的本机仿真结果；map来自逐项核对后的映射。
files = wg_export_simulink(out, fullfile(pwd,'wg_exports'), map, 'WG_PV_Environment');
```

若logsout信号本来就使用标准列名和SI单位，可以省略map参数。映射例子中的G、Pgrid、Qgrid、vdc、vdc_ref仅为例子，不能假定你的模型恰好使用这些名字。模型名称为导出者声明，文件指纹仅验证一致性，不认证原始模型。

| CSV列（必须依此顺序） | 单位 | 含义 |
| --- | --- | --- |
| time_s | s | 共同保存时间 |
| G_Wm2 | W/m2 | 全局辐照 |
| Pgrid_W | W | 并网有功 |
| Qgrid_var | var | 并网无功 |
| Vdc_V | V | 直流电压 |
| VdcRef_V | V | 直流电压参考 |

导出器仅在显式声明kW或kvar时乘1000；后台接收SI列，不再次换算。不自动推断单位、补齐信号、删除重复时间、去重或重采样。没有Q信号时不能默认为0；请在原模型中正确记录。最多200000个样本、CSV UTF-8内容15MB。CSV为数值文本序列化，保留原MAT结果供需要完整原始精度的研究核查。

网页中填写名称，分别选择同一批导出的CSV与_source.json，点击“校验文件”，通过后“保存并载入”。更换文件或名称会使上次校验失效。重复相同内容拒绝再次导入。保存过程中网络超时，先刷新已保存记录确认，不要盲目重复提交。已有导入可从下拉框重新载入并导出ZIP；内容保存在runtime/simulink_imports独立目录，保存文件被改动后读取和导出返回冲突。

导出ZIP包含signals.csv原始字节、source.json、record.json和hashes.json。CSV来源可追溯到导出声明，但不代表独立验证了SLX、单位真实性或设备参数。

## 回放与建模边界

- 独立回放时钟，0.1×/0.25×/1×/2×速度；隐藏页面自动暂停。时间轴位置与保存样本时间分别显示。
- 读数使用不晚于游标的最近保存样本（前值保持），不插值。曲线仅为绘图抽绘最多约1200点，保存/导出和读数保留全部样本。
- G只映射全局辐照，全120片一致。没有逐片输入就没有分区云影、逐片功率、逐片故障判断。
- Vdc/VdcRef为逆变器全局信号，Pgrid/Qgrid为并网点信号。储能柜仅几何展示，没有从这6列推断SOC或储能控制。
- 场站是程序化设计资产，未现场测绘、点云重建或校准尺寸；不是工程施工模型。位置、数量、材质不改变原始电路或仿真输出。
- 这是离线结果回放与资产联动；不是Simulink在线求解、硬实时、实测数字孪生或控制器硬件在环。既有Python虚拟实验仍为独立模型。

## 验收证据与限制

- test_integration.py：严格CSV与来源清单、完整字节导出、10万零1点保留、BOM、重复/篡改拒绝、跨Bridge重开、实时任务隔离、GLB解析与原生blend指纹。
- test_server_wire.py：真实localhost TCP启动，HTTP校验/保存/读取/导出/GLB下载，旧WebSocket回放与实时求解继续通过。
- npm run check/build/test:integration/test:interactions/test:transport通过；实际GLTFLoader解析资产、120绑定、几何边界和不规则时间取样通过。
- 原生.blend重新打开并渲染成功；实际GLB在浏览器加载成功。无WebGL的云端浏览器使用软件渲染检查了完整12排支架；软件材质与阴影简化。
- 浏览器检查回放末端6001/6001点、慢速播放/暂停、设备信号、无文件提示和视角。360/768/1280宽度iframe无根横向溢出；实际内容视口345/753/1265像素（扣滚动条）。
- 浏览器验收用真实后台GET录制响应与真实GLB，预览不保存导入。导入保存的HTTP链路在Python真实TCP测试验证，并未声称浏览器到Python完整端到端测试。
- 未验证：MATLAB脚本实际执行与版本兼容、用户原始SLX重新仿真、Windows本机启动、原生GPU渲染、移动设备触控。不能把这些标记为已完成。

下一阶段需要用户原始.slx、初始化/参数脚本、MATLAB版本与依赖工具箱信息；先做本机批量仿真和统一信号验证，再考虑联机协同与速率边界。

## 官方接口参考

- https://www.mathworks.com/help/simulink/slref/simulink.simulationoutput.html
- https://www.mathworks.com/help/matlab/ref/writetable.html
- https://docs.blender.org/api/4.0/bpy.ops.export_scene.html
