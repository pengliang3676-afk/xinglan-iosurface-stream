TARGET = iphone:clang:latest:15.0
ARCHS = arm64 arm64e
THEOS_PACKAGE_SCHEME = roothide

include $(THEOS)/makefiles/common.mk

TOOL_NAME = XLStreamLauncher xlstreamd

XLStreamLauncher_FILES = XLLauncher.mm
XLStreamLauncher_CFLAGS = -fobjc-arc -Wall -Wextra -Wno-error
XLStreamLauncher_FRAMEWORKS = Foundation
XLStreamLauncher_CODESIGN_FLAGS = -SLauncherEntitlements.plist
XLStreamLauncher_INSTALL_PATH = /Applications/XLStream.app

xlstreamd_FILES = main.mm XLFrameSource.mm XLVideoServer.mm XLControlServer.mm XLStatusServer.mm XLHIDSender.mm
xlstreamd_CFLAGS = -fobjc-arc -std=c++17 -Wall -Wextra -Wno-error -Wno-deprecated-declarations
xlstreamd_FRAMEWORKS = Foundation UIKit IOSurface VideoToolbox CoreMedia CoreVideo QuartzCore IOKit Security
xlstreamd_CODESIGN_FLAGS = -SEntitlements.plist
xlstreamd_INSTALL_PATH = /Applications/XLStream.app/bin

include $(THEOS_MAKE_PATH)/tool.mk
