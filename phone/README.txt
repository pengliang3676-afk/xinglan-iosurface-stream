星澜新手机端 0.1.0

目标
====

这是独立实现的新视频链路，不包含鹰眼的代码、资源、协议或授权逻辑。
技术分层参考了对鹰眼二进制的静态架构观察，并使用iOS系统的IOSurface、
VTPixelTransferSession和VideoToolbox自行实现。

与旧手机插件的区别
==================

1. 不再创建CGImage截图，也不使用CPU的CoreGraphics缩放。
2. CARenderServer直接渲染到IOSurface。
3. VTPixelTransferSession把原始屏幕缩放到360x640的IOSurface像素缓冲。
4. 三缓冲槽位；编码器繁忙时直接丢弃新帧，不建立无限队列。
5. 手机到电脑采用新的XLV3分包协议，视频端口为6202。
6. socket发送最多阻塞300毫秒，电脑不读取时主动断开，避免拖死SpringBoard。
7. Windows正式测试客户端只连接6202，不再回退旧6002视频链路。

当前阶段
========

0.1.0只替换视频链路。触摸仍使用已验证的旧6000端口。
新视频单机验证通过后，再实现独立控制端口、心跳、关键帧请求和系统操作通道。

构建
====

需要Theos、iOS 15 SDK和arm64/arm64e工具链。当前Windows电脑没有本地clang/Theos
构建环境，使用项目的GitHub Actions构建流程生成rootless deb。
