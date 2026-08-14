星澜TP 0.9.0 + TrollVNC静默版（仅 RootHide）
================================================

用途
----

这是星澜手机端唯一正式插件。一个 DEB 同时提供：

1. XLStream H.264 投屏、系统控制、键盘输入和文件传输。
2. TrollVNC 静默触控服务，仅供星澜电脑端通过 USB 的 5901 端口发送鼠标触控。

版本约束
--------

1. 只构建和发布 Dopamine RootHide 版本（iphoneos-arm64e）。
2. 软件包 ID 保持 com.jibeib.xlstream.safe，版本由 0.8.3 升到 0.9.0，可直接覆盖升级。
3. 新包提供并替换旧星澜包及独立 TrollVNC 包，不再分发普通越狱版。
4. 包内没有 dpkg 维护脚本；安装完成后执行一次 Respring，由 SpringBoard 和 launchd 启动服务。

触控结构
--------

1. XLStream 原有的逐点 IOHID 鼠标触控协议和手机端注入代码已经删除。
2. 鼠标 DOWN / MOVE / UP 只走 TrollVNC RFB 3.8 的 5901 端口。
3. XLStream 仍保留键盘、文字输入、Home、开屏、息屏、文件传输等非触摸控制功能。
4. TrollVNC 只打包静默服务端与 LaunchDaemon，不包含设置面板、控制中心模块和浏览器画面资源。

端口
----

- 5901：TrollVNC 鼠标触控。
- 6000：开屏/息屏兼容接口，电脑端按需连接，用完立即断开。
- 6202：XLStream H.264 视频。
- 6203：二进制系统控制、键盘与关键帧请求。
- 6204：状态与心跳。
- 6205：USB 文件传输。

开源说明
--------

TrollVNC 源码： https://github.com/pengliang3676-afk/TrollVNC
固定提交：57af31e1eb204251d21d681503fa3f0765a8fd96
许可证：GPL-2.0。整合 DEB 在 XLStream.app 内附带 TrollVNC_GPLv2_LICENSE.txt。
