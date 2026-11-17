V24 启动修复：pip 初始化

针对截图中的 “ERROR: To modify pip, please run the following command” 与 bootstrap.py 第48行失败。

修复包使用方法：
1. 关闭之前报错的启动终端。
2. 将本包内 bootstrap.py 复制到原网站文件夹，替换同名文件。该层应同时有 server.py 和 start_windows.bat。
3. 再双击原来的“启动网站.cmd”，保持窗口打开。

不用删除 .runtime、重新解压模型或重装系统Python。原先已解压的Python和依赖轮子可以继续使用。
如果已经使用本次更新的完整Windows包，其中已带修复，不必再覆盖补丁。

修复内容：pip首次引导采用模块入口并更新sys.argv，使其与模块方式一致；忽略引导轮子在临时sys.path上的已安装元数据，确保真正装入当前Python环境；引导后用 python -m pip --version 再检查。哈希校验、离线依赖、仿真、模型和业务代码保持原状。

验证：在无pip的真实隔离环境中，针对Windows入口保护规则复现原错误；修复后成功安装pip 26.2.2，执行 python -m pip、实际离线安装并导入colorama成功，重复引导、中文及空格路径检查通过。保护规则使用Windows条件夹具，安装过程运行在Linux；未在原生Windows执行，覆盖后仍需本机确认。
