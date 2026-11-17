# V9 验证结果

执行环境：Linux / Python 3.12；本包目标启动环境Windows / Python 3.11未原生执行。

- test_live.py：PASS。包含独立后台推进、网页WebSocket断开后继续、暂停冻结、倍速推进、参数热修改、CSV日志与运行重置隔离、API输入校验、重复启动生命周期。
- test_backend.py：PASS。原回放、导入、告警、SQLite、8760起报点与年度指标。
- test_physics.py：PASS。单二极管参考、阵列缩放、遮挡多峰和API。
- test_spatial.py：PASS。最近光线交点、BVH、点云噪声与几何遮挡。
- test_energy_storage.py：PASS。研究指标复现、MILP约束、不可行与缺依赖隔离。
- npm run check / npm run build：PASS。前端已打包，不要求用户安装Node。
- live_examples/cloud_pass_240s.csv：相同后端模型的240秒离线确定性复现；JSON附参数与守恒误差。不是墙钟实时性能测试，不是测量数据。

未完成浏览器视觉验收、原生Windows双击启动验收、MATLAB/Simulink软件联机。简化热惯性、逆变器响应与设备参数尚未标定。
